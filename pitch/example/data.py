"""Deterministic ticket and customer data shared by the demo servers and repo.

Everything here is fictional. The same seed always yields the same data, so
the servers, the generated git history, and the expected answer agree.
"""

from __future__ import annotations

import random

_rng = random.Random(1410)

N_RELEASE = 100  # tickets fixed by commits after the v1.4 tag
N_BEFORE_TAG = 15  # tickets fixed before the tag: must not be counted
N_OPEN = 25  # tickets no commit mentions at all

_COMPONENTS = [
    "webhook delivery", "invoice export", "SSO login", "audit trail",
    "search indexer", "billing sync", "report scheduler", "CSV import",
    "rate limiter", "session store", "email digest", "role permissions",
    "usage dashboard", "API gateway", "file uploads", "notification queue",
    "data retention job", "seat provisioning", "currency rounding",
    "password reset",
]
# (ticket title, commit subject) — the ticket states the problem, the commit the fix
_ISSUES = [
    ("{C} times out under load", "fix: add backoff to {c}"),
    ("{C} drops items after a retry", "fix: make {c} idempotent on retry"),
    ("{C} shows stale data", "fix: invalidate {c} cache on write"),
    ("{C} fails for accounts with many seats", "fix: paginate {c} for large accounts"),
    ("{C} is slow on first request", "perf: warm {c} on startup"),
    ("{C} returns a 500 on empty input", "fix: handle empty input in {c}"),
    ("{C} ignores the account time zone", "fix: use account time zone in {c}"),
    ("{C} logs are missing request ids", "chore: add request ids to {c} logs"),
    ("{C} leaks memory over long runs", "fix: release buffers in {c}"),
    ("{C} has no way to cancel", "feat: allow cancelling {c}"),
]
_CUSTOMER_NAMES = [
    "Halvorsen Freight", "Brightmoor Labs", "Quillon Health", "Osprey & Vane",
    "Tamsin Robotics", "Kestrel Ridge Bank", "Marrowfield Foods", "Juniper Hollow",
    "Calloway Optics", "Fennimore Grid", "Larkspur Media", "Dunmore Aerial",
    "Wexcombe Insurance", "Solenne Travel", "Ardwick Tooling", "Pellham Studios",
    "Corvane Logistics", "Thistledown Energy", "Ravenscar Mutual", "Ostara Biotech",
    "Mirefield Schools", "Bexley & Stroud", "Yarrow Street Games", "Glasswing Retail",
]
_PLANS = ["enterprise"] * 8 + ["team"] * 9 + ["free"] * 7

CUSTOMERS: dict[str, dict] = {}
for _i, (_name, _plan) in enumerate(
    zip(_CUSTOMER_NAMES, _rng.sample(_PLANS, len(_PLANS)), strict=True)
):
    _cid = f"CUS-{201 + _i}"
    CUSTOMERS[_cid] = {"id": _cid, "name": _name, "plan": _plan}

_total = N_RELEASE + N_BEFORE_TAG + N_OPEN
_ids = [f"TCK-{n}" for n in _rng.sample(range(1001, 1400), _total)]
_combos = _rng.sample([(c, i) for c in _COMPONENTS for i in _ISSUES], _total)

TICKETS: dict[str, dict] = {}
SUBJECTS: dict[str, str] = {}  # ticket id -> subject of the commit that fixes it
for _tid, (_component, (_title, _subject)) in zip(_ids, _combos, strict=True):
    _reported = _rng.random() < 0.40
    TICKETS[_tid] = {
        "id": _tid,
        "title": _title.format(C=_component[0].upper() + _component[1:]),
        "reported_by_customer": _reported,
        "customer_id": _rng.choice(sorted(CUSTOMERS)) if _reported else None,
        "opened_days_ago": _rng.randint(1, 30),
        "severity": _rng.choice(
            ["high", "high", "medium", "low"] if _reported else ["high", "medium", "low", "low"]
        ),
    }
    SUBJECTS[_tid] = f"{_subject.format(c=_component)} ({_tid})"

BEFORE_TAG_IDS = _ids[:N_BEFORE_TAG]
RELEASE_IDS = _ids[N_BEFORE_TAG : N_BEFORE_TAG + N_RELEASE]


def sla_breach(ticket: dict, customer: dict) -> bool:
    """The team's rule: an enterprise customer waited over 5 days on a high-severity ticket."""
    return (
        customer["plan"] == "enterprise"
        and ticket["severity"] == "high"
        and ticket["opened_days_ago"] > 5
    )


if __name__ == "__main__":
    release = [TICKETS[t] for t in RELEASE_IDS]
    reported = [t for t in release if t["reported_by_customer"]]
    hits = [t for t in reported if sla_breach(t, CUSTOMERS[t["customer_id"]])]
    print("release tickets:", len(release))
    print("customer-reported:", len(reported))
    print("enterprise customers:", sum(c["plan"] == "enterprise" for c in CUSTOMERS.values()))
    print("rule hits:", [(t["id"], CUSTOMERS[t["customer_id"]]["name"]) for t in hits])
    others = [TICKETS[t] for t in _ids if t not in RELEASE_IDS and TICKETS[t]["reported_by_customer"]]
    print("hits outside the release:", sum(sla_breach(t, CUSTOMERS[t["customer_id"]]) for t in others))
