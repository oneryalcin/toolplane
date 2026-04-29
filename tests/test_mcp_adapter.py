from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from toolplane import CapabilityRegistry, DuplicateCapabilityError, Toolplane

pytest.importorskip("fastmcp")
from fastmcp import FastMCP  # noqa: E402


class FakeCliResult:
    stdout = "3"
    stderr = ""
    exit_code = 0

    def ok(self) -> bool:
        return True


def run(coro):
    return asyncio.run(coro)


def test_register_fastmcp_app_exposes_structured_python_values() -> None:
    async def exercise() -> tuple[list[str], dict[str, object]]:
        runtime = Toolplane()
        mcp = FastMCP("Arch")
        holdings = [
            {"id": "h1", "capital_account_dollars": 10},
            {"id": "h2", "capital_account_dollars": 15},
            {"id": "h3", "capital_account_dollars": 5},
        ]

        @mcp.tool
        def list_entities(entity_type: str, limit: int, offset: int) -> dict:
            """List entities from the Arch demo catalog."""
            assert entity_type == "holding"
            page_items = holdings[offset : offset + limit]
            return {
                "items": page_items,
                "page": {
                    "has_more": offset + len(page_items) < len(holdings),
                    "returned": len(page_items),
                },
            }

        capabilities = await runtime.register_mcp("arch", mcp)
        result = await runtime.execute(
            """
all_holdings = []
offset = 0

while True:
    page = await arch_list_entities(
        entity_type="holding",
        limit=2,
        offset=offset,
    )
    all_holdings.extend(page["items"])
    if not page["page"]["has_more"]:
        break
    offset += page["page"]["returned"]

total_nav = sum(h.get("capital_account_dollars", 0) for h in all_holdings)
return {"total_nav": total_nav, "holding_count": len(all_holdings)}
"""
        )
        assert result.ok, result.error
        return [capability.name for capability in capabilities], result.value

    names, value = run(exercise())

    assert names == ["mcp:arch/list_entities"]
    assert value == {"total_nav": 30, "holding_count": 3}


def test_mcp_canonical_id_and_alias_both_dispatch() -> None:
    async def exercise() -> tuple[int, int, list[str]]:
        runtime = Toolplane()
        mcp = FastMCP("Demo")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        capabilities = await runtime.register_mcp("demo", mcp)
        via_alias = await runtime.call_tool("demo_add", {"a": 2, "b": 3})
        via_canonical = await runtime.call_tool("mcp:demo/add", {"a": 5, "b": 7})
        return via_alias, via_canonical, sorted(capabilities[0].aliases)

    via_alias, via_canonical, aliases = run(exercise())

    assert via_alias == 5
    assert via_canonical == 12
    assert aliases == ["demo_add"]


def test_mcp_cli_and_python_capabilities_mix_in_one_snippet() -> None:
    async def exercise() -> int:
        runtime = Toolplane()
        mcp = FastMCP("Demo")

        @mcp.tool
        def double(x: int) -> int:
            return x * 2

        @runtime.tool
        def add(x: int, y: int) -> int:
            return x + y

        def cli_value(**_: Any) -> FakeCliResult:
            return FakeCliResult()

        runtime.register_cli("cli_value", cli_value)
        await runtime.register_mcp("demo", mcp)
        result = await runtime.execute(
            """
base = await demo_double(x=4)
from_cli = await cli_value()
return await add(x=base, y=int(from_cli["stdout"]))
"""
        )
        assert result.ok, result.error
        return result.value

    assert run(exercise()) == 11


def test_mcp_tool_error_detail_reaches_execution_result() -> None:
    async def exercise() -> str:
        runtime = Toolplane()
        mcp = FastMCP("Demo")

        @mcp.tool
        def explode() -> None:
            raise ValueError("original mcp detail")

        await runtime.register_mcp("demo", mcp)
        result = await runtime.execute("return await demo_explode()")

        assert not result.ok
        assert result.error is not None
        return result.error.message

    assert "original mcp detail" in run(exercise())


def test_mcp_alias_collision_fails_loudly() -> None:
    async def exercise() -> None:
        runtime = Toolplane()

        @runtime.tool(name="demo_add")
        def existing_alias() -> int:
            return 1

        mcp = FastMCP("Demo")

        @mcp.tool
        def add(a: int, b: int) -> int:
            return a + b

        await runtime.register_mcp("demo", mcp)

    with pytest.raises(DuplicateCapabilityError):
        run(exercise())


def test_register_mcp_config_supports_stdio_server(tmp_path: Path) -> None:
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

    async def exercise() -> int:
        runtime = Toolplane()
        await runtime.register_mcp_config(
            {
                "mcpServers": {
                    "context7": {
                        "command": sys.executable,
                        "args": [str(server_path)],
                    }
                }
            }
        )
        result = await runtime.execute(
            """
product = await context7_multiply(x=6, y=7)
return product
"""
        )
        assert result.ok, result.error
        return result.value

    assert run(exercise()) == 42


def test_mcp_config_accepts_remote_url_shape_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.mcp_config import MCPConfig
    from toolplane.adapters import mcp as mcp_adapter

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
        mcp_adapter.register_mcp_config(
            CapabilityRegistry(),
            {
                "mcpServers": {
                    "context7": {
                        "url": "https://mcp.context7.com/mcp",
                    }
                }
            },
        )
    )

    single_server_config = captured["context7"]

    assert isinstance(single_server_config, MCPConfig)
    assert single_server_config.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
    ) == {
        "mcpServers": {
            "context7": {
                "url": "https://mcp.context7.com/mcp",
            }
        }
    }


def test_mcp_config_accepts_fastmcp_root_server_shape_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp.mcp_config import MCPConfig
    from toolplane.adapters import mcp as mcp_adapter

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
        mcp_adapter.register_mcp_config(
            CapabilityRegistry(),
            {
                "context7": {
                    "url": "https://mcp.context7.com/mcp",
                }
            },
        )
    )

    single_server_config = captured["context7"]

    assert isinstance(single_server_config, MCPConfig)
    assert single_server_config.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
    ) == {
        "mcpServers": {
            "context7": {
                "url": "https://mcp.context7.com/mcp",
            }
        }
    }
