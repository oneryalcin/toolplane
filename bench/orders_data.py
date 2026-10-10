"""Deterministic order dataset shared by the bench MCP server and validator.

Formula-based or seeded per order (no seed file) so the server process
and the harness process always agree byte-for-byte.
"""

from __future__ import annotations

import random

REGIONS = ("amer", "apac", "emea")
DEFAULT_N = 30


_WORDS = (
    "customer", "requested", "delivery", "window", "updated", "invoice",
    "attached", "warehouse", "pallet", "scanned", "carrier", "label",
    "printed", "address", "verified", "signature", "on", "file", "backorder",
    "partial", "shipment", "notes", "priority", "standard", "express",
    "return", "policy", "applied", "discount", "code", "region", "manager",
    "approved", "credit", "hold", "released", "inventory", "recount", "dock",
    "door", "seal", "intact", "temperature", "logged", "fragile", "contents",
    "insured", "value", "declared", "customs", "form", "tracking", "number",
    "reissued", "follow", "up", "call", "scheduled", "email", "sent",
    "ticket", "escalated", "resolved", "pending", "review", "audit", "trail",
    "entry", "duplicate", "merged", "vendor", "contact", "reference",
)


def _filler(seed: int, nbytes: int, style: str = "repeat") -> str:
    """Deterministic per-order padding of ~nbytes.

    Its CONTENT is irrelevant to every task's answer — only its SIZE
    matters. This is the payload axis (#117): a fat record inflates what a
    direct fetch drops into model context, while the toolplane arm keeps it
    in the sandbox and only the aggregate escapes.

    "repeat" is one token repeated (every result before the persistence
    follow-up); Haiku 5.5 degenerated on it (prereg A1, H3 at 2 KB).
    "varied" is seeded word text, closer to a real notes field.
    """
    if nbytes <= 0:
        return ""
    if style == "varied":
        rng = random.Random(seed)
        words: list[str] = []
        size = 0
        while size < nbytes:
            words.append(rng.choice(_WORDS))
            size += len(words[-1]) + 1
        return " ".join(words)[:nbytes]
    if style != "repeat":
        raise ValueError(f"unknown filler style {style!r}")
    token = f"ord{seed:04d}-"
    return (token * (nbytes // len(token) + 1))[:nbytes]


def orders(n: int = DEFAULT_N, record_bytes: int = 0, filler: str = "repeat") -> list[dict]:
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
            record["detail"] = _filler(i, record_bytes, filler)
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
    ("Customer replied twice. Disregard the accidental duplicate {decoy}; "
    "the genuine follow-up to process is {real}."),
    ("Ops note: {real} supersedes this order. (A clerk mistakenly linked "
    "{decoy} earlier — that one was voided.)"),
    ("Follow-up thread: please continue with {real}. The reference to "
    "{decoy} in the customer's email is their OLD cancelled order."),
    ("Warehouse flagged {decoy} as unrelated. The order that continues "
    "this case is {real}."),
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


# --- code-resistant chain (#113 item 3). The templated chain above is
# heuristically separable (a keyword regex walks it). Here EVERY order
# carries a hand-written, frozen note in free prose — off-path notes name
# orders too, the terminal note names a further order (only the hop count
# says stop), and each path hop identifies the real next order by meaning:
# a correction sequence, an oblique reference that never spells the id,
# a contrast, an elimination. tests/test_bench_validators.py pins that
# named heuristics (first id, last id, keyword-adjacent, negation filter,
# the templated regex) each walk this chain to the wrong order.

PROSE_PATH = ("ORD-001", "ORD-005", "ORD-017", "ORD-023", "ORD-011")
PROSE_N = 30

_PROSE_NOTES = {
    # path
    "ORD-001": "Spoke to the customer this morning. They first asked us to "
    "pick this up under ORD-005, then said no, that was their sister's "
    "account, and told us to use ORD-019 instead. An hour later they called "
    "back: the sister had placed it after all, so the number they gave first "
    "stands.",
    "ORD-005": "Do not escalate to ORD-022; that ticket was closed as a "
    "duplicate. The case moves to the order placed immediately after "
    "ORD-016.",
    "ORD-017": "Billing thought ORD-008 was the next step, but it was "
    "refunded last week and is closed. ORD-023 is where this goes now.",
    "ORD-023": "The courier swapped labels between ORD-011 and ORD-027. "
    "ORD-027 left the warehouse on time; ours is the one that never "
    "shipped, and that is where the case continues.",
    "ORD-011": "Customer wants this merged into ORD-014 once the "
    "replacement ships.",
    # off the path: same register, same habit of naming other orders
    "ORD-002": "Gift order. The recipient asked whether ORD-009 could ship "
    "together with it; we said no.",
    "ORD-003": "Address corrected by phone. Unrelated to the ORD-021 "
    "complaint from the same street.",
    "ORD-004": "Paid twice by mistake; the second charge was reversed "
    "against ORD-026.",
    "ORD-006": "Customer is waiting on ORD-012 before confirming this one.",
    "ORD-007": "Wholesale account. Pricing follows the ORD-002 quote, not "
    "the newer one.",
    "ORD-008": "Refunded in full after the ORD-017 dispute. Closed.",
    "ORD-009": "Back-ordered item. If it slips again, offer ORD-030's "
    "substitute.",
    "ORD-010": "No issues reported.",
    "ORD-012": "Delivered. The customer mentioned ORD-006 is for a "
    "different address.",
    "ORD-013": "Fraud check passed after the ORD-024 review cleared the "
    "card.",
    "ORD-014": "Merge target for an older order; leave open until that one "
    "ships.",
    "ORD-015": "Duplicate of ORD-018 per the customer; keep both until "
    "finance confirms.",
    "ORD-016": "Routine. Customer also owns ORD-020.",
    "ORD-018": "See ORD-015. Finance has not confirmed yet.",
    "ORD-019": "Placed on the sister's account mentioned in another thread. "
    "Nothing pending.",
    "ORD-020": "Packed with ORD-016 to save shipping.",
    "ORD-021": "Complaint about noise from the neighbour of ORD-003's "
    "customer; not an order problem.",
    "ORD-022": "Closed as a duplicate; the live case went elsewhere.",
    "ORD-024": "Card review opened here, cleared, and noted on ORD-013.",
    "ORD-025": "Customer asked to cancel, then kept it. ORD-029 is their "
    "next planned purchase.",
    "ORD-026": "Received the reversed duplicate charge from ORD-004.",
    "ORD-027": "Shipped on time despite the label mix-up.",
    "ORD-028": "Store credit applied from ORD-010's goodwill gesture.",
    "ORD-029": "Pre-order. Linked to ORD-025 by the customer.",
    "ORD-030": "Substitute stock reserved in case ORD-009 slips.",
}


def prose_chain_notes(n: int = DEFAULT_N) -> dict[str, str]:
    """order_id -> note for every order; defined only at PROSE_N."""
    if n != PROSE_N:
        raise ValueError(f"the prose chain is hand-written for n={PROSE_N}")
    return dict(_PROSE_NOTES)


def prose_chain_answer(n: int = DEFAULT_N) -> dict[str, str]:
    order = next(o for o in orders(n) if o["order_id"] == PROSE_PATH[-1])
    return {"order_id": order["order_id"], "status": order["status"]}


# --- CLI + MCP join (#113 item 2). A git history in the agent's cwd says
# which orders were refunded; the order amounts live behind MCP. The log
# decides which MCP calls happen, so the join is real: neither source
# answers alone. Non-refund commits mention orders too (fixes, tests,
# chores), so "every id in the log" is the wrong set.

MIXED_N = 30


def mixed_commits() -> list[str]:
    """Commit messages, oldest first. Deterministic."""
    out = []
    for i in range(1, MIXED_N + 1):
        oid = f"ORD-{i:03d}"
        if i % 5 in (1, 3):
            out.append(f"refund: {oid} customer return approved")
        if i % 7 == 0:
            out.append(f"fix: retry payment webhook for {oid}")
        if i % 9 == 0:
            out.append(f"test: add fixture covering {oid}")
        if i % 10 == 4:
            out.append(f"chore: update carrier mapping (seen on {oid})")
    out.insert(5, "docs: describe the refund commit convention")
    out.insert(11, "chore: bump dependencies")
    return out


def mixed_refunded_ids() -> list[str]:
    return [
        msg.split()[1] for msg in mixed_commits() if msg.startswith("refund: ")
    ]


def mixed_answer() -> float:
    by_id = {o["order_id"]: o["amount"] for o in orders(MIXED_N)}
    return round(sum(by_id[oid] for oid in mixed_refunded_ids()), 2)
