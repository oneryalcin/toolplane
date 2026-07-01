from __future__ import annotations

from pathlib import Path

import pytest

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


def assert_emitted_config_loads(tmp_path: Path, output: str) -> ToolplaneConfig:
    config_path = tmp_path / "emitted.toml"
    config_path.write_text(output, encoding="utf-8")
    config = load_toolplane_config(config_path)
    MCPConfig.from_dict(config.mcp.to_fastmcp_config())
    return config
