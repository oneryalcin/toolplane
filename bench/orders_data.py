"""Deterministic order dataset shared by the bench MCP server and validator.

Formula-based (no RNG, no seed file) so the server process and the
harness process always agree byte-for-byte.
"""

from __future__ import annotations

REGIONS = ("amer", "apac", "emea")
DEFAULT_N = 30


def orders(n: int = DEFAULT_N) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        out.append(
            {
                "order_id": f"ORD-{i:03d}",
                "region": REGIONS[i % 3],
                "amount": round(100 + (i * 37.7) % 900, 2),
                "status": "shipped" if i % 4 else "pending",
            }
        )
    return out


def totals_by_region(n: int = DEFAULT_N) -> dict[str, float]:
    acc: dict[str, float] = {}
    for order in orders(n):
        acc[order["region"]] = round(acc.get(order["region"], 0.0) + order["amount"], 2)
    return acc


def emea_over_500(n: int = DEFAULT_N) -> int:
    return sum(1 for o in orders(n) if o["region"] == "emea" and o["amount"] > 500)
