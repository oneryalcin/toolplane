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


def test_mcp_add_url_emits_round_trippable_toml(
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
    )
    assert existing_config.read_text(encoding="utf-8") == "# existing config\n"
    assert_emitted_config_loads(tmp_path, captured.out)


def test_mcp_add_command_emits_round_trippable_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        [
            "mcp",
            "add",
            "linear-bridge",
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


def test_mcp_add_emits_astral_characters_as_valid_toml(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url = "https://mcp.example/mcp?name=café😀"

    code = main(["mcp", "add", "unicode", "--url", url])

    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert "café😀" in captured.out
    assert "\\ud" not in captured.out
    config = assert_emitted_config_loads(tmp_path, captured.out)
    assert config.mcp.servers["unicode"]["url"] == url


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


def assert_emitted_config_loads(tmp_path: Path, output: str) -> ToolplaneConfig:
    config_path = tmp_path / "emitted.toml"
    config_path.write_text(output, encoding="utf-8")
    config = load_toolplane_config(config_path)
    MCPConfig.from_dict(config.mcp.to_fastmcp_config())
    return config
