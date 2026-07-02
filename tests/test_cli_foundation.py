from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from toolplane.cli import main
from toolplane.config import load_toolplane_config


def test_cli_init_writes_safe_default_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"

    code = main(["init", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == f"Wrote {config_path}\n"
    config = load_toolplane_config(config_path)
    assert config.toolplane.default_backend == "monty"
    assert config.cli.mode == "disabled"
    assert config.mcp.servers == {}


def test_cli_init_refuses_existing_config_without_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("# precious\n", encoding="utf-8")

    code = main(["init", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "use --force to overwrite" in captured.err
    assert config_path.read_text(encoding="utf-8") == "# precious\n"

    assert main(["init", "--config", str(config_path), "--force"]) == 0
    assert config_path.read_text(encoding="utf-8") != "# precious\n"


def test_cli_config_check_validates_and_summarizes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [cli]
            mode = "allowlist"
            allow = ["git", "rg"]

            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["config", "check", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        f"config: {config_path}\n"
        "backend: monty\n"
        "cli: allowlist [git, rg]\n"
        "mcp servers: linear\n"
        "ok\n"
    )


def test_cli_config_check_notes_unsafe_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        '[toolplane]\ndefault_backend = "local_unsafe"\n',
        encoding="utf-8",
    )

    code = main(["config", "check", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert "note: serve mcp will require --unsafe" in captured.out


def test_cli_config_check_rejects_invalid_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text('[cli]\nmode = "sideways"\n', encoding="utf-8")

    code = main(["config", "check", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "toolplane:" in captured.err


def test_cli_doctor_reports_missing_allowlisted_binary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [cli]
            mode = "allowlist"
            allow = ["definitely-not-a-binary-xyz"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["doctor", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 1
    assert (
        "cli allow definitely-not-a-binary-xyz: fail (not found on PATH)"
        in captured.out
    )


def test_cli_doctor_warnings_do_not_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [toolplane]
            default_backend = "local_unsafe"

            [cli]
            mode = "ambient"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["doctor", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert "backend local_unsafe: warn" in captured.out
    assert "cli ambient: warn" in captured.out


def test_cli_doctor_fails_on_unknown_backend(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text('[toolplane]\ndefault_backend = "nope"\n', encoding="utf-8")

    code = main(["doctor", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 1
    assert "backend nope: fail" in captured.out


def test_cli_mcp_list_prints_remote_and_stdio_servers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            auth = "oauth"

            [mcp.servers.math]
            command = "uvx"
            args = ["some-server", "--flag"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "list", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "MCP servers:\n"
        "- linear: transport=url url=https://mcp.linear.app/mcp auth=oauth "
        "warning=direct OAuth tokens are ephemeral in Toolplane v1; "
        "use a fastmcp-remote bridge for persistent login\n"
        "- math: transport=stdio command=uvx args=2 auth=none\n"
    )


def test_cli_run_executes_script_against_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("", encoding="utf-8")
    script_path = tmp_path / "snippet.py"
    script_path.write_text(
        'print("from snippet")\nreturn {"answer": 6 * 7}\n',
        encoding="utf-8",
    )

    code = main(["run", str(script_path), "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == 'from snippet\n{\n  "answer": 42\n}\n'


def test_cli_run_exits_nonzero_on_execution_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("", encoding="utf-8")
    script_path = tmp_path / "snippet.py"
    script_path.write_text('raise ValueError("bad snippet")\n', encoding="utf-8")

    code = main(["run", str(script_path), "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 1
    assert "toolplane: ValueError: bad snippet" in captured.err


def test_cli_serve_mcp_reports_malformed_config_cleanly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("[mcp\n", encoding="utf-8")

    code = main(["serve", "mcp", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert captured.err.startswith("toolplane:")


def test_cli_run_rejects_missing_script(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["run", str(tmp_path / "missing.py"), "--config", "unused.toml"])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "toolplane:" in captured.err
