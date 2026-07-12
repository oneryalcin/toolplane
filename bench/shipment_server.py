"""Deterministic stdio MCP server for the #127 second-domain validation.

Same two-tool record-store shape as order_server, a different domain. The
get_shipment description lists "state"; the benchmark task asks for a
shipment's "status" (a synonym NOT in the description), so a name-signal
re-export's leaf cannot carry the query word. "status" still collides with
the client's built-in Task tools, so the discovery difficulty is the same
as orders — isolating whether the name-signal bump was a lexical
coincidence of "status" appearing in the orders description.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastmcp import FastMCP
from shipment_data import DEFAULT_N, shipments

mcp = FastMCP("shipments")
_N = int(os.environ.get("BENCH_SHIPMENTS_N", str(DEFAULT_N)))
_LATENCY_S = float(os.environ.get("BENCH_TOOL_LATENCY_MS", "0")) / 1000.0
_BY_ID = {shipment["shipment_id"]: shipment for shipment in shipments(_N)}


@mcp.tool
async def list_shipment_ids() -> list[str]:
    """List every shipment id in the store."""
    if _LATENCY_S:
        await asyncio.sleep(_LATENCY_S)
    return sorted(_BY_ID)


@mcp.tool
async def get_shipment(shipment_id: str) -> dict:
    """Fetch one shipment record: shipment_id, carrier, destination, state."""
    if _LATENCY_S:
        await asyncio.sleep(_LATENCY_S)
    shipment = _BY_ID.get(shipment_id)
    if shipment is None:
        raise ValueError(f"no such shipment: {shipment_id}")
    return shipment


if __name__ == "__main__":
    mcp.run()
