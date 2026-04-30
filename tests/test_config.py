from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from toolplane import CapabilityRegistry, Toolplane
from toolplane.adapters import mcp as mcp_adapter
from toolplane.config import ToolplaneConfig, load_toolplane_config

pytest.importorskip("fastmcp")
from fastmcp.mcp_config import MCPConfig  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_load_toolplane_config_uses_native_mcp_servers_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [toolplane]
            default_backend = "pyodide-deno"

            [cli]
            mode = "allowlist"
            allow = ["git", "docker-compose"]

            [mcp.servers.linear]
            url = "https://mcp.linear.app/mcp"
            auth = "oauth"

            [mcp.servers.context7.headers]
            Authorization = "Bearer ${CONTEXT7_TOKEN}"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_toolplane_config(config_path)

    assert config.toolplane.default_backend == "pyodide-deno"
    assert config.cli.allow == ("git", "docker-compose")
    assert config.mcp.to_fastmcp_config() == {
        "mcpServers": {
            "linear": {
                "url": "https://mcp.linear.app/mcp",
                "auth": "oauth",
            },
            "context7": {
                "headers": {
                    "Authorization": "Bearer ${CONTEXT7_TOKEN}",
                },
            },
        }
    }


@pytest.mark.parametrize(
    "config",
    [
        {"cli": {"mode": "allowlist"}},
        {"cli": {"mode": "ambient", "allow": ["git"]}},
        {"cli": {"mode": "disabled", "allow": ["git"]}},
        {"cli": {"mode": "allowlist", "allow": ["git", "git"]}},
        {"cli": {"mode": "allowlist", "allow": [""]}},
    ],
)
def test_toolplane_config_rejects_invalid_cli_policy(
    config: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ToolplaneConfig.model_validate(config)


def test_from_config_loads_default_backend_and_disables_cli() -> None:
    runtime = run(
        Toolplane.from_config(
            {
                "toolplane": {"default_backend": "pyodide-deno"},
                "cli": {"mode": "disabled"},
            }
        )
    )

    assert runtime.default_backend == "pyodide-deno"

    result = run(runtime.execute("return cli", backend="local_unsafe"))

    assert not result.ok
    assert result.error is not None
    assert result.error.type == "NameError"


def test_from_config_cli_allowlist_blocks_unlisted_binary() -> None:
    runtime = run(
        Toolplane.from_config(
            {
                "cli": {
                    "mode": "allowlist",
                    "allow": ["git"],
                }
            }
        )
    )

    via_call = run(
        runtime.execute(
            """
return await cli("curl").version()
"""
        )
    )
    via_attribute = run(
        runtime.execute(
            """
return await cli.curl.version()
"""
        )
    )
    via_top_level = run(runtime.execute("return curl"))

    assert via_call.error is not None
    assert via_call.error.type == "CliPolicyError"
    assert via_attribute.error is not None
    assert via_attribute.error.type == "CliPolicyError"
    assert via_top_level.error is not None
    assert via_top_level.error.type == "NameError"


def test_from_config_preserves_remote_mcp_auth_shape_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_register_mcp_server(
        registry: CapabilityRegistry,
        name: str,
        server: object,
        **_: object,
    ) -> list[object]:
        captured[name] = server
        return []

    monkeypatch.setattr(
        mcp_adapter,
        "register_mcp_server",
        fake_register_mcp_server,
    )

    run(
        Toolplane.from_config(
            {
                "cli": {"mode": "disabled"},
                "mcp": {
                    "servers": {
                        "linear": {
                            "url": "https://mcp.linear.app/mcp",
                            "auth": "oauth",
                        }
                    }
                },
            }
        )
    )

    single_server_config = captured["linear"]

    assert isinstance(single_server_config, MCPConfig)
    assert single_server_config.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
    ) == {
        "mcpServers": {
            "linear": {
                "url": "https://mcp.linear.app/mcp",
                "auth": "oauth",
            }
        }
    }


def test_from_config_registers_mcp_server_from_stdio_config(tmp_path: Path) -> None:
    server_path = tmp_path / "server.py"
    server_path.write_text(
        textwrap.dedent(
            """
            from fastmcp import FastMCP

            mcp = FastMCP("Config Demo")

            @mcp.tool
            def multiply(x: int, y: int) -> int:
                return x * y

            if __name__ == "__main__":
                mcp.run()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "toolplane.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            [cli]
            mode = "disabled"

            [mcp.servers.docs]
            command = {json.dumps(sys.executable)}
            args = [{json.dumps(str(server_path))}]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    async def exercise() -> int:
        runtime = await Toolplane.from_config(config_path)
        result = await runtime.execute(
            """
return await docs.multiply(x=6, y=7)
"""
        )
        assert result.ok, result.error
        return result.value

    assert run(exercise()) == 42
