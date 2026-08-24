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
    async def exercise() -> tuple[int, int, int, list[str], dict[str, object]]:
        runtime = Toolplane()
        mcp = FastMCP("Demo")

        @mcp.tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        capabilities = await runtime.register_mcp("demo", mcp)
        via_alias = await runtime.call_tool("demo_add", {"a": 2, "b": 3})
        via_canonical = await runtime.call_tool("mcp:demo/add", {"a": 5, "b": 7})
        via_namespace = await runtime.execute("return await demo.add(a=11, b=13)")
        assert via_namespace.ok, via_namespace.error
        schema = capabilities[0].to_schema()
        return (
            via_alias,
            via_canonical,
            via_namespace.value,
            sorted(capabilities[0].aliases),
            schema,
        )

    via_alias, via_canonical, via_namespace, aliases, schema = run(exercise())

    assert via_alias == 5
    assert via_canonical == 12
    assert via_namespace == 24
    assert aliases == ["demo_add"]
    assert schema["namespace"] == {"name": "demo", "member": "add"}


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
    real_prepare = mcp_adapter._prepare_server_entry

    def fake_prepare(server_name: str, server_config: object) -> object:
        captured[server_name] = real_prepare(server_name, server_config)
        raise ConnectionError("not connecting in this test")

    monkeypatch.setattr(mcp_adapter, "_prepare_server_entry", fake_prepare)

    with pytest.warns(UserWarning, match="context7"):
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
    real_prepare = mcp_adapter._prepare_server_entry

    def fake_prepare(server_name: str, server_config: object) -> object:
        captured[server_name] = real_prepare(server_name, server_config)
        raise ConnectionError("not connecting in this test")

    monkeypatch.setattr(mcp_adapter, "_prepare_server_entry", fake_prepare)

    with pytest.warns(UserWarning, match="context7"):
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


def test_composed_output_schema_result_normalizes_to_plain_values() -> None:
    """fastmcp deserializes structured content against the tool's output
    schema; composed schemas ($ref/$defs) come back as generated
    dataclasses, which must not leak into capability results (#132
    finding 4)."""
    from toolplane.adapters.mcp import register_mcp_server

    async def exercise() -> object:
        mcp = FastMCP("composed")

        @mcp.tool(
            output_schema={
                "type": "object",
                "$defs": {
                    "item": {
                        "type": "object",
                        "properties": {"n": {"type": "integer"}},
                    }
                },
                "properties": {
                    "many": {"type": "array", "items": {"$ref": "#/$defs/item"}}
                },
            }
        )
        def rich_tool() -> dict:
            return {"many": [{"n": 1}]}

        registry = CapabilityRegistry()
        (cap,) = await register_mcp_server(registry, "composed", mcp)
        return await cap.callable()

    result = run(exercise())

    assert result == {"many": [{"n": 1}]}


def _delay_server_script(tmp_path: Path) -> Path:
    server_path = tmp_path / "delay_server.py"
    server_path.write_text(
        textwrap.dedent(
            """
            import argparse
            from fastmcp import FastMCP

            parser = argparse.ArgumentParser()
            parser.add_argument("--delay", type=float, default=0.0)
            parser.add_argument("--name", default="Delayed")
            args = parser.parse_args()

            mcp = FastMCP(args.name)

            @mcp.tool
            def ping() -> str:
                return "pong"

            if args.delay:
                import time
                time.sleep(args.delay)
            mcp.run(show_banner=False, log_level="ERROR")
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return server_path


def test_register_mcp_config_insertion_order_is_config_order(tmp_path: Path) -> None:
    """Completion order must not decide registry order (#118): the slowest
    server is listed first, so sequential init and concurrent init would
    disagree if results were added as they finish."""
    import time as time_mod

    server = _delay_server_script(tmp_path)

    async def exercise() -> list[str]:
        runtime = Toolplane()
        started = time_mod.perf_counter()
        caps = await runtime.register_mcp_config(
            {
                "mcpServers": {
                    "slow": {
                        "command": sys.executable,
                        "args": [str(server), "--delay", "1.5", "--name", "Slow"],
                    },
                    "medium": {
                        "command": sys.executable,
                        "args": [str(server), "--delay", "0.7", "--name", "Medium"],
                    },
                    "fast": {"command": sys.executable, "args": [str(server)]},
                }
            }
        )
        wall = time_mod.perf_counter() - started
        # concurrency proof with CI-slack: three spawns overlap, so wall is
        # roughly max(delay)+spawn (~2-4s); sequential init would be
        # >= sum(delays) + 3 spawns >= ~6.6s
        assert wall < 6.0, f"not concurrent: {wall:.2f}s"
        return [c.name for c in caps]

    assert run(exercise()) == [
        "mcp:slow/ping",
        "mcp:medium/ping",
        "mcp:fast/ping",
    ]


def test_unavailable_server_warns_and_does_not_block_others(
    tmp_path: Path,
) -> None:
    """One hung upstream must not hold facade startup hostage (#118):
    bounded per-server wait, skip-with-warning, healthy servers still
    register. The 8s bound must exceed a cold python+fastmcp spawn on
    CI runners (~2s) while staying well under the hung server's delay."""
    server = _delay_server_script(tmp_path)

    async def exercise() -> tuple[list[str], list[str]]:
        runtime = Toolplane()
        with pytest.warns(UserWarning, match="hung"):
            caps = await runtime.register_mcp_config(
                {
                    "mcpServers": {
                        "hung": {
                            "command": sys.executable,
                            "args": [str(server), "--delay", "30"],
                        },
                        "healthy": {"command": sys.executable, "args": [str(server)]},
                    }
                },
                timeout_seconds=8.0,
            )
        names = [c.name for c in caps]
        return names, list(runtime.registry.callable_namespace())

    names, namespace = run(exercise())

    assert names == ["mcp:healthy/ping"]
    assert any("healthy_ping" in n for n in namespace)
