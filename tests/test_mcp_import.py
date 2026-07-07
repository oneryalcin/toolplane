"""Contract tests for `toolplane mcp import` (#97).

The bugs these prevent: plaintext secrets landing in TOML, dry-run writing,
scope-precedence inversions, silent key drops, and conflict clobbering.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import keyring
import pytest

from toolplane.cli import main
from toolplane.config import load_toolplane_config
from toolplane.mcp_import import (
    McpImportError,
    format_import_report,
    import_mcp_servers,
)


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def _write_claude_config(home: Path, payload: dict) -> None:
    (home / ".claude.json").write_text(json.dumps(payload), encoding="utf-8")


def _import(
    config_path: Path,
    source: str,
    home: Path,
    project_dir: Path,
    **kwargs,
):
    kwargs.setdefault("environ", {})
    return import_mcp_servers(
        config_path, source, home=home, project_dir=project_dir, **kwargs
    )


def test_claude_stdio_and_url_servers_import(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "chrome": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["chrome-devtools-mcp@latest"],
                    "env": {},
                },
                "docs": {"type": "http", "url": "https://mcp.example.com/mcp"},
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    report = _import(config_path, "claude", fake_home, project_dir)

    assert [p.name for p in report.imported] == ["chrome", "docs"]
    config = load_toolplane_config(config_path)
    assert config.mcp.servers["chrome"] == {
        "command": "npx",
        "args": ["chrome-devtools-mcp@latest"],
    }
    assert config.mcp.servers["docs"] == {"url": "https://mcp.example.com/mcp"}
    # a bare url import carries no auth signal; the report must route the
    # user to status (which detects auth_required) instead of guessing
    docs = report.imported[1]
    assert any("mcp status docs" in step for step in docs.next_steps)


def test_project_scope_shadows_user_scope(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {"dup": {"command": "user-scope-cmd"}},
            "projects": {
                str(project_dir): {
                    "mcpServers": {"dup": {"command": "project-scope-cmd"}}
                }
            },
        },
    )
    config_path = tmp_path / "toolplane.toml"

    report = _import(config_path, "claude", fake_home, project_dir)

    config = load_toolplane_config(config_path)
    assert config.mcp.servers["dup"]["command"] == "project-scope-cmd"
    assert report.skipped[0].name == "dup"
    assert "shadowed" in report.skipped[0].reason


def test_project_mcp_json_is_read(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    (project_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"local": {"command": "uvx", "args": ["srv"]}}}),
        encoding="utf-8",
    )
    config_path = tmp_path / "toolplane.toml"

    report = _import(config_path, "claude", fake_home, project_dir)

    assert [p.name for p in report.imported] == ["local"]


def test_codex_import_drops_unknown_keys_with_note(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        textwrap.dedent(
            """
            [mcp_servers.node_repl]
            command = "/bin/node_repl"
            startup_timeout_sec = 120
            """
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "toolplane.toml"

    report = _import(config_path, "codex", fake_home, project_dir)

    planned = report.imported[0]
    assert planned.config == {"command": "/bin/node_repl"}
    assert any("startup_timeout_sec" in note for note in planned.notes)


def test_secret_env_value_becomes_keyring_ref_and_is_stored(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    token = "abcd1234efgh5678ijkl9000"
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": token}}
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    _import(config_path, "claude", fake_home, project_dir)

    written = config_path.read_text(encoding="utf-8")
    assert token not in written
    assert "keyring://gh-github_token" in written
    assert keyring.get_password("toolplane", "gh-github_token") == token


def test_secret_matching_environment_becomes_env_ref(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    token = "abcd1234efgh5678ijkl9000"
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": token}}
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    _import(
        config_path,
        "claude",
        fake_home,
        project_dir,
        environ={"GITHUB_TOKEN": token},
    )

    written = config_path.read_text(encoding="utf-8")
    assert token not in written
    assert "env://GITHUB_TOKEN" in written


def test_authorization_header_is_always_treated_as_secret(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "api": {
                    "type": "http",
                    "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer shh"},
                }
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    _import(config_path, "claude", fake_home, project_dir)

    written = config_path.read_text(encoding="utf-8")
    assert "Bearer shh" not in written
    assert "keyring://api-authorization" in written


def test_plaintext_flag_copies_literals(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": "shh"}}
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    _import(config_path, "claude", fake_home, project_dir, plaintext=True)

    assert 'GITHUB_TOKEN = "shh"' in config_path.read_text(encoding="utf-8")


def test_remote_bridge_wrapper_rewritten_to_direct_oauth_url(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "linear": {
                    "command": "npx",
                    "args": ["-y", "mcp-remote", "https://mcp.linear.app/sse"],
                }
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    report = _import(config_path, "claude", fake_home, project_dir)

    config = load_toolplane_config(config_path)
    assert config.mcp.servers["linear"] == {
        "url": "https://mcp.linear.app/sse",
        "auth": "oauth",
    }
    planned = report.imported[0]
    assert any("mcp-remote wrapper" in note for note in planned.notes)
    assert any("mcp login linear" in step for step in planned.next_steps)


def test_verbatim_keeps_the_wrapper(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "linear": {
                    "command": "npx",
                    "args": ["-y", "mcp-remote", "https://mcp.linear.app/sse"],
                }
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    _import(config_path, "claude", fake_home, project_dir, verbatim=True)

    config = load_toolplane_config(config_path)
    assert config.mcp.servers["linear"]["command"] == "npx"
    assert "url" not in config.mcp.servers["linear"]


def test_dry_run_writes_nothing_and_stores_no_secrets(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "gh": {
                    "command": "gh-mcp",
                    "env": {"GITHUB_TOKEN": "abcd1234efgh5678ijkl9000"},
                }
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("# precious\n", encoding="utf-8")

    report = _import(config_path, "claude", fake_home, project_dir, dry_run=True)

    assert config_path.read_text(encoding="utf-8") == "# precious\n"
    assert keyring.get_password("toolplane", "gh-github_token") is None
    rendered = format_import_report(report)
    assert "would import gh" in rendered
    assert "would store secret 'gh-github_token'" in rendered


def test_existing_server_skipped_without_force(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home, {"mcpServers": {"linear": {"url": "https://new.example/mcp"}}}
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        "# keep me\n[mcp.servers.linear]\nurl = \"https://old.example/mcp\"\n",
        encoding="utf-8",
    )

    report = _import(config_path, "claude", fake_home, project_dir)
    assert report.skipped[0].reason == "already configured (--force to replace)"
    assert "old.example" in config_path.read_text(encoding="utf-8")

    _import(config_path, "claude", fake_home, project_dir, force=True)
    written = config_path.read_text(encoding="utf-8")
    assert "new.example" in written
    assert written.startswith("# keep me\n")


def test_invalid_names_are_sanitized_or_skipped(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "chrome devtools": {"command": "npx"},
                "???": {"command": "npx"},
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    report = _import(config_path, "claude", fake_home, project_dir)

    config = load_toolplane_config(config_path)
    assert list(config.mcp.servers) == ["chrome-devtools"]
    assert any("renamed" in note for note in report.imported[0].notes)
    assert report.skipped[0].name == "???"


def test_missing_source_config_is_a_clean_error(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    with pytest.raises(McpImportError, match="no Claude Code config found"):
        _import(tmp_path / "toolplane.toml", "claude", fake_home, project_dir)
    with pytest.raises(McpImportError, match="no Codex config found"):
        _import(tmp_path / "toolplane.toml", "codex", fake_home, project_dir)


def test_entry_without_command_or_url_is_skipped_not_fatal(
    tmp_path: Path, fake_home: Path, project_dir: Path
) -> None:
    _write_claude_config(
        fake_home,
        {
            "mcpServers": {
                "broken": {"type": "stdio"},
                "fine": {"command": "npx"},
            }
        },
    )
    config_path = tmp_path / "toolplane.toml"

    report = _import(config_path, "claude", fake_home, project_dir)

    assert [p.name for p in report.imported] == ["fine"]
    assert report.skipped[0].name == "broken"
    assert "neither a command nor a url" in report.skipped[0].reason


def test_cli_import_command_end_to_end(
    tmp_path: Path,
    fake_home: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(project_dir)
    _write_claude_config(
        fake_home, {"mcpServers": {"docs": {"url": "https://mcp.example.com/mcp"}}}
    )
    config_path = tmp_path / "toolplane.toml"

    code = main(["mcp", "import", "--from", "claude", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert "imported docs" in captured.out
    assert "summary: imported 1, skipped 0" in captured.out
    assert load_toolplane_config(config_path).mcp.servers["docs"] == {
        "url": "https://mcp.example.com/mcp"
    }


def test_cli_import_missing_source_exits_2(
    tmp_path: Path,
    fake_home: Path,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(project_dir)

    code = main(
        ["mcp", "import", "--from", "codex", "--config", str(tmp_path / "t.toml")]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "no Codex config found" in captured.err
