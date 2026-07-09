"""Deterministic stdio MCP server for the code-mode benchmark.

Two tools shaped like a typical record-store API: enumerate ids, fetch one
record. Fetching each record individually is the point — it forces the
round-trip structure the benchmark measures.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp import FastMCP
from orders_data import DEFAULT_N, chain_notes, orders

mcp = FastMCP("orders")
_N = int(os.environ.get("BENCH_ORDERS_N", str(DEFAULT_N)))
# simulated per-call backend latency (#107 latency axis); async sleep so
# the server itself never serializes concurrent client calls — any
# sequencing in the measurement comes from the arms, not the fixture
_LATENCY_S = float(os.environ.get("BENCH_TOOL_LATENCY_MS", "0")) / 1000.0
_BY_ID = {order["order_id"]: order for order in orders(_N)}
if os.environ.get("BENCH_NOTES") == "chain":
    for order_id, note in chain_notes(_N).items():
        _BY_ID[order_id] = {**_BY_ID[order_id], "note": note}


@mcp.tool
async def list_order_ids() -> list[str]:
    """List every order id in the store."""
    if _LATENCY_S:
        await asyncio.sleep(_LATENCY_S)
    return sorted(_BY_ID)


@mcp.tool
async def get_order(order_id: str) -> dict:
    """Fetch one order record: order_id, region, amount, status."""
    if _LATENCY_S:
        await asyncio.sleep(_LATENCY_S)
    order = _BY_ID.get(order_id)
    if order is None:
        raise ValueError(f"no such order: {order_id}")
    return order


if __name__ == "__main__":
    mcp.run()
