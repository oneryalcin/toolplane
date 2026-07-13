"""Deterministic stdio MCP server for the code-mode benchmark.

The ``BENCH_API_GRANULARITY`` profile exposes one of two mutually exclusive
record-store shapes: enumerate ids + fetch one record, or fetch every record
in one bulk response. Keeping the profiles exclusive makes API granularity a
fixture property rather than a model endpoint-choice confound (#117).
"""

from __future__ import annotations

import asyncio
import json
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
# payload axis (#117): pad each record so a direct fetch drops a fat blob
# into model context while the toolplane arm keeps it in the sandbox
_RECORD_BYTES = int(os.environ.get("BENCH_RECORD_BYTES", "0"))
_CALL_LOG = os.environ.get("BENCH_CALL_LOG")
_GRANULARITY = os.environ.get("BENCH_API_GRANULARITY", "fetch-one")
if _GRANULARITY not in {"fetch-one", "bulk"}:
    raise ValueError(
        "BENCH_API_GRANULARITY must be 'fetch-one' or 'bulk', "
        f"got {_GRANULARITY!r}"
    )
_BY_ID = {
    order["order_id"]: order for order in orders(_N, record_bytes=_RECORD_BYTES)
}
if os.environ.get("BENCH_NOTES") == "chain":
    for order_id, note in chain_notes(_N).items():
        _BY_ID[order_id] = {**_BY_ID[order_id], "note": note}


def _record_call(tool: str, **params: object) -> None:
    """Optional append-only fixture telemetry for longitudinal runs (#119)."""
    if not _CALL_LOG:
        return
    with Path(_CALL_LOG).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "params": params}) + "\n")


if _GRANULARITY == "fetch-one":

    @mcp.tool
    async def list_order_ids() -> list[str]:
        """List every order id in the store."""
        _record_call("list_order_ids")
        if _LATENCY_S:
            await asyncio.sleep(_LATENCY_S)
        return sorted(_BY_ID)

    @mcp.tool
    async def get_order(order_id: str) -> dict:
        """Fetch one order record: order_id, region, amount, status."""
        _record_call("get_order", order_id=order_id)
        if _LATENCY_S:
            await asyncio.sleep(_LATENCY_S)
        order = _BY_ID.get(order_id)
        if order is None:
            raise ValueError(f"no such order: {order_id}")
        return order

else:

    @mcp.tool
    async def get_orders() -> list[dict]:
        """Fetch all order records in one bulk response."""
        _record_call("get_orders")
        if _LATENCY_S:
            await asyncio.sleep(_LATENCY_S)
        return [_BY_ID[order_id] for order_id in sorted(_BY_ID)]


if __name__ == "__main__":
    mcp.run()
