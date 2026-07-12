"""Deterministic shipment dataset for the #127 second-domain validation.

Formula-based (no RNG) so the server and harness agree byte-for-byte,
mirroring orders_data. The field is named "state"; the benchmark task asks
for a shipment's "status" — a synonym ABSENT from the tool description, so
a name-signal re-export's leaf cannot carry the query word. Yet "status"
still collides with the client's built-in Task tools, so the discovery
DIFFICULTY that orders had is reproduced. That isolates the one variable:
whether the name-signal bump survives when the query word is not in the
description. If it does not, the orders bump was a lexical coincidence.
"""

from __future__ import annotations

CARRIERS = ("dhl", "fedex", "ups")
STATES = ("delivered", "delayed", "pending")
DEFAULT_N = 30


def shipments(n: int = DEFAULT_N) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        out.append(
            {
                "shipment_id": f"SHP-{i:03d}",
                "carrier": CARRIERS[i % 3],
                "destination": f"depot-{(i * 7) % 11:02d}",
                "state": STATES[i % 3],
            }
        )
    return out
