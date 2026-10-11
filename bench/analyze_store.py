"""Deterministic analysis for the store-teaching registration.

    python bench/analyze_store.py --git-sha <registration merge sha> \
        --sessions bench/results/longitudinal-X.json

Reads committed rows and prints every verdict the plan
(bench/PREREG-store-teaching-2026-10.md) fixes. The bootstrap, pass gate,
died-session rule, margin-first equivalence and VOID checks are the
persistence follow-up's (analyze_persistence.py), imported unchanged.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_persistence import (
    PASS_GATE,
    REPS,
    REUSE,
    TURNS_1_5,
    _row,
    _stat,
    died,
    passed,
    verdict,
    void_reasons,
)
from analyze_prereg import EQUIVALENCE_MARGIN, _tally, bootstrap

ARMS = ("toolplane", "toolplane_nosession")
TURN1 = "neutral"
# T1 is a share, so the 'no effect' line is a majority, not zero
STORE_MAJORITY = 0.5


def store_share(session) -> float:
    reuse = [t for t in session["turns"] if t["turn"] in REUSE]
    return sum(t.get("reuse_mechanism") == "result_store" for t in reuse) / len(reuse)


def _gate(by_arm, arms):
    for arm in arms:
        ok, n = sum(passed(s) for s in by_arm[arm]), len(by_arm[arm])
        if n != REPS:
            return f"UNRESOLVED (incomplete: {arm} has {n}/{REPS})"
        if ok < PASS_GATE * n:
            return f"UNRESOLVED (gate: {arm} passed {ok}/{n})"
    return None


def _impute(groups_known, deaths, stat, judge):
    """Run stat at both extremes of each arm's died sessions; a verdict
    stands only if both agree (a death never decides a verdict)."""
    arms = list(groups_known)
    runs = []
    for low_first in (True, False):
        groups = {}
        for i, arm in enumerate(arms):
            fill = min if (i == 0) == low_first else max
            known = groups_known[arm]
            groups[arm] = known + [fill(known)] * deaths[arm]
        point, lo, hi = bootstrap(groups, stat)
        runs.append((point, lo, hi, judge(groups, lo, hi)))
        if not any(deaths.values()):
            return runs[0]
    (p, lo1, hi1, v1), (_, lo2, hi2, v2) = runs
    v = v1 if v1 == v2 else f"UNRESOLVED (depends on {sum(deaths.values())} died session(s))"
    return p, min(lo1, lo2), max(hi1, hi2), v


def hypotheses(sessions):
    by_arm = {arm: [s for s in sessions if s["arm"] == arm] for arm in ARMS}
    deaths = {arm: sum(died(s) for s in ss) for arm, ss in by_arm.items()}
    out = []

    label = "T1 sessions-off reuse turns served by the result store (share; >0.5)"
    blocked = _gate(by_arm, ["toolplane_nosession"])
    if blocked:
        out.append((label, None, None, None, blocked))
    else:
        known = [store_share(s) for s in by_arm["toolplane_nosession"] if not died(s)]
        out.append((label, *_died_share(known, deaths["toolplane_nosession"])))

    label = "T2 reuse cost, tp - nosession (turns 2-5; equivalence +/-10%)"
    blocked = _gate(by_arm, ARMS)
    if blocked:
        out.append((label, None, None, None, blocked))
    else:
        known = {arm: [_stat(s, "cost_usd", REUSE) for s in by_arm[arm] if not died(s)]
                 for arm in ARMS}
        a, b = ARMS
        point, lo, hi, v = _impute(
            known, deaths,
            lambda g: statistics.median(g[a]) - statistics.median(g[b]),
            lambda g, lo, hi: verdict(
                lo, hi, "~0", EQUIVALENCE_MARGIN * statistics.median(g[b])
            ),
        )
        # T2 compares a session against the store only if the store served
        # the reuse turns; otherwise it is session against refetch again
        if out[0][4] != "HOLDS":
            v += " (T1 did not hold: not a store comparison)"
        out.append((label, point, lo, hi, v))
    return out, by_arm


def _died_share(known, n_died):
    """Mean store share with a bootstrap CI against the majority line. A
    died session's share is unknown, so it is imputed at 0 and at 1 (not
    the observed range); the verdict stands only if both agree."""
    runs = []
    for fill in (0.0, 1.0):
        _, lo, hi = bootstrap({"s": known + [fill] * n_died}, lambda g: statistics.fmean(g["s"]))
        runs.append((lo, hi, verdict(lo - STORE_MAJORITY, hi - STORE_MAJORITY, ">0")))
    (lo1, hi1, v1), (lo2, hi2, v2) = runs
    v = v1 if v1 == v2 else f"UNRESOLVED (depends on {n_died} died session(s))"
    point = statistics.fmean(known) if known else float("nan")
    return point, min(lo1, lo2), max(hi1, hi2), v


def store_void_reasons(sessions, git_sha: str) -> list[str]:
    reasons = void_reasons([], sessions)
    # uniform rows from the wrong commit (an old or reverted description)
    # would otherwise pass: pin the registration's merge commit
    shas = {s.get("git_sha") for s in sessions}
    if shas != {git_sha}:
        reasons.append(f"rows at {sorted(map(str, shas))}, not the registered {git_sha}")
    turn1 = {s.get("turn1", "sessions") for s in sessions}
    if turn1 - {TURN1}:
        reasons.append(f"sessions with turn1 {sorted(turn1 - {TURN1})}")
    return reasons


def report(sessions) -> str:
    hyps, by_arm = hypotheses(sessions)
    out = ["# Store-teaching re-test: results", "", "## Hypotheses", "",
           "| id | point | 95% CI | verdict |", "|---|---|---|---|"]
    out += [_row(*h) for h in hyps]
    out += ["", "## Sessions per arm (reported, not tested)", "",
            ("| arm | passed 1-5 / sessions | errors | median cost 1-5 | median reuse cost 2-5 "
             "| median peak ctx | reuse mechanism (turns 2-5) |"),
            "|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        ss = by_arm[arm]
        done = [s for s in ss if not died(s)]
        med = (lambda xs: f"{statistics.median(xs):.4f}" if xs else "—")
        out.append(
            f"| {arm} | {sum(passed(s) for s in ss)}/{len(ss)} | {len(ss) - len(done)} "
            f"| {med([_stat(s, 'cost_usd', TURNS_1_5) for s in done])} "
            f"| {med([_stat(s, 'cost_usd', REUSE) for s in done])} "
            f"| {med([_stat(s, 'peak_request_context_tokens', TURNS_1_5) for s in done])} "
            f"| {_tally(t.get('reuse_mechanism', 'unrecorded') for s in done for t in s['turns'] if t['turn'] in REUSE)} |"
        )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", nargs="+", type=Path, required=True)
    parser.add_argument("--git-sha", required=True)
    args = parser.parse_args()
    sessions = [s for p in args.sessions for s in json.loads(p.read_text())["rows"]]
    reasons = store_void_reasons(sessions, args.git_sha)
    if reasons:
        print("VOID: " + "; ".join(reasons), file=sys.stderr)
        return 2
    print(report(sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
