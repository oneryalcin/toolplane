"""Deterministic analysis for the pre-registered confirmation matrix.

    python bench/analyze_prereg.py bench/results/run-A.json ... \
        [--longitudinal bench/results/longitudinal-X.json ...]

Reads committed result rows and prints every table, CI and hypothesis
verdict the plan (bench/PREREG-confirmation-2026-10.md) fixes. Nothing
here is tuned after seeing data: the hypotheses, the cells they read and
the decision rule are module constants, committed with the plan.

Decision rule: a hypothesis HOLDS when the 95% bootstrap CI of its
statistic excludes 0 in the predicted direction, is REVERSED when it
excludes 0 the other way, and is UNRESOLVED otherwise. "Similar" (~0)
predictions are equivalence tests: they HOLD only when the whole CI lies
within +/-10% of the reference arm's value (EQUIVALENCE_MARGIN), are
REVERSED when the CI excludes 0, and are UNRESOLVED otherwise -- so an
underpowered cell cannot pass by being noisy. Every bootstrap resamples each
group independently (arm order is counterbalanced by rep parity, not
paired), 4000 resamples, seed 0.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run import _cost_of_pass

RESAMPLES = 4000
SEED = 0
EQUIVALENCE_MARGIN = 0.10


# ---- cells -----------------------------------------------------------------


def cell(rows, *, task, arm, m=1, b=0, g="fetch-one", builtins="default", model=None):
    return [
        r
        for r in rows
        if r["task"] == task
        and r["arm"] == arm
        # rows older than the #117 axes lack these fields; their runs
        # were 0-byte fetch-one, which the defaults state
        and r.get("m_servers", 1) == m
        and r.get("record_bytes", 0) == b
        and r.get("granularity", "fetch-one") == g
        and r.get("builtins", "default") == builtins
        and (model is None or r.get("requested_model", r["model"]) == model)
    ]


def _runs(group):
    """(cost, passed) per run. A timeout's cost is unknown (the client never
    reported it); the registered rule imputes the cell's highest observed
    cost, so a timeout can only make its arm look worse, never cheaper."""
    known = [r["cost_usd"] for r in group if r["cost_usd"] is not None]
    fallback = max(known, default=0.0)
    return [
        (r["cost_usd"] if r["cost_usd"] is not None else fallback, bool(r["correct"]))
        for r in group
    ]



def bootstrap(groups: dict[str, list], stat: Callable[[dict[str, list]], float]):
    """Point estimate and 95% percentile CI, each group resampled alone.

    A resample with no passes in an arm prices that arm at inf; a draw that
    is undefined (inf - inf) is discarded. If more than 5% of draws are
    undefined, or the point estimate is, the CI is NaN and every verdict
    reads UNRESOLVED: too many all-failure resamples to say anything.
    """
    rng = random.Random(SEED)
    point = stat(groups)
    draws = [
        stat({k: rng.choices(v, k=len(v)) for k, v in groups.items()})
        for _ in range(RESAMPLES)
    ]
    defined = sorted(d for d in draws if not math.isnan(d))
    if math.isnan(point) or len(defined) < 0.95 * RESAMPLES:
        return point, math.nan, math.nan
    n = len(defined)
    return point, defined[int(0.025 * n)], defined[int(0.975 * n) - 1]


def verdict(lo: float, hi: float, predicted: str, margin: float | None = None) -> str:
    """predicted: '<0', '>0', or '~0' (equivalence within +/-margin)."""
    if predicted == "~0":
        if lo > 0 or hi < 0:
            return "REVERSED"
        return "HOLDS" if margin is not None and -margin <= lo and hi <= margin else "UNRESOLVED"
    if lo > 0:
        return "HOLDS" if predicted == ">0" else "REVERSED"
    if hi < 0:
        return "HOLDS" if predicted == "<0" else "REVERSED"
    return "UNRESOLVED"


# ---- hypotheses ------------------------------------------------------------


def cost_diff(rows, arm, model, predicted, **where):
    # toolplane_cli removes the shell by construction, so its rows record
    # restricted built-ins while its direct comparator keeps the default
    a = cell(rows, arm=arm, model=model,
             **({"builtins": "restricted"} | where if arm == "toolplane_cli" else where))
    d = cell(rows, arm="direct", model=model, **where)
    if not a or not d:
        return None
    # same statistic and draw order as run.py's _bootstrap_cost_of_pass_diff
    # (arm resampled before direct), so all-pass cells reproduce its CIs
    point, lo, hi = bootstrap(
        {"arm": _runs(a), "direct": _runs(d)},
        lambda g: _cost_of_pass(g["arm"]) - _cost_of_pass(g["direct"]),
    )
    return point, lo, hi, verdict(lo, hi, predicted), len(a), len(d)


def _slope(points: list[tuple[float, float]]) -> float:
    xs, ys = zip(*points, strict=True)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x in xs)


# N=30 is block B's 0-byte fetch-one `loop` cell: the same task, joined
_SCALE = {"loop5": 5, "loop20": 20, "loop": 30, "loop100": 100}


def scale_slope(rows, model, arms=("toolplane", "direct")):
    """$ per record, OLS of per-cell cost-of-pass on N. With two arms:
    slope(toolplane) - slope(direct), H2b's statistic (flatter than direct)."""
    groups = {
        f"{arm}:{t}": _runs(cell(rows, task=t, arm=arm, model=model))
        for arm in arms for t in _SCALE
    }
    if not all(groups.values()):
        return None

    def slope(g, arm):
        pts = [(_SCALE[t], _cost_of_pass(g[f"{arm}:{t}"])) for t in _SCALE]
        # a cell with no passing run has no cost per pass: the slope is
        # undefined (NaN -> UNRESOLVED), never an infinitely steep HOLDS
        if any(math.isinf(y) for _, y in pts):
            return math.nan
        return _slope(pts)

    if len(arms) == 1:
        return bootstrap(groups, lambda g: slope(g, arms[0]))
    return bootstrap(groups, lambda g: slope(g, arms[0]) - slope(g, arms[1]))


def payload_growth(rows, model):
    """(tp-direct at 20 KB) - (tp-direct at 0): <0 means the gap widens."""
    keys = [(arm, b) for arm in ("toolplane", "direct") for b in (0, 20000)]
    groups = {f"{a}@{b}": _runs(cell(rows, task="loop", arm=a, b=b, model=model)) for a, b in keys}
    if not all(groups.values()):
        return None

    def stat(g):
        c = {k: _cost_of_pass(v) for k, v in g.items()}
        return (c["toolplane@20000"] - c["direct@20000"]) - (c["toolplane@0"] - c["direct@0"])

    return bootstrap(groups, stat)


def wall_diff(rows, model, task):
    # every registered run counts, failures and timeouts included: a slow
    # failure is part of what an arm costs in wall time
    groups = {
        arm: [r["wall_s"] for r in cell(rows, task=task, arm=arm, model=model)]
        for arm in ("toolplane", "direct")
    }
    if not all(groups.values()):
        return None
    return bootstrap(groups, lambda g: statistics.median(g["toolplane"]) - statistics.median(g["direct"]))


def direct_m_effect(rows, model):
    """Direct cost-of-pass at M=15 minus M=1 (H8: deferred loading), with
    the equivalence margin: 10% of direct's M=1 cost-of-pass."""
    groups = {m: _runs(cell(rows, task="loop", arm="direct", m=m, model=model)) for m in (1, 15)}
    if not all(groups.values()):
        return None
    point, lo, hi = bootstrap(groups, lambda g: _cost_of_pass(g[15]) - _cost_of_pass(g[1]))
    margin = EQUIVALENCE_MARGIN * _cost_of_pass(groups[1])
    return point, lo, hi, verdict(lo, hi, "~0", margin)


def chain_hop_by_hop(group) -> int:
    """Runs that walked one order per call (H5's no-shortcut clause)."""
    walked = 0
    for r in group:
        names = r["tool_call_names"]
        executes = sum(n.endswith("execute_code") for n in names)
        fetches = sum(n.endswith("get_order") for n in names)
        walked += (executes >= 5) if r["arm"] != "direct" else (fetches >= 5)
    return walked


# ---- longitudinal (H9, O2) -------------------------------------------------


def _session_stat(sessions, field, turns):
    return [
        sum(t[field] for t in s["turns"] if t["turn"] in turns)
        if field == "cost_usd"
        else max(t[field] for t in s["turns"] if t["turn"] in turns)
        for s in sessions
    ]


def longitudinal(sessions):
    by_arm: dict[str, list] = {}
    for s in sessions:
        by_arm.setdefault(s["arm"], []).append(s)
    out = []

    def med_diff(a, b, field, turns, predicted, label):
        if a not in by_arm or b not in by_arm:
            return
        groups = {a: _session_stat(by_arm[a], field, turns), b: _session_stat(by_arm[b], field, turns)}
        point, lo, hi = bootstrap(groups, lambda g: statistics.median(g[a]) - statistics.median(g[b]))
        margin = EQUIVALENCE_MARGIN * statistics.median(groups[b])
        out.append((label, point, lo, hi, verdict(lo, hi, predicted, margin)))

    t15, reuse = range(1, 6), range(2, 6)
    med_diff("toolplane", "direct", "peak_request_context_tokens", t15, "<0", "H9a peak context, toolplane - direct (turns 1-5)")
    med_diff("toolplane", "direct", "cost_usd", reuse, "~0", "H9b reuse cost, toolplane - direct (turns 2-5)")
    med_diff("toolplane", "toolplane_nosession", "cost_usd", reuse, "<0", "O2a reuse cost, session - no session (turns 2-5)")
    med_diff("toolplane", "toolplane_nosession", "cost_usd", t15, "<0", "O2b five-turn cost, session - no session")
    mechanisms = {
        arm: _tally(t["reuse_mechanism"] for s in ss for t in s["turns"] if t["turn"] in reuse)
        for arm, ss in by_arm.items()
        if all("reuse_mechanism" in t for s in ss for t in s["turns"])
    }
    return out, mechanisms


def _tally(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


# ---- report ----------------------------------------------------------------


def _fmt(x: float) -> str:
    if math.isnan(x):
        return "undefined"
    return "inf" if math.isinf(x) else f"{x:+.4f}"


def report(rows, sessions, haiku: str, sonnet: str) -> str:
    lines = ["# Pre-registered confirmation matrix: results", ""]
    lines += ["## Hypotheses", "", "| id | statistic | n (arm/direct) | point | 95% CI | verdict |", "|---|---|---|---|---|---|"]

    def add(hid, label, res, n=""):
        if res is None:
            lines.append(f"| {hid} | {label} | — | — | — | NOT RUN |")
            return
        point, lo, hi, *rest = res
        v = rest[0] if rest and isinstance(rest[0], str) else ""
        if rest and len(rest) >= 3:
            n = f"{rest[1]}/{rest[2]}"
        lines.append(f"| {hid} | {label} | {n} | {_fmt(point)} | [{_fmt(lo)}, {_fmt(hi)}] | {v} |")

    def with_verdict(res, predicted):
        if res is None:
            return None
        point, lo, hi = res
        return point, lo, hi, verdict(lo, hi, predicted)

    m = haiku
    add("H1a", "single: tp - direct $/pass", cost_diff(rows, "toolplane", m, ">0", task="single"))
    add("H1b", "N=5: tp - direct $/pass", cost_diff(rows, "toolplane", m, ">0", task="loop5"))
    add("H2a", "N=100: tp - direct $/pass", cost_diff(rows, "toolplane", m, "<0", task="loop100"))
    add("H2b", "$/record slope over N=5..100: tp - direct", with_verdict(scale_slope(rows, m), "<0"))
    add("H2c", "direct $/record slope over N=5..100", with_verdict(scale_slope(rows, m, ("direct",)), ">0"))
    add("H2 info", "tp $/record slope (reported, not tested)", scale_slope(rows, m, ("toolplane",)))
    for b in (0, 2000, 20000):
        add(f"H3 b={b}", f"fetch-one {b} B: tp - direct", cost_diff(rows, "toolplane", m, "<0", task="loop", b=b))
    add("H3 growth", "gap at 20 KB minus gap at 0", with_verdict(payload_growth(rows, m), "<0"))
    for b in (0, 2000, 20000):
        add(f"H4 b={b}", f"bulk {b} B: tp - direct", cost_diff(rows, "toolplane", m, ">0", task="loop", b=b, g="bulk"))
    for t in ("chain", "chain_prose"):
        add(f"H5 {t}", f"{t}: tp - direct", cost_diff(rows, "toolplane", m, ">0", task=t))
    add("H6a", "latency: tp - direct median wall (s)", with_verdict(wall_diff(rows, m, "loop_lat100"), "<0"))
    h6b = cost_diff(rows, "toolplane", m, "<0", task="loop_lat100")
    if h6b and not math.isnan(h6b[1]):  # "not costlier": holds unless the CI is wholly above 0
        h6b = (*h6b[:3], "HOLDS" if h6b[1] <= 0 else "REVERSED", *h6b[4:])
    add("H6b", "latency: tp - direct $/pass (not costlier)", h6b)
    add("H7a", "refunds: tp_cli - direct", cost_diff(rows, "toolplane_cli", m, "<0", task="refunds"))
    add("H7b", "refunds: tp (shell) - direct", cost_diff(rows, "toolplane", m, ">0", task="refunds"))
    add("H8", "direct $/pass at M=15 minus M=1 (equivalence +/-10%)", direct_m_effect(rows, m))
    add("O3", "fetch-one 20 KB, no built-ins: tp - direct",
        cost_diff(rows, "toolplane", m, "<0", task="loop", b=20000, builtins="restricted"))
    add("S1", "Sonnet 5.5 single: tp - direct", cost_diff(rows, "toolplane", sonnet, ">0", task="single"))
    add("S2", "Sonnet 5.5 chain_prose: tp - direct", cost_diff(rows, "toolplane", sonnet, ">0", task="chain_prose"))

    lines += ["", "H5-walk (hop-by-hop runs / runs; HOLDS at >= 7/8 of runs per arm):", ""]
    for t in ("chain", "chain_prose"):
        for arm in ("direct", "toolplane"):
            g = cell(rows, task=t, arm=arm, model=m)
            if g:
                walked = chain_hop_by_hop(g)
                ok = "HOLDS" if walked >= 7 / 8 * len(g) else "FAILS"
                lines.append(f"- {t} {arm}: {walked}/{len(g)} {ok}")
    timeouts = _tally(
        "/".join(str(x) for x in (
            r.get("requested_model") or r["model"], r["task"], f"M{r.get('m_servers', 1)}",
            f"B{r.get('record_bytes', 0)}", r.get("granularity", "fetch-one"),
            r.get("builtins", "default"), r["arm"],
        ))
        for r in rows if r["cost_usd"] is None
    )
    if timeouts:
        lines += ["", f"Timeout costs imputed at the cell max (runs per cell): {json.dumps(timeouts, sort_keys=True)}"]

    if sessions:
        results, mechanisms = longitudinal(sessions)
        lines += ["", "## Longitudinal (H9, O2)", "", "| statistic | point | 95% CI | verdict |", "|---|---|---|---|"]
        lines += [f"| {label} | {_fmt(p)} | [{_fmt(lo)}, {_fmt(hi)}] | {v} |" for label, p, lo, hi, v in results]
        lines += ["", "Reuse-turn data source (turns 2-5): " + json.dumps(mechanisms, sort_keys=True)]

    lines += ["", "## Cells", "", cells_table(rows), "", "## Token classes (medians; the cost model's terms)", "", token_table(rows)]
    return "\n".join(lines)


def _cell_keys(rows):
    return sorted({
        (r.get("requested_model") or r["model"] or "?", r["task"], r.get("m_servers", 1), r.get("record_bytes", 0),
         r.get("granularity", "fetch-one"), r.get("builtins", "default"), r["arm"])
        for r in rows
    })


def cells_table(rows) -> str:
    out = ["| model | task | M | B | G | built-ins | arm | ok | $/pass | median wall s | median reqs | abstained |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for model, task, m, b, g, bi, arm in _cell_keys(rows):
        grp = cell(rows, task=task, arm=arm, m=m, b=b, g=g, builtins=bi, model=model)
        abstained = sum(r.get("tool_calls") == 0 for r in grp)
        out.append(
            f"| {model} | {task} | {m} | {b} | {g} | {bi} | {arm} | {sum(r['correct'] for r in grp)}/{len(grp)} "
            f"| {_cost_of_pass(_runs(grp)):.4f} | {statistics.median(r['wall_s'] for r in grp):.1f} "
            f"| {statistics.median(r.get('model_requests') or 0 for r in grp)} | {abstained} |"
        )
    return "\n".join(out)


def token_table(rows) -> str:
    out = ["| model | task | B | G | built-ins | arm | output | uncached input | cache read | peak context |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for model, task, m, b, g, bi, arm in _cell_keys(rows):
        if m != 1:
            continue
        grp = cell(rows, task=task, arm=arm, m=m, b=b, g=g, builtins=bi, model=model)

        def med(f, grp=grp):
            return statistics.median(f(r) for r in grp)

        out.append(
            f"| {model} | {task} | {b} | {g} | {bi} | {arm} | {med(lambda r: r['output_tokens']):.0f} "
            f"| {med(lambda r: r['uncached_input_tokens']):.0f} "
            f"| {med(lambda r: r['input_tokens'] - r['uncached_input_tokens']):.0f} "
            f"| {med(lambda r: r.get('peak_context_tokens') or 0):.0f} |"
        )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--longitudinal", nargs="*", type=Path, default=[])
    parser.add_argument("--haiku", default="claude-haiku-5-5")
    parser.add_argument("--sonnet", default="claude-sonnet-5-5")
    args = parser.parse_args()
    rows = [r for p in args.runs for r in json.loads(p.read_text())]
    sessions = [s for p in args.longitudinal for s in json.loads(p.read_text())["rows"]]
    dirty = sorted(
        {p.name for p in args.runs for r in json.loads(p.read_text()) if r.get("git_dirty")}
        | {p.name for p in args.longitudinal
           for s in json.loads(p.read_text())["rows"] if s.get("git_dirty")}
    )
    if dirty:
        print(f"VOID: git_dirty=true rows in {dirty} — the plan excludes them", file=sys.stderr)
        return 2
    # one frozen client: rows spanning versions mean it updated mid-matrix
    # (a timeout has no init event: an unknown version is not a second one)
    versions = sorted(
        ({r.get("client_version") for r in rows} | {x.get("client_version") for x in sessions})
        - {None}
    )
    if len(versions) > 1:
        print(f"VOID: rows span client versions {versions} — the plan freezes one", file=sys.stderr)
        return 2
    print(report(rows, sessions, args.haiku, args.sonnet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
