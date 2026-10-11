"""Fictional customer directory as a stdio MCP server."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp import FastMCP

from data import CUSTOMERS

mcp = FastMCP("customers")
_CALL_LOG = os.environ.get("DEMO_CALL_LOG")
_LATENCY_S = float(os.environ.get("DEMO_LATENCY_MS", "0")) / 1000.0


def _record_call(tool: str, id: str) -> None:
    """Append one line per call, so calls can be counted independently of toolplane."""
    if _CALL_LOG:
        with Path(_CALL_LOG).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"tool": tool, "id": id}) + "\n")


@mcp.tool
async def get_customer(id: str) -> dict:
    """Fetch one customer: id, name, plan (enterprise, team or free)."""
    _record_call("get_customer", id)
    await asyncio.sleep(_LATENCY_S)
    if id not in CUSTOMERS:
        raise ValueError(f"no such customer: {id}")
    return CUSTOMERS[id]


if __name__ == "__main__":
    mcp.run(show_banner=False, log_level="WARNING")
