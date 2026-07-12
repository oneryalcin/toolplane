"""Deterministic order dataset shared by the bench MCP server and validator.

Formula-based (no RNG, no seed file) so the server process and the
harness process always agree byte-for-byte.
"""

from __future__ import annotations

REGIONS = ("amer", "apac", "emea")
DEFAULT_N = 30


def _filler(seed: int, nbytes: int) -> str:
    """Deterministic per-order padding of ~nbytes (formula-based, no RNG).

    Its CONTENT is irrelevant to every task's answer — only its SIZE
    matters. This is the payload axis (#117): a fat record inflates what a
    direct fetch drops into model context, while the toolplane arm keeps it
    in the sandbox and only the aggregate escapes.
    """
    if nbytes <= 0:
        return ""
    token = f"ord{seed:04d}-"
    return (token * (nbytes // len(token) + 1))[:nbytes]


def orders(n: int = DEFAULT_N, record_bytes: int = 0) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        record = {
            "order_id": f"ORD-{i:03d}",
            "region": REGIONS[i % 3],
            "amount": round(100 + (i * 37.7) % 900, 2),
            "status": "shipped" if i % 4 else "pending",
        }
        if record_bytes:
            # a "detail" blob the tasks never read; sizes the fetch payload
            record["detail"] = _filler(i, record_bytes)
        out.append(record)
    return out


def totals_by_region(n: int = DEFAULT_N) -> dict[str, float]:
    acc: dict[str, float] = {}
    for order in orders(n):
        acc[order["region"]] = round(acc.get(order["region"], 0.0) + order["amount"], 2)
    return acc


def emea_over_500(n: int = DEFAULT_N) -> int:
    return sum(1 for o in orders(n) if o["region"] == "emea" and o["amount"] > 500)


# --- adaptive-hop chain (#107 item 2: the shape prior work says code mode
# loses). Each order's note names the next order in a follow-up chain AND a
# decoy order, with the mention order and phrasing varying by hop, so a
# position- or template-guessing one-shot snippet extracts the wrong id.
# Correctness requires reading each note before the next fetch.

CHAIN_START = "ORD-001"
CHAIN_HOPS = 4

_NOTE_TEMPLATES = (
    "Customer replied twice. Disregard the accidental duplicate {decoy}; "
    "the genuine follow-up to process is {real}.",
    "Ops note: {real} supersedes this order. (A clerk mistakenly linked "
    "{decoy} earlier — that one was voided.)",
    "Follow-up thread: please continue with {real}. The reference to "
    "{decoy} in the customer's email is their OLD cancelled order.",
    "Warehouse flagged {decoy} as unrelated. The order that continues "
    "this case is {real}.",
)


def _chain_next(i: int, n: int) -> int:
    # deterministic; empirically cycle-free for CHAIN_HOPS hops from
    # CHAIN_START at n=30 and n=100 (path 1-5-17-23-11 / 1-5-17-53-61)
    return ((i * 3 + 1) % n) + 1


def _chain_decoy(i: int, n: int) -> int:
    decoy = ((i * 11 + 5) % n) + 1
    if decoy == _chain_next(i, n):
        decoy = (decoy % n) + 1
    return decoy


def chain_notes(n: int = DEFAULT_N) -> dict[str, str]:
    """order_id -> note, for the ids on the chain path (others get none)."""
    notes: dict[str, str] = {}
    i = int(CHAIN_START.split("-")[1])
    for hop in range(CHAIN_HOPS):
        real, decoy = _chain_next(i, n), _chain_decoy(i, n)
        template = _NOTE_TEMPLATES[hop % len(_NOTE_TEMPLATES)]
        notes[f"ORD-{i:03d}"] = template.format(
            real=f"ORD-{real:03d}", decoy=f"ORD-{decoy:03d}"
        )
        i = real
    notes[f"ORD-{i:03d}"] = "This is the final order in the thread."
    return notes


def chain_answer(n: int = DEFAULT_N) -> dict[str, str]:
    """The order the chain ends on after CHAIN_HOPS hops, and its status."""
    i = int(CHAIN_START.split("-")[1])
    for _ in range(CHAIN_HOPS):
        i = _chain_next(i, n)
    order = next(o for o in orders(n) if o["order_id"] == f"ORD-{i:03d}")
    return {"order_id": order["order_id"], "status": order["status"]}
