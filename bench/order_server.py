"""Deterministic stdio MCP server for the code-mode benchmark.

Two tools shaped like a typical record-store API: enumerate ids, fetch one
record. Fetching each record individually is the point — it forces the
round-trip structure the benchmark measures.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp import FastMCP
from orders_data import DEFAULT_N, orders

mcp = FastMCP("orders")
_N = int(os.environ.get("BENCH_ORDERS_N", str(DEFAULT_N)))
_BY_ID = {order["order_id"]: order for order in orders(_N)}


@mcp.tool
def list_order_ids() -> list[str]:
    """List every order id in the store."""
    return sorted(_BY_ID)


@mcp.tool
def get_order(order_id: str) -> dict:
    """Fetch one order record: order_id, region, amount, status."""
    order = _BY_ID.get(order_id)
    if order is None:
        raise ValueError(f"no such order: {order_id}")
    return order


if __name__ == "__main__":
    mcp.run()
