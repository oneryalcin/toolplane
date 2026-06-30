from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path

import pytest

from toolplane import Toolplane, build_mcp_facade, build_mcp_facade_from_config
from toolplane.cli import main

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def write_stdio_demo_config(tmp_path: Path) -> Path:
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
                mcp.run(show_banner=False, log_level="ERROR")
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
    return config_path


def test_mcp_facade_exposes_only_toolplane_meta_tools() -> None:
    async def exercise() -> list[str]:
        app = build_mcp_facade(Toolplane(ambient_cli=False))
        async with Client(app) as client:
            tools = await client.list_tools()
        return sorted(tool.name for tool in tools)

    assert run(exercise()) == [
        "execute_code",
        "get_capability_schemas",
        "search_capabilities",
    ]


def test_mcp_facade_search_schema_execute_flow() -> None:
    async def exercise() -> dict[str, object]:
        runtime = Toolplane(ambient_cli=False)

        @runtime.tool(tags={"math"})
        def add(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y

        app = build_mcp_facade(runtime)
        async with Client(app) as client:
            search = await client.call_tool(
                "search_capabilities",
                {"query": "add numbers"},
            )
            schema = await client.call_tool(
                "get_capability_schemas",
                {"names": ["add"]},
            )
            execution = await client.call_tool(
                "execute_code",
                {"code": "return await add(x=2, y=3)"},
            )
        return {
            "search": search.data,
            "schema": schema.data,
            "execution": execution.data,
        }

    result = run(exercise())

    assert "- add: Add two numbers." in result["search"]
    assert "### add" in result["schema"]
    assert result["execution"]["value"] == 5
    assert result["execution"]["backend"] == "local_unsafe"
    assert result["execution"]["error"] is None


def test_mcp_facade_from_config_executes_stdio_mcp_tool(tmp_path: Path) -> None:
    config_path = write_stdio_demo_config(tmp_path)

    async def exercise() -> dict[str, object]:
        app = await build_mcp_facade_from_config(config_path)
        async with Client(app) as client:
            result = await client.call_tool(
                "execute_code",
                {"code": "return await docs.multiply(x=6, y=7)"},
            )
        return result.data

    result = run(exercise())

    assert result["value"] == 42
    assert result["error"] is None


def test_cli_stdio_facade_round_trip_crosses_process_boundary(tmp_path: Path) -> None:
    config_path = write_stdio_demo_config(tmp_path)

    async def exercise() -> dict[str, object]:
        client_config = {
            "mcpServers": {
                "toolplane": {
                    "command": sys.executable,
                    "args": [
                        "-m",
                        "toolplane.cli",
                        "serve",
                        "mcp",
                        "--config",
                        str(config_path),
                    ],
                    "cwd": str(Path.cwd()),
                }
            }
        }
        async with Client(client_config) as client:
            tools = await client.list_tools()
            execution = await client.call_tool(
                "execute_code",
                {"code": "return await docs.multiply(x=6, y=7)"},
            )
            failure = await client.call_tool(
                "execute_code",
                {"code": "raise ValueError('stdio boundary detail')"},
            )
        return {
            "tools": sorted(tool.name for tool in tools),
            "execution": execution.data,
            "failure": failure.data,
        }

    result = run(exercise())

    assert result["tools"] == [
        "execute_code",
        "get_capability_schemas",
        "search_capabilities",
    ]
    assert result["execution"]["value"] == 42
    assert result["execution"]["error"] is None
    assert result["failure"]["error"]["type"] == "ValueError"
    assert "stdio boundary detail" in result["failure"]["error"]["message"]


def test_cli_requires_nested_serve_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serve"]) == 2
    captured = capsys.readouterr()
    assert "usage: toolplane" in captured.out
