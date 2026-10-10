"""Deterministic analysis for the persistence follow-up registration.

    python bench/analyze_persistence.py --sessions bench/results/longitudinal-X.json \
        --runs bench/results/run-Y.json

Reads committed rows and prints every verdict the plan
(bench/PREREG-persistence-2026-10.md) fixes. The bootstrap, cell matching,
timeout imputation and directional rule are the confirmation matrix's
(analyze_prereg.py). One rule differs, as registered: an equivalence (~0)
prediction checks the margin FIRST -- a CI wholly inside +/-10% of the
reference HOLDS even when it also excludes 0 (two one-sided tests); the
confirmation matrix's rule checked "excludes 0" first and reported a +3%
effect as REVERSED (its H8).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_prereg import EQUIVALENCE_MARGIN, _fmt, _tally, bootstrap, cost_diff

MODEL = "claude-sonnet-5-5"
FILLER = "varied"
ARMS = ("direct", "toolplane", "toolplane_nosession")
# an arm's cost and context statistics are read only when at least this
# share of its sessions passed turns 1-5 with no error
PASS_GATE = 7 / 8
TURNS_1_5, REUSE = range(1, 6), range(2, 6)


def verdict(lo: float, hi: float, predicted: str, margin: float | None = None) -> str:
    """predicted: '<0', '>0', or '~0'. Margin first for '~0'."""
    if math.isnan(lo) or math.isnan(hi):
        return "UNRESOLVED"
    if predicted == "~0":
        if margin is not None and -margin <= lo and hi <= margin:
            return "HOLDS"
        return "REVERSED" if lo > 0 or hi < 0 else "UNRESOLVED"
    if lo > 0:
        return "HOLDS" if predicted == ">0" else "REVERSED"
    if hi < 0:
        return "HOLDS" if predicted == "<0" else "REVERSED"
    return "UNRESOLVED"


def passed(session) -> bool:
    return not session.get("error") and bool(session["reuse_turns_correct"])


def _stat(session, field, turns):
    values = [t[field] for t in session["turns"] if t["turn"] in turns]
    return sum(values) if field == "cost_usd" else max(values)


def session_hypotheses(sessions):
    by_arm = {arm: [s for s in sessions if s["arm"] == arm] for arm in ARMS}
    gate = {
        arm: (sum(passed(s) for s in ss), len(ss)) for arm, ss in by_arm.items()
    }
    out = []

    def med_diff(label, a, b, field, turns, predicted):
        for arm in (a, b):
            ok, n = gate[arm]
            if not n:
                out.append((label, None, None, None, f"NOT RUN ({arm})"))
                return
            if ok < PASS_GATE * n:
                out.append((label, None, None, None, f"UNRESOLVED (gate: {arm} passed {ok}/{n})"))
                return
        # sessions that died have no turns: they count against the gate,
        # never in the statistic
        groups = {
            arm: [_stat(s, field, turns) for s in by_arm[arm] if not s.get("error")]
            for arm in (a, b)
        }
        point, lo, hi = bootstrap(
            groups, lambda g: statistics.median(g[a]) - statistics.median(g[b])
        )
        margin = EQUIVALENCE_MARGIN * statistics.median(groups[b])
        out.append((label, point, lo, hi, verdict(lo, hi, predicted, margin)))

    med_diff("P1 peak context, tp - direct (turns 1-5)", "toolplane", "direct",
             "peak_request_context_tokens", TURNS_1_5, "<0")
    med_diff("P2 session cost, tp - direct (turns 1-5)", "toolplane", "direct",
             "cost_usd", TURNS_1_5, "<0")
    med_diff("P3 reuse cost, tp - direct (turns 2-5; equivalence +/-10%)", "toolplane", "direct",
             "cost_usd", REUSE, "~0")
    med_diff("P4 reuse cost, tp - nosession (turns 2-5)", "toolplane", "toolplane_nosession",
             "cost_usd", REUSE, "<0")
    med_diff("P5 session cost, tp - nosession (turns 1-5)", "toolplane", "toolplane_nosession",
             "cost_usd", TURNS_1_5, "<0")
    return out, gate, by_arm


def slice_hypotheses(rows):
    out = []
    for label, task in (("S1 single, tp - direct $/pass", "single"),
                        ("S2 chain_prose, tp - direct $/pass", "chain_prose")):
        r = cost_diff(rows, "toolplane", MODEL, ">0", task=task)
        if r is None:
            out.append((label, None, None, None, "NOT RUN"))
            continue
        point, lo, hi, v, *_ = r
        out.append((label, point, lo, hi, v))
    return out


def void_reasons(rows, sessions) -> list[str]:
    reasons = []
    if any(x.get("git_dirty") for x in [*rows, *sessions]):
        reasons.append("git_dirty=true rows")
    versions = {x.get("client_version") for x in [*rows, *sessions]} - {None}
    if len(versions) > 1:
        reasons.append(f"rows span client versions {sorted(versions)}")
    models = {x.get("requested_model", x.get("model")) for x in [*rows, *sessions]}
    if models - {MODEL}:
        reasons.append(f"rows for other models {sorted(models - {MODEL})}")
    fillers = {s.get("filler", "repeat") for s in sessions}
    if fillers - {FILLER}:
        reasons.append(f"sessions with filler {sorted(fillers - {FILLER})}")
    return reasons


def _row(label, point, lo, hi, v):
    if point is None:
        return f"| {label} | — | — | {v} |"
    return f"| {label} | {_fmt(point)} | [{_fmt(lo)}, {_fmt(hi)}] | {v} |"


def report(rows, sessions) -> str:
    hyps, gate, by_arm = session_hypotheses(sessions)
    out = ["# Persistence follow-up: results", "", "## Hypotheses", "",
           "| id | point | 95% CI | verdict |", "|---|---|---|---|"]
    out += [_row(*h) for h in [*hyps, *slice_hypotheses(rows)]]
    out += ["", "## Sessions per arm (reported, not tested)", "",
            ("| arm | passed 1-5 / sessions | errors | median cost 1-5 | median peak ctx | "
             "reset verified | compaction events | reuse mechanism (turns 2-5) |"),
            "|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        ss = by_arm[arm]
        done = [s for s in ss if not s.get("error")]
        med = (lambda xs: f"{statistics.median(xs):.4f}" if xs else "—")
        out.append(
            f"| {arm} | {gate[arm][0]}/{gate[arm][1]} | {len(ss) - len(done)} "
            f"| {med([_stat(s, 'cost_usd', TURNS_1_5) for s in done])} "
            f"| {med([_stat(s, 'peak_request_context_tokens', TURNS_1_5) for s in done])} "
            f"| {sum(s.get('reset_verified') is True for s in ss)}/{len(ss)} "
            f"| {sum(t.get('compaction_events', 0) for s in done for t in s['turns'])} "
            f"| {_tally(t.get('reuse_mechanism', 'unrecorded') for s in done for t in s['turns'] if t['turn'] in REUSE)} |"
        )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", nargs="+", type=Path, required=True)
    parser.add_argument("--runs", nargs="*", type=Path, default=[])
    args = parser.parse_args()
    rows = [r for p in args.runs for r in json.loads(p.read_text())]
    sessions = [s for p in args.sessions for s in json.loads(p.read_text())["rows"]]
    reasons = void_reasons(rows, sessions)
    if reasons:
        print("VOID: " + "; ".join(reasons), file=sys.stderr)
        return 2
    print(report(rows, sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
