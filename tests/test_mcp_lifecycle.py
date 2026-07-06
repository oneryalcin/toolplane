from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest

import toolplane.mcp_lifecycle as lifecycle
from toolplane.cli import main
from toolplane.config import ToolplaneConfig, load_toolplane_config

pytest.importorskip("fastmcp")
from fastmcp.mcp_config import MCPConfig  # noqa: E402


def test_mcp_add_url_writes_config_and_preserves_existing_comments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        '# existing config\n\n[cli]\nmode = "disabled"\n',
        encoding="utf-8",
    )

    code = main(
        [
            "mcp",
            "add",
            "linear",
            "--config",
            str(config_path),
            "--url",
            "https://mcp.linear.app/mcp",
            "--auth",
            "oauth",
        ]
    )

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == f"Added MCP server 'linear' to {config_path}\n"
    written = config_path.read_text(encoding="utf-8")
    assert written == (
        "# existing config\n"
        "\n"
        "[cli]\n"
        'mode = "disabled"\n'
        "\n"
        "[mcp.servers.linear]\n"
        'url = "https://mcp.linear.app/mcp"\n'
        'auth = "oauth"\n'
        "# tokens are stored encrypted at rest (key in your OS keyring);\n"
        "# prime once with: toolplane mcp login linear\n"
    )
    config = load_toolplane_config(config_path)
    MCPConfig.from_dict(config.mcp.to_fastmcp_config())


def test_mcp_add_print_url_emits_round_trippable_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing_config = tmp_path / "toolplane.toml"
    existing_config.write_text("# existing config\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    code = main(
        [
            "mcp",
            "add",
            "linear",
            "--print",
            "--url",
            "https://mcp.linear.app/mcp",
            "--auth",
            "oauth",
        ]
    )

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "# add this to your toolplane.toml:\n"
        "[mcp.servers.linear]\n"
        'url = "https://mcp.linear.app/mcp"\n'
        'auth = "oauth"\n'
        "# tokens are stored encrypted at rest (key in your OS keyring);\n"
        "# prime once with: toolplane mcp login linear\n"
    )
    assert existing_config.read_text(encoding="utf-8") == "# existing config\n"
    assert_emitted_config_loads(tmp_path, captured.out)


def test_mcp_add_print_command_emits_round_trippable_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "mcp",
            "add",
            "linear-bridge",
            "--print",
            "--command",
            "npx",
            "--arg",
            "-y",
            "--arg",
            "mcp-remote",
            "--arg",
            'https://mcp.linear.app/mcp?name="Linear"',
        ]
    )

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "# add this to your toolplane.toml:\n"
        "[mcp.servers.linear-bridge]\n"
        'command = "npx"\n'
        'args = ["-y", "mcp-remote", '
        '"https://mcp.linear.app/mcp?name=\\"Linear\\""]\n'
    )
    config = assert_emitted_config_loads(tmp_path, captured.out)
    assert config.mcp.servers["linear-bridge"]["args"] == [
        "-y",
        "mcp-remote",
        'https://mcp.linear.app/mcp?name="Linear"',
    ]


def test_mcp_add_creates_missing_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"

    code = main(
        [
            "mcp",
            "add",
            "context7",
            "--config",
            str(config_path),
            "--url",
            "https://mcp.context7.com/mcp",
        ]
    )

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == f"Added MCP server 'context7' to {config_path}\n"
    config = load_toolplane_config(config_path)
    assert config.mcp.servers["context7"] == {
        "url": "https://mcp.context7.com/mcp",
    }


def test_mcp_add_fastmcp_remote_emits_prime_guidance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "mcp",
            "add",
            "linear-bridge",
            "--print",
            "--command",
            "uvx",
            "--arg",
            "fastmcp-remote",
            "--arg",
            "https://mcp.linear.app/mcp",
            "--arg",
            "--resource",
            "--arg",
            "linear-prod",
        ]
    )

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "# add this to your toolplane.toml:\n"
        "[mcp.servers.linear-bridge]\n"
        'command = "uvx"\n'
        'args = ["fastmcp-remote", "https://mcp.linear.app/mcp", '
        '"--resource", "linear-prod"]\n'
        "# prime this bridge before relying on status or execute:\n"
        "# toolplane mcp login linear-bridge\n"
    )
    assert_emitted_config_loads(tmp_path, captured.out)


def test_mcp_add_emits_astral_characters_as_valid_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url = "https://mcp.example/mcp?name=café😀"

    code = main(["mcp", "add", "unicode", "--print", "--url", url])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert "café😀" in captured.out
    assert "\\ud" not in captured.out
    config = assert_emitted_config_loads(tmp_path, captured.out)
    assert config.mcp.servers["unicode"]["url"] == url


def test_mcp_add_rejects_existing_server_without_force(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://old.example/mcp"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(
        [
            "mcp",
            "add",
            "linear",
            "--config",
            str(config_path),
            "--url",
            "https://mcp.linear.app/mcp",
        ]
    )

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "already exists; use --force" in captured.err
    assert 'url = "https://old.example/mcp"' in config_path.read_text(
        encoding="utf-8"
    )


def test_mcp_add_force_replaces_existing_server(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://old.example/mcp"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(
        [
            "mcp",
            "add",
            "linear",
            "--config",
            str(config_path),
            "--force",
            "--command",
            "uvx",
            "--arg",
            "fastmcp-remote",
            "--arg",
            "https://mcp.linear.app/mcp",
        ]
    )

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == f"Added MCP server 'linear' to {config_path}\n"
    config = load_toolplane_config(config_path)
    assert config.mcp.servers["linear"] == {
        "command": "uvx",
        "args": ["fastmcp-remote", "https://mcp.linear.app/mcp"],
    }


def test_mcp_add_preserves_original_config_when_atomic_replace_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "toolplane.toml"
    original = '# keep me\n\n[mcp.servers.old]\nurl = "https://old.example/mcp"\n'
    config_path.write_text(original, encoding="utf-8")

    def fail_replace(src: str, dst: str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(lifecycle.os, "replace", fail_replace)

    code = main(
        [
            "mcp",
            "add",
            "linear",
            "--config",
            str(config_path),
            "--url",
            "https://mcp.linear.app/mcp",
        ]
    )

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "simulated replace failure" in captured.err
    assert config_path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".toolplane.toml.*.tmp")) == []


@pytest.mark.parametrize("name", ["linear.prod", "linear/prod", "linear prod", ""])
def test_mcp_add_rejects_invalid_names(
    name: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["mcp", "add", name, "--url", "https://mcp.linear.app/mcp"])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "MCP server name" in captured.err


def test_mcp_add_rejects_auth_for_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["mcp", "add", "linear", "--command", "npx", "--auth", "oauth"])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "--auth is only valid with --url" in captured.err


def test_mcp_add_rejects_args_for_url(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "mcp",
            "add",
            "linear",
            "--url",
            "https://mcp.linear.app/mcp",
            "--arg",
            "unused",
        ]
    )

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "--arg is only valid with --command" in captured.err


def test_mcp_status_reports_empty_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("", encoding="utf-8")

    code = main(["mcp", "status", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == "MCP servers:\n(none)\n"


def test_mcp_status_checks_stdio_server(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    server_path = tmp_path / "server.py"
    server_path.write_text(
        textwrap.dedent(
            """
            from fastmcp import FastMCP

            mcp = FastMCP("Status Demo")

            @mcp.tool
            def ping() -> str:
                return "pong"

            if __name__ == "__main__":
                mcp.run(show_banner=False)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            [mcp.servers.docs]
            command = {json.dumps(sys.executable)}
            args = [{json.dumps(str(server_path))}]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "status", "--config", str(config_path), "--timeout", "10"])

    captured = capfd.readouterr()

    assert code == 0
    assert captured.out == (
        "MCP servers:\n"
        "- docs: ok transport=stdio auth=none tools=1\n"
    )


def test_mcp_status_uses_no_auth_probe(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_probe: dict[str, object] = {}

    async def fake_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        captured_probe["name"] = name
        captured_probe["server_config"] = server_config
        captured_probe["timeout_seconds"] = timeout_seconds
        return [object(), object()]

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", fake_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            auth = "oauth"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "status", "linear", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "MCP servers:\n"
        "- linear: ok transport=url auth=oauth tools=2\n"
    )
    assert captured_probe["name"] == "linear"
    assert captured_probe["timeout_seconds"] == 5.0
    assert captured_probe["server_config"] == {
        "url": "https://mcp.linear.app/mcp",
    }


def test_mcp_status_stdio_probe_neutralizes_browser_and_preserves_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_probe: dict[str, object] = {}

    async def fake_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        captured_probe["server_config"] = server_config
        return []

    monkeypatch.setenv("PATH", "/toolplane/test/path")
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", "/home/default-fastmcp")
    monkeypatch.setattr(lifecycle, "_list_mcp_tools", fake_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear_bridge]
            command = "uvx"
            args = ["fastmcp-remote", "https://mcp.linear.app/mcp"]

            [mcp.servers.linear_bridge.env]
            FASTMCP_REMOTE_CONFIG_DIR = "/project/fastmcp"
            BROWSER = "/usr/bin/open"
            LINEAR_REGION = "eu"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "status", "--config", str(config_path)])

    captured = capsys.readouterr()
    server_config = captured_probe["server_config"]
    assert isinstance(server_config, dict)
    env = server_config["env"]
    assert isinstance(env, dict)

    assert code == 0
    assert captured.err == ""
    assert env["PATH"] == "/toolplane/test/path"
    assert env["FASTMCP_REMOTE_CONFIG_DIR"] == "/project/fastmcp"
    assert env["LINEAR_REGION"] == "eu"
    assert env["BROWSER"] == lifecycle._disabled_browser_command()
    assert server_config["keep_alive"] is False


def test_mcp_status_reports_timeout_as_data(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", slow_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.slow]
            url = "https://mcp.example/slow"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(
        ["mcp", "status", "--config", str(config_path), "--timeout", "0.01"]
    )

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "MCP servers:\n"
        "- slow: timeout transport=url auth=none detail=timed out after 0.01s\n"
    )


def test_mcp_status_reports_auth_required_as_data(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unauthorized_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", unauthorized_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "status", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "MCP servers:\n"
        "- linear: auth_required transport=url auth=none detail=401 Unauthorized\n"
    )


def test_mcp_status_hints_login_for_direct_oauth_when_auth_required(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unauthorized_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", unauthorized_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            auth = "oauth"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "status", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert captured.out == (
        "MCP servers:\n"
        "- linear: auth_required transport=url auth=oauth detail=401 "
        "Unauthorized — prime it once with: toolplane mcp login linear\n"
    )


def test_mcp_status_tells_primed_direct_oauth_apart_from_unprimed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # after login the status probe still gets 401 (it never attaches
    # credentials) — the detail must say the saved login exists instead
    # of re-teaching the login command
    async def unauthorized_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        raise RuntimeError("401 Unauthorized")

    async def tokens_exist(url: str) -> bool:
        return True

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", unauthorized_list_mcp_tools)
    monkeypatch.setattr(
        "toolplane.credentials.has_stored_oauth_tokens", tokens_exist
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            auth = "oauth"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "status", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert "tokens are stored" in captured.out
    assert "mcp login" not in captured.out


def test_mcp_status_rejects_unknown_server(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "status", "missing", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "Unknown MCP server: missing" in captured.err


def test_mcp_status_rejects_bad_timeout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("", encoding="utf-8")

    code = main(["mcp", "status", "--config", str(config_path), "--timeout", "0"])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "--timeout must be greater than zero" in captured.err


def test_mcp_status_rejects_malformed_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("[mcp\n", encoding="utf-8")

    code = main(["mcp", "status", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "toolplane:" in captured.err


def test_mcp_login_primes_stdio_server(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    server_path = tmp_path / "server.py"
    server_path.write_text(
        textwrap.dedent(
            """
            from fastmcp import FastMCP

            mcp = FastMCP("Login Demo")

            @mcp.tool
            def ping() -> str:
                return "pong"

            if __name__ == "__main__":
                mcp.run(show_banner=False)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            [mcp.servers.docs]
            command = {json.dumps(sys.executable)}
            args = [{json.dumps(str(server_path))}]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "login", "docs", "--config", str(config_path)])

    captured = capfd.readouterr()

    assert code == 0
    assert captured.out == "Login succeeded for 'docs': 1 tools\n"
    assert "browser window may open" in captured.err


def test_mcp_login_keeps_browser_enabled_and_preserves_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_probe: dict[str, object] = {}

    async def fake_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        captured_probe["server_config"] = server_config
        captured_probe["timeout_seconds"] = timeout_seconds
        return [object()]

    monkeypatch.setenv("BROWSER", "/usr/bin/open")
    monkeypatch.setenv("FASTMCP_REMOTE_CONFIG_DIR", "/home/default-fastmcp")
    monkeypatch.setattr(lifecycle, "_list_mcp_tools", fake_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear_bridge]
            command = "uvx"
            args = ["fastmcp-remote", "https://mcp.linear.app/mcp"]

            [mcp.servers.linear_bridge.env]
            FASTMCP_REMOTE_CONFIG_DIR = "/project/fastmcp"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "login", "linear_bridge", "--config", str(config_path)])

    server_config = captured_probe["server_config"]
    assert isinstance(server_config, dict)
    env = server_config["env"]
    assert isinstance(env, dict)

    assert code == 0
    assert env["BROWSER"] == "/usr/bin/open"
    assert env["FASTMCP_REMOTE_CONFIG_DIR"] == "/project/fastmcp"
    assert server_config["keep_alive"] is False
    assert captured_probe["timeout_seconds"] == 180.0


def test_mcp_login_wires_direct_oauth_to_encrypted_storage(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_probe: dict[str, object] = {}

    async def fake_list_mcp_tools(
        name: str,
        server_config: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> list[object]:
        captured_probe["server_config"] = server_config
        return [object(), object()]

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", fake_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            auth = "oauth"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "login", "linear", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 0
    assert "Login succeeded" in captured.out
    from fastmcp.client.auth import OAuth

    probe_config = captured_probe["server_config"]
    assert isinstance(probe_config["auth"], OAuth)
    assert probe_config["auth"].context.storage is not None


def test_mcp_login_rejects_unknown_server(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text("", encoding="utf-8")

    code = main(["mcp", "login", "missing", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "Unknown MCP server: missing" in captured.err


def test_mcp_login_reports_auth_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unauthorized_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", unauthorized_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.linear_bridge]
            command = "uvx"
            args = ["fastmcp-remote", "https://mcp.linear.app/mcp"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(["mcp", "login", "linear_bridge", "--config", str(config_path)])

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "Login failed for 'linear_bridge': 401 Unauthorized" in captured.err


def test_mcp_login_reports_timeout_with_retry_hint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_list_mcp_tools(
        name: str,
        server_config: object,
        *,
        timeout_seconds: float,
    ) -> list[object]:
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(lifecycle, "_list_mcp_tools", slow_list_mcp_tools)
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [mcp.servers.slow]
            command = "uvx"
            args = ["fastmcp-remote", "https://mcp.example/slow"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    code = main(
        ["mcp", "login", "slow", "--config", str(config_path), "--timeout", "0.01"]
    )

    captured = capsys.readouterr()

    assert code == 2
    assert captured.out == ""
    assert "Login timed out for 'slow' after 0.01s" in captured.err
    assert "--timeout" in captured.err


def assert_emitted_config_loads(tmp_path: Path, output: str) -> ToolplaneConfig:
    config_path = tmp_path / "emitted.toml"
    config_path.write_text(output, encoding="utf-8")
    config = load_toolplane_config(config_path)
    MCPConfig.from_dict(config.mcp.to_fastmcp_config())
    return config
