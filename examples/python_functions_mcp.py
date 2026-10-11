"""Serve your own Python functions to an MCP client (Claude Code, Codex, ...).

`toolplane serve mcp` serves what a config file can name: MCP servers and
allowed CLI binaries. Python functions are registered in code, so serving
them takes a launcher like this one: build the runtime from the config,
register the functions, then serve the same facade over stdio.

Point a client at it:

    claude mcp add my-tools -- python /abs/path/to/python_functions_mcp.py

Prove it end to end (spawns this file as a stdio server and calls it):

    python examples/python_functions_mcp.py --self-test
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from toolplane import Toolplane, build_mcp_facade, load_toolplane_config
from toolplane.policy import EffectivePolicy, ensure_safe_facade_policy

CONFIG = Path(__file__).with_name("python_functions.toml")


def sla_breach(severity: str, days_open: int) -> bool:
    """True when a high-severity ticket has been open more than 5 days."""
    return severity == "high" and days_open > 5


async def serve() -> None:
    config = load_toolplane_config(CONFIG)
    runtime = await Toolplane.from_config(config)
    # register before building: the facade's tool descriptions are a snapshot
    # of the registry at build time
    runtime.register_python_namespace("policy", {"sla_breach": sla_breach})
    # the policy is what pins execute_code to the configured sandbox; without
    # it a client could ask for backend="local_unsafe"
    policy = EffectivePolicy.from_config(config)
    ensure_safe_facade_policy(policy)
    app = build_mcp_facade(runtime, policy=policy)
    await app.run_async(transport="stdio", show_banner=False)


async def self_test() -> None:
    from fastmcp import Client

    async with Client(Path(__file__)) as client:
        found = await client.call_tool("search_capabilities", {"query": "sla breach"})
        assert "policy_sla_breach" in str(found.data), found.data

        result = await client.call_tool(
            "execute_code",
            {
                "code": (
                    "version = await git('version')\n"
                    "late = await policy_sla_breach(severity='high', days_open=9)\n"
                    "fresh = await policy_sla_breach(severity='high', days_open=2)\n"
                    "return {'git': version['ok'], 'late': late, 'fresh': fresh}"
                )
            },
        )
        assert result.data["error"] is None, result.data["error"]
        assert result.data["value"] == {"git": True, "late": True, "fresh": False}

        unsafe = await client.call_tool(
            "execute_code", {"code": "return 1", "backend": "local_unsafe"}
        )
        assert unsafe.data["error"]["type"] == "BackendPolicyError", unsafe.data

    print(result.data["value"])


if __name__ == "__main__":
    asyncio.run(self_test() if "--self-test" in sys.argv[1:] else serve())
