"""The bench validators gate every published correctness claim (#72).

The production bug each prevents: an over-accepting validator would let a
wrong agent answer into a public results table (or, as actually happened
pre-publication, an over-strict one marks correct answers wrong).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from run import _check_filter, _check_region_totals, _check_single


def _load_order_server(
    monkeypatch, granularity: str, record_bytes: int = 0, call_log: Path | None = None
):
    monkeypatch.setenv("BENCH_API_GRANULARITY", granularity)
    monkeypatch.setenv("BENCH_RECORD_BYTES", str(record_bytes))
    if call_log is None:
        monkeypatch.delenv("BENCH_CALL_LOG", raising=False)
    else:
        monkeypatch.setenv("BENCH_CALL_LOG", str(call_log))
    path = Path(__file__).resolve().parent.parent / "bench" / "order_server.py"
    spec = importlib.util.spec_from_file_location(
        f"bench_order_server_{granularity.replace('-', '_')}_{record_bytes}", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_order_server_granularity_profiles_are_mutually_exclusive(monkeypatch) -> None:
    from fastmcp import Client

    async def tool_names(app) -> list[str]:
        async with Client(app) as client:
            return sorted(tool.name for tool in await client.list_tools())

    fetch = _load_order_server(monkeypatch, "fetch-one")
    bulk = _load_order_server(monkeypatch, "bulk", record_bytes=2000)

    assert asyncio.run(tool_names(fetch.mcp)) == ["get_order", "list_order_ids"]
    assert asyncio.run(tool_names(bulk.mcp)) == ["get_orders"]
    records = asyncio.run(bulk.get_orders())
    assert len(records) == 30
    assert len(records[0]["detail"]) == 2000


def test_order_server_optional_call_log(monkeypatch, tmp_path: Path) -> None:
    log = tmp_path / "calls.jsonl"
    fetch = _load_order_server(monkeypatch, "fetch-one", call_log=log)

    assert asyncio.run(fetch.list_order_ids())[0] == "ORD-001"
    assert asyncio.run(fetch.get_order("ORD-017"))["order_id"] == "ORD-017"
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        {"tool": "list_order_ids", "params": {}},
        {"tool": "get_order", "params": {"order_id": "ORD-017"}},
    ]


def test_task_server_env_carries_payload_and_granularity() -> None:
    from run import task_server_env

    assert task_server_env("loop", 20000, "bulk") == {
        "BENCH_ORDERS_N": "30",
        "BENCH_API_GRANULARITY": "bulk",
        "BENCH_RECORD_BYTES": "20000",
    }


def test_payload_axes_reject_non_orders_tasks() -> None:
    from run import validate_axis_scope

    validate_axis_scope(["single_shipment"], [0], ["fetch-one"])

    with pytest.raises(ValueError, match="orders-domain"):
        validate_axis_scope(["single_shipment"], [0, 20_000], ["fetch-one"])
    with pytest.raises(ValueError, match="single_shipment"):
        validate_axis_scope(
            ["loop", "single_shipment"], [0], ["fetch-one", "bulk"]
        )


def test_mcp_config_wires_bulk_profile_to_both_arms(tmp_path: Path) -> None:
    from run import mcp_config

    code = {
        "fixtures_dir": str(tmp_path / "fixtures"),
        "python": "/frozen/python",
        "toolplane_bin": "/frozen/toolplane",
    }
    direct = mcp_config(
        "direct", tmp_path, "loop", 1, code, 2000, "bulk"
    )
    direct_env = direct["mcpServers"]["orders"]["env"]
    assert direct_env["BENCH_API_GRANULARITY"] == "bulk"
    assert direct_env["BENCH_RECORD_BYTES"] == "2000"

    toolplane = mcp_config(
        "toolplane", tmp_path, "loop", 1, code, 2000, "bulk"
    )
    config_arg = toolplane["mcpServers"]["toolplane"]["args"][-1]
    toml = Path(config_arg).read_text(encoding="utf-8")
    assert 'BENCH_API_GRANULARITY = "bulk"' in toml
    assert 'BENCH_RECORD_BYTES = "2000"' in toml


def test_region_totals_accepts_two_decimal_rendering() -> None:
    # the exact shape that the first, string-strict validator marked wrong
    assert _check_region_totals(
        "amer,4520.50\napac,4666.50\nemea,5043.50", 30
    )


def test_region_totals_accepts_plain_rendering() -> None:
    assert _check_region_totals("amer,4520.5\napac,4666.5\nemea,5043.5", 30)


def test_region_totals_rejects_wrong_value() -> None:
    assert not _check_region_totals(
        "amer,4520.51\napac,4666.5\nemea,5043.5", 30
    )


def test_region_totals_rejects_missing_region() -> None:
    assert not _check_region_totals("amer,4520.5\napac,4666.5", 30)


def test_region_totals_rejects_garbage_and_empty() -> None:
    assert not _check_region_totals("", 30)
    assert not _check_region_totals("the totals are as follows", 30)


def test_single_is_case_insensitive_and_strict() -> None:
    assert _check_single("Shipped", 30)
    assert not _check_single("pending", 30)
    assert not _check_single("", 30)


def test_filter_requires_exact_integer() -> None:
    assert _check_filter("5", 30)
    assert not _check_filter("4", 30)
    assert not _check_filter("five", 30)


def test_distractors_rejects_zero_and_negative_m() -> None:
    # a silently-empty distractor list would record rows labelled M=0
    # against a config that actually ran one server
    import pytest
    from run import distractors

    for bad in (0, -3):
        with pytest.raises(ValueError):
            distractors(bad)


def test_distractors_boundary_counts() -> None:
    from run import distractors

    assert distractors(1) == []
    assert len(distractors(15)) == 14
    import pytest

    with pytest.raises(ValueError):
        distractors(16)


def test_chain_validator_accepts_exact_and_case_insensitive() -> None:
    from run import _check_chain

    assert _check_chain("ORD-011,shipped", 30)
    assert _check_chain(" ord-011 , Shipped ", 30)


def test_chain_validator_rejects_decoy_endpoints() -> None:
    # the ids a template-guessing agent would land on must score as losses
    from run import _check_chain

    assert not _check_chain("ORD-019,shipped", 30)  # last hop's decoy
    assert not _check_chain("ORD-023,shipped", 30)  # one hop short
    assert not _check_chain("ORD-011,pending", 30)  # right id, wrong status
    assert not _check_chain("garbage", 30)


def test_summarize_flags_overlapping_ranges_and_prices_failures() -> None:
    from run import summarize

    def row(arm, cost, correct, task="loop"):
        return {
            "task": task,
            "arm": arm,
            "m_servers": 1,
            "correct": correct,
            "cost_usd": cost,
            "wall_s": 10.0,
            "tool_calls": 1,
            "num_turns": 1,
            "output_tokens": 1,
            "uncached_input_tokens": 1,
        }

    # overlapping cost ranges -> † on cost; one failure -> cost/pass above
    # median (total spend / successes)
    rows = [
        row("direct", 0.10, True),
        row("direct", 0.30, True),
        row("toolplane", 0.20, True),
        row("toolplane", 0.40, False),
    ]
    table = summarize(rows)
    line = next(ln for ln in table.splitlines() if "| toolplane |" in ln)
    assert "0.3†" in line  # median cost flagged as overlapping
    assert "| 0.6 |" in line  # cost/pass: (0.20+0.40)/1 success
    assert "overlaps direct" in table
    assert "noise" not in table  # observed overlap, not a noise conclusion


def test_summarize_three_arms_flags_against_direct_only() -> None:
    # #114 adds a hybrid arm; the table must render all three in order and
    # the † overlap mark is measured against direct (the reference), so
    # direct itself is never flagged
    from run import summarize

    def row(arm, cost, wall, task="single"):
        return {
            "task": task,
            "arm": arm,
            "m_servers": 1,
            "correct": True,
            "cost_usd": cost,
            "wall_s": wall,
            "model_requests": 3,
            "tool_calls": 2,
            "num_turns": 3,
            "output_tokens": 1,
            "uncached_input_tokens": 1,
        }

    rows = [
        row("direct", 0.10, 10.0),
        row("direct", 0.20, 11.0),
        row("toolplane", 0.50, 20.0),  # disjoint from direct on both axes
        row("toolplane", 0.60, 21.0),
        row("hybrid", 0.15, 10.5),  # overlaps direct on both axes -> †
        row("hybrid", 0.18, 10.8),
    ]
    table = summarize(rows)
    arm_lines = [ln for ln in table.splitlines() if ln.startswith("| single |")]
    assert [ln.split("|")[5].strip() for ln in arm_lines] == [
        "direct",
        "toolplane",
        "hybrid",
    ]
    direct_line = next(ln for ln in arm_lines if "| direct |" in ln)
    toolplane_line = next(ln for ln in arm_lines if "| toolplane |" in ln)
    hybrid_line = next(ln for ln in arm_lines if "| hybrid |" in ln)
    assert "†" not in direct_line  # the reference is never flagged
    assert "†" not in toolplane_line  # disjoint range
    assert "†" in hybrid_line  # overlaps direct


def test_summarize_keeps_granularity_cells_separate() -> None:
    from run import summarize

    rows = []
    for granularity, cost in (("fetch-one", 0.30), ("bulk", 0.10)):
        for arm in ("direct", "toolplane"):
            rows.append(
                {
                    "task": "loop",
                    "arm": arm,
                    "m_servers": 1,
                    "record_bytes": 2000,
                    "granularity": granularity,
                    "correct": True,
                    "cost_usd": cost,
                    "wall_s": 10.0,
                    "model_requests": 3,
                    "tool_calls": 2,
                    "num_turns": 3,
                    "output_tokens": 1,
                    "uncached_input_tokens": 1,
                }
            )

    table = summarize(rows)
    assert table.count("| loop | 1 | 2000 | fetch-one |") == 2
    assert table.count("| loop | 1 | 2000 | bulk |") == 2


def test_summarize_survives_all_timeout_arm_and_prices_unknown_as_na() -> None:
    # an arm whose reps all timed out has cost_usd=None everywhere; the
    # table must not crash (data is already on disk, but losing the
    # printed summary loses the run report) and a mixed cell with one
    # unknown cost must say n/a, not price the timeout as free
    from run import summarize

    def row(arm, cost, correct):
        return {
            "task": "loop",
            "arm": arm,
            "m_servers": 1,
            "correct": correct,
            "cost_usd": cost,
            "wall_s": None if cost is None else 10.0,
            "tool_calls": None if cost is None else 1,
            "num_turns": None,
            "output_tokens": 0,
            "uncached_input_tokens": 0,
        }

    all_timeout = summarize(
        [row("direct", 0.10, True), row("toolplane", None, False)]
    )
    assert "| inf |" in all_timeout or "| n/a |" in all_timeout

    mixed = summarize(
        [
            row("direct", 0.10, True),
            row("toolplane", 0.20, True),
            row("toolplane", None, False),
        ]
    )
    line = next(ln for ln in mixed.splitlines() if "| toolplane |" in ln)
    assert "| n/a |" in line


def test_summarize_no_flag_when_ranges_disjoint() -> None:
    from run import summarize

    def row(arm, cost):
        return {
            "task": "loop",
            "arm": arm,
            "m_servers": 1,
            "correct": True,
            "cost_usd": cost,
            "wall_s": 5.0 if arm == "direct" else 50.0,
            "tool_calls": 1,
            "num_turns": 1,
            "output_tokens": 1,
            "uncached_input_tokens": 1,
        }

    rows = [row("direct", 0.10), row("direct", 0.12),
            row("toolplane", 0.30), row("toolplane", 0.35)]
    table = summarize(rows)
    assert "†" not in table


def test_arm_order_counterbalances_deterministically() -> None:
    # a fixed direct-first order confounded arm with cache warmth (#116);
    # the alternation must be recomputable from the rep number alone
    from run import arm_order

    arms = ["direct", "toolplane"]
    assert arm_order(arms, 0) == ["direct", "toolplane"]
    assert arm_order(arms, 1) == ["toolplane", "direct"]
    assert arm_order(arms, 2) == ["direct", "toolplane"]
    # never mutates the input
    assert arms == ["direct", "toolplane"]


def test_unique_request_ids_counts_model_requests_not_events() -> None:
    # the metric that exposed client-side double discovery (#115): several
    # assistant events share one API request; non-assistant events carry
    # no request_id at all
    import json

    from run import _unique_request_ids

    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "request_id": "req_A"},
        {"type": "assistant", "request_id": "req_A"},
        {"type": "user"},
        {"type": "assistant", "request_id": "req_B"},
        {"type": "result", "subtype": "success"},
    ]
    stdout = "\n".join(json.dumps(e) for e in events) + "\nnot json\n"
    assert _unique_request_ids(stdout) == 2


def test_unique_request_ids_matches_committed_transcripts() -> None:
    # pin the metric to the published #111 run the external review counted
    # by hand: single = 3 direct / 5 toolplane
    from pathlib import Path

    from run import _unique_request_ids

    transcripts = (
        Path(__file__).resolve().parent.parent
        / "bench/results/transcripts/run-20260709-221840"
    )
    if not transcripts.is_dir():
        # the sdist ships bench code but not bench/results run data
        pytest.skip("committed bench transcripts not present (sdist)")
    direct = (transcripts / "single-direct-m1-rep1.jsonl").read_text()
    toolplane = (transcripts / "single-toolplane-m1-rep1.jsonl").read_text()
    assert _unique_request_ids(direct) == 3
    assert _unique_request_ids(toolplane) == 5


def test_bootstrap_prices_failures_in_so_a_cheap_wrong_answer_never_wins() -> None:
    # #116: a raw-spend bootstrap called toolplane "resolved cheaper" at N=5
    # on the 0.5.0 re-run purely because its failed runs were cheap; the CI
    # must be over cost-of-pass (spend / correct runs), like the table
    from run import _bootstrap_cost_of_pass_diff

    direct = [(0.10, True)] * 4
    cheap_failure = [(0.05, False), (0.11, True), (0.11, True), (0.11, True)]

    point, low, _ = _bootstrap_cost_of_pass_diff(cheap_failure, direct)

    assert point == pytest.approx((0.05 + 3 * 0.11) / 3 - 0.10)
    assert low > -1e-9  # never "resolved cheaper"


def test_codex_server_flags_use_bare_keys() -> None:
    # a quoted -c key (mcp_servers."orders") puts the quotes in the server
    # name; without required=true the server silently never starts and the
    # agent answers "unknown" (#113) — emit bare keys, refuse non-bare names
    from run import _codex_mcp_flags

    flags = _codex_mcp_flags(
        {"crm-eu": {"command": "/py", "args": ["s.py"], "env": {"N": "30"}}}
    )
    keys = {f.split("=", 1)[0] for f in flags if f != "-c"}

    assert keys == {
        f"mcp_servers.crm-eu.{k}"
        for k in ("command", "args", "env", "default_tools_approval_mode", "required")
    }
    with pytest.raises(ValueError):
        _codex_mcp_flags({"bad name": {"command": "/py"}})


def test_codex_rollout_facts_tell_code_mode_from_direct_calls(tmp_path) -> None:
    # the Codex write-up's central distinction rests on this classifier:
    # `exec` = Codex-native code mode, namespaced function_calls = plain
    # per-tool calls (gpt-5.5); one token_count per model request (#113)
    import json as _json

    from run import _codex_rollout_facts

    def rollout(thread, payloads):
        path = tmp_path / "sessions" / "2026" / f"rollout-x-{thread}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(_json.dumps({"payload": p}) for p in payloads))

    def usage(n):
        return {"type": "token_count", "info": {"last_token_usage": {"input_tokens": n}}}

    rollout("t-code", [usage(9000), {"type": "custom_tool_call", "name": "exec"}, usage(12000)])
    rollout("t-direct", [usage(8000), {"type": "function_call", "namespace": "mcp__orders", "name": "get_order"}])

    code = _codex_rollout_facts(tmp_path, '{"type":"thread.started","thread_id":"t-code"}')
    direct = _codex_rollout_facts(tmp_path, '{"type":"thread.started","thread_id":"t-direct"}')

    assert (code["model_call_names"], code["model_requests"], code["peak_context_tokens"]) == (["exec"], 2, 12000)
    assert direct["model_call_names"] == ["mcp__orders__get_order"]


def test_codex_rollout_facts_survive_a_truncated_line(tmp_path) -> None:
    # a timeout-killed Codex can leave a half-written last line; raising
    # there aborted the matrix and lost every row collected so far (#113)
    from run import _codex_rollout_facts

    path = tmp_path / "sessions" / "rollout-x-t-cut.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"payload": {"type": "custom_tool_call", "name": "exec"}}\n{"payload": {"ty')

    facts = _codex_rollout_facts(tmp_path, '{"type":"thread.started","thread_id":"t-cut"}')

    assert facts["model_call_names"] == ["exec"]


# --- #113 item 3: the prose chain must stay code-resistant. The bug this
# prevents: an edit that makes a note keyword-separable again would let a
# one-snippet regex walk the chain, and the "code mode loses on adaptive
# tasks" finding would again rest on what agents choose, not on the task.

_ORD = re.compile(r"ORD-\d{3}")
_NEG = re.compile(
    r"\b(not|no|do not|don't|disregard|mistakenly|refunded|closed|"
    r"duplicate|cancel\w*|old|unrelated|swapped)\b",
    re.IGNORECASE,
)
_KW = re.compile(
    r"\b(continue\w*|next|goes|moves|follow\w*|use|process|supersedes?|stands)\b",
    re.IGNORECASE,
)


def _mentions(note: str, me: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group()) for m in _ORD.finditer(note) if m.group() != me]


def _first(note, me):
    found = _mentions(note, me)
    return found[0][1] if found else None


def _last(note, me):
    found = _mentions(note, me)
    return found[-1][1] if found else None


def _nearest_keyword(note, me):
    found, kws = _mentions(note, me), [m.start() for m in _KW.finditer(note)]
    if not found or not kws:
        return None
    return min(found, key=lambda t: min(abs(t[0] - k) for k in kws))[1]


def _first_not_negated(note, me):
    for pos, oid in _mentions(note, me):
        if not _NEG.search(note[max(0, pos - 40) : pos]):
            return oid
    return None


def _successor_of_last(note, me):
    oid = _last(note, me)
    return f"ORD-{int(oid[4:]) + 1:03d}" if oid else None


@pytest.mark.parametrize(
    "heuristic",
    [_first, _last, _nearest_keyword, _first_not_negated, _successor_of_last],
)
def test_prose_chain_defeats_named_heuristics(heuristic) -> None:
    from orders_data import PROSE_PATH, prose_chain_notes

    notes = prose_chain_notes(30)
    hits = [
        heuristic(notes[PROSE_PATH[k]], PROSE_PATH[k]) == PROSE_PATH[k + 1]
        for k in range(len(PROSE_PATH) - 1)
    ]
    assert sum(hits) <= 2, f"{heuristic.__name__} gets {hits}"


def test_prose_chain_has_no_terminal_marker() -> None:
    # the templated chain's "final order" note let fetch-everything skip
    # every hop; here every order has a note and none flags the end
    from orders_data import prose_chain_notes

    notes = prose_chain_notes(30)
    assert len(notes) == 30
    marker = re.compile(r"\bfinal\b|\blast order\b|\bend of\b", re.IGNORECASE)
    assert not [n for n in notes.values() if marker.search(n)]


def test_chain_prose_validator_wants_the_fourth_hop() -> None:
    from run import _check_chain_prose

    assert _check_chain_prose("ORD-011,shipped", 30)
    assert not _check_chain_prose("ORD-014,shipped", 30)  # one hop too far
    assert not _check_chain_prose("ORD-023,shipped", 30)  # one hop short


# --- #113 item 2: the CLI + MCP join


def test_refund_total_needs_the_refund_filter() -> None:
    # every order id in the log is the tempting wrong set
    from orders_data import MIXED_N, mixed_commits, orders
    from run import _check_refund_total

    amounts = {o["order_id"]: o["amount"] for o in orders(MIXED_N)}
    every_id = {oid for msg in mixed_commits() for oid in _ORD.findall(msg)}
    assert not _check_refund_total(f"{sum(amounts[o] for o in every_id):.2f}", MIXED_N)
    assert _check_refund_total("5959.80", MIXED_N)
    assert _check_refund_total("5959.8", MIXED_N)


def test_seeded_git_history_is_byte_identical(tmp_path: Path) -> None:
    # transcripts across runs and arms must see the same hashes
    import subprocess

    from orders_data import mixed_commits
    from run import seed_git_repo

    heads = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        seed_git_repo(tmp_path / name, mixed_commits())
        heads.append(
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=tmp_path / name,
                capture_output=True, text=True, check=True,
            ).stdout
        )
    assert heads[0] == heads[1]


def test_cli_tasks_refuse_lanes_without_a_direct_shell() -> None:
    from run import validate_cli_scope

    validate_cli_scope(["refunds"], "claude", False)
    for client, restrict in (("codex", False), ("claude", True)):
        with pytest.raises(ValueError):
            validate_cli_scope(["refunds"], client, restrict)


def test_cli_section_only_on_cli_tasks(tmp_path: Path) -> None:
    from run import mcp_config

    code = {"fixtures_dir": str(tmp_path), "python": "py", "toolplane_bin": "tp"}
    for task, want in (("refunds", True), ("loop", False)):
        mcp_config("toolplane", tmp_path, task, 1, code)
        toml = next(tmp_path.glob(f"toolplane-bench-toolplane-{task}-*.toml")).read_text()
        assert ('[cli]\nmode = "allowlist"\nallow = ["git"]' in toml) is want


# --- the pre-registered matrix (bench/PREREG-confirmation-2026-10.md)


def test_prereg_verdicts_follow_the_committed_rule() -> None:
    # a mis-coded rule would publish wrong verdicts for every hypothesis
    from analyze_prereg import verdict

    assert verdict(-0.3, -0.1, "<0") == "HOLDS"
    assert verdict(0.1, 0.3, "<0") == "REVERSED"
    assert verdict(-0.1, 0.1, "<0") == "UNRESOLVED"


def test_prereg_similarity_needs_the_equivalence_margin() -> None:
    # a noisy, wide CI must not pass a "no difference" hypothesis
    from analyze_prereg import verdict

    assert verdict(-0.01, 0.01, "~0", margin=0.02) == "HOLDS"
    assert verdict(-0.05, 0.05, "~0", margin=0.02) == "UNRESOLVED"
    assert verdict(0.01, 0.05, "~0", margin=0.02) == "REVERSED"


def test_nosession_arm_turns_sessions_off(tmp_path: Path) -> None:
    # the O2 arm silently running with sessions on would measure nothing
    import tomllib

    from run import mcp_config

    from toolplane.config import load_toolplane_config

    code = {"fixtures_dir": str(tmp_path), "python": "py", "toolplane_bin": "tp"}
    for arm, enabled in (("toolplane_nosession", False), ("toolplane", True)):
        mcp_config(arm, tmp_path, "loop", 1, code)
        toml = next(tmp_path.glob(f"toolplane-bench-{arm}-loop-*.toml"))
        config = load_toolplane_config(tomllib.loads(toml.read_text()))
        assert config.session.enabled is enabled


def test_prereg_counts_timeouts_as_costly_failures() -> None:
    # a timeout row has model=None; dropping it would hide a failure and
    # flatter its arm's cost per correct answer
    from analyze_prereg import _runs, cell

    rows = [
        {"task": "loop", "arm": "toolplane", "model": "claude-haiku-5-5",
         "requested_model": "claude-haiku-5-5", "cost_usd": 0.02, "correct": True},
        {"task": "loop", "arm": "toolplane", "model": None,
         "requested_model": "claude-haiku-5-5", "cost_usd": None, "correct": False},
    ]
    group = cell(rows, task="loop", arm="toolplane", model="claude-haiku-5-5")
    assert _runs(group) == [(0.02, True), (0.02, False)]


def test_prereg_all_failure_resamples_read_unresolved() -> None:
    # inf - inf draws must not sort into an inverted interval
    from analyze_prereg import _cost_of_pass, bootstrap, verdict

    groups = {"arm": [(0.1, True), (0.1, False)], "direct": [(0.1, True), (0.1, False)]}
    _, lo, hi = bootstrap(
        groups, lambda g: _cost_of_pass(g["arm"]) - _cost_of_pass(g["direct"])
    )
    assert verdict(lo, hi, "<0") == "UNRESOLVED"


def test_prereg_voids_dirty_longitudinal_rows(tmp_path: Path, monkeypatch) -> None:
    import analyze_prereg

    runs = tmp_path / "run.json"
    runs.write_text(json.dumps([]))
    sessions = tmp_path / "longitudinal.json"
    sessions.write_text(json.dumps({"rows": [{"arm": "toolplane", "git_dirty": True, "turns": []}]}))
    monkeypatch.setattr(sys, "argv", ["analyze", str(runs), "--longitudinal", str(sessions)])
    assert analyze_prereg.main() == 2


def test_prereg_slope_with_an_all_failure_cell_is_unresolved() -> None:
    # an infinite cost-per-pass cell must not read as an infinitely steep HOLDS
    from analyze_prereg import scale_slope, verdict

    rows = [
        {"task": t, "arm": "direct", "model": "m", "cost_usd": 0.1 * n,
         "correct": t != "loop5"}
        for t, n in (("loop5", 5), ("loop20", 20), ("loop", 30), ("loop100", 100))
        for _ in range(4)
    ]
    _, lo, hi = scale_slope(rows, "m", ("direct",))
    assert verdict(lo, hi, ">0") == "UNRESOLVED"


def test_prereg_wall_time_counts_slow_failures() -> None:
    # dropping failed runs would hide a slow arm behind its fast passes
    from analyze_prereg import verdict, wall_diff

    def row(arm, wall, ok):
        return {"task": "loop_lat100", "arm": arm, "model": "m", "wall_s": wall, "correct": ok}

    rows = [row("toolplane", 1, True)] * 2 + [row("toolplane", 100, False)] * 2 + [
        row("direct", 10, True)
    ] * 4
    _, lo, hi = wall_diff(rows, "m", "loop_lat100")
    assert verdict(lo, hi, "<0") != "HOLDS"


def test_prereg_report_survives_a_timeout_row() -> None:
    # model=None on a timeout must not crash the registered report
    from analyze_prereg import report

    base = {"task": "single", "m_servers": 1, "record_bytes": 0, "granularity": "fetch-one",
            "builtins": "default", "requested_model": "m", "wall_s": 9.0, "tool_calls": 3,
            "tool_call_names": [], "model_requests": 3, "input_tokens": 10,
            "uncached_input_tokens": 5, "output_tokens": 2, "peak_context_tokens": 10}
    rows = [
        {**base, "arm": "direct", "model": "m", "cost_usd": 0.01, "correct": True},
        {**base, "arm": "toolplane", "model": "m", "cost_usd": 0.02, "correct": True},
        {**base, "arm": "toolplane", "model": None, "cost_usd": None, "correct": False},
    ]
    assert "| H1a |" in report(rows, [], "m", "s")


def test_prereg_lists_every_imputed_timeout() -> None:
    # the registration promises every imputation is listed, per cell
    from analyze_prereg import report

    base = {"task": "single", "m_servers": 1, "record_bytes": 0, "granularity": "fetch-one",
            "builtins": "default", "requested_model": "m", "wall_s": 9.0, "tool_calls": 3,
            "tool_call_names": [], "model_requests": 3, "input_tokens": 10,
            "uncached_input_tokens": 5, "output_tokens": 2, "peak_context_tokens": 10}
    timeout = {**base, "arm": "toolplane", "model": None, "cost_usd": None, "correct": False}
    rows = [
        {**base, "arm": "direct", "model": "m", "cost_usd": 0.01, "correct": True},
        {**base, "arm": "toolplane", "model": "m", "cost_usd": 0.02, "correct": True},
        timeout, timeout,
    ]
    assert '"m/single/M1/B0/fetch-one/default/toolplane": 2' in report(rows, [], "m", "s")


def test_prereg_voids_a_matrix_spanning_client_versions(tmp_path: Path, monkeypatch) -> None:
    # Claude Code self-updates; a matrix straddling an update is not frozen
    import analyze_prereg

    runs = tmp_path / "run.json"
    runs.write_text(json.dumps([
        {"client_version": "2.1.295", "git_dirty": False},
        {"client_version": "2.1.296", "git_dirty": False},
        {"client_version": None, "git_dirty": False},
    ]))
    monkeypatch.setattr(sys, "argv", ["analyze", str(runs)])
    assert analyze_prereg.main() == 2


def test_prereg_timeout_beside_a_completed_run_is_not_void(tmp_path: Path, monkeypatch) -> None:
    # a timeout's unknown version must not read as a second client version
    import analyze_prereg

    runs = tmp_path / "run.json"
    runs.write_text(json.dumps([
        {"client_version": "2.1.296", "git_dirty": False},
        {"client_version": None, "git_dirty": False},
    ]))
    monkeypatch.setattr(sys, "argv", ["analyze", str(runs)])
    monkeypatch.setattr(analyze_prereg, "report", lambda *a: "ok")
    assert analyze_prereg.main() == 0


# ---- persistence follow-up (PREREG-persistence-2026-10) --------------------


def test_persistence_equivalence_checks_the_margin_first() -> None:
    # the confirmation matrix's H8: a precise +3% effect inside the 10%
    # margin was reported REVERSED because "excludes 0" was checked first
    from analyze_persistence import verdict

    assert verdict(0.00002, 0.00034, "~0", margin=0.00066) == "HOLDS"


def test_persistence_equivalence_noisy_cell_does_not_pass() -> None:
    from analyze_persistence import verdict

    assert verdict(-0.0086, 0.0208, "~0", margin=0.01) == "UNRESOLVED"


def _session(arm, cost, ok=True, error=None):
    turns = [] if error else [
        {"turn": t, "cost_usd": cost, "peak_request_context_tokens": 1000, "correct": ok,
         "reuse_mechanism": "retained", "compaction_events": 0}
        for t in range(1, 7)
    ]
    return {"arm": arm, "turns": turns, "reuse_turns_correct": ok and not error,
            "error": error, "reset_verified": True, "exit_code": 0}


def test_persistence_gate_blocks_a_verdict_read_off_survivors() -> None:
    # an arm that failed 2 of 8 sessions would otherwise be priced on its
    # 6 survivors and could "win" by failing
    from analyze_persistence import session_hypotheses

    sessions = [_session("direct", 0.05) for _ in range(8)] + [
        _session("toolplane", 0.01, ok=i >= 2) for i in range(8)
    ]
    hyps, _, _ = session_hypotheses(sessions)
    p2 = next(h for h in hyps if h[0].startswith("P2"))
    assert p2[4].startswith("UNRESOLVED (gate")


def test_persistence_died_session_counts_against_gate_without_crashing() -> None:
    from analyze_persistence import report

    sessions = [_session("direct", 0.05) for _ in range(8)] + [
        _session("toolplane", 0.01, error="TimeoutError: turn 1 timed out")
    ] + [_session("toolplane", 0.01) for _ in range(7)]
    assert "| toolplane | 7/8 | 1 |" in report([], sessions)


def test_persistence_voids_sessions_on_the_old_filler(tmp_path: Path, monkeypatch) -> None:
    import analyze_persistence

    path = tmp_path / "longitudinal.json"
    path.write_text(json.dumps({"rows": [
        {**_session("direct", 0.05), "requested_model": "claude-sonnet-5-5", "filler": "repeat"}
    ]}))
    monkeypatch.setattr(sys, "argv", ["analyze", "--sessions", str(path)])
    assert analyze_persistence.main() == 2


def test_order_server_serves_the_varied_filler(monkeypatch) -> None:
    from orders_data import orders

    monkeypatch.setenv("BENCH_FILLER", "varied")
    server = _load_order_server(monkeypatch, "fetch-one", record_bytes=2000)
    expected = orders(30, record_bytes=2000, filler="varied")[0]["detail"]
    assert server._BY_ID["ORD-001"]["detail"] == expected


def test_longitudinal_keeps_other_sessions_when_one_dies(tmp_path: Path, monkeypatch) -> None:
    # the results file is written once at the end: an uncaught turn
    # timeout used to discard every session already run
    import longitudinal

    (tmp_path / "results").mkdir()
    monkeypatch.setattr(longitudinal, "BENCH_DIR", tmp_path)
    monkeypatch.setattr(longitudinal.base, "build_code_under_test", lambda wd: {"python": sys.executable})
    monkeypatch.setattr(longitudinal.base, "provenance_row", lambda code: {"git_dirty": False})
    monkeypatch.setattr(longitudinal.base, "_sha256", lambda p: "x")
    monkeypatch.setattr(
        longitudinal.subprocess, "run",
        lambda *a, **k: type("P", (), {"stdout": "[]"})(),
    )

    def fake_session(arm, *args):
        if arm == "toolplane":
            raise TimeoutError("turn 1 timed out")
        return {**_session(arm, 0.05), "all_correct": True, "total_cost_usd": 0.3,
                "peak_context_tokens": 1, "total_fixture_calls": 0}

    monkeypatch.setattr(longitudinal, "run_session", fake_session)
    monkeypatch.setattr(sys, "argv", ["longitudinal", "--arms", "direct,toolplane", "--filler", "varied"])
    assert longitudinal.main() == 1
    (result,) = (tmp_path / "results").glob("longitudinal-*.json")
    rows = json.loads(result.read_text())["rows"]
    assert [(r["arm"], bool(r.get("error"))) for r in rows] == [("direct", False), ("toolplane", True)]


def _verdict_of(sessions, prefix):
    from analyze_persistence import session_hypotheses

    hyps, _, _ = session_hypotheses(sessions)
    return next(h for h in hyps if h[0].startswith(prefix))[4]


def test_persistence_short_arm_gets_no_verdict() -> None:
    # 7 of 7 passing sessions must not stand in for the registered 8
    sessions = [_session("direct", 0.05) for _ in range(8)] + [
        _session("toolplane", 0.01) for _ in range(7)
    ]
    assert _verdict_of(sessions, "P2").startswith("UNRESOLVED (incomplete")


def test_persistence_a_death_cannot_decide_a_verdict(monkeypatch) -> None:
    # direct's costs straddle toolplane's: where its one died session lands
    # decides the median, so the verdict must not stand. The bootstrap is
    # stubbed to a zero-width CI at the point so only the imputation rule
    # is under test.
    import analyze_persistence

    def point_ci(groups, stat):
        x = stat(groups)
        return x, x, x

    monkeypatch.setattr(analyze_persistence, "bootstrap", point_ci)
    sessions = [_session("toolplane", 0.05 / 5) for _ in range(8)] + [
        _session("direct", c / 5) for c in (0.02, 0.02, 0.02, 0.02, 0.10, 0.10, 0.10)
    ] + [_session("direct", 0, error="TimeoutError")]
    assert _verdict_of(sessions, "P2").startswith("UNRESOLVED (depends")


def test_persistence_nonzero_exit_fails_the_gate() -> None:
    from analyze_persistence import passed

    assert not passed({**_session("toolplane", 0.01), "exit_code": 1})


def test_persistence_voids_rows_from_different_code() -> None:
    from analyze_persistence import void_reasons

    sessions = [{**_session("direct", 0.05), "git_sha": sha, "requested_model": "claude-sonnet-5-5",
                 "filler": "varied"} for sha in ("aaa", "bbb")]
    assert any("git_sha" in r for r in void_reasons([], sessions))


def test_persistence_provenance_check_reads_real_rows() -> None:
    # real rows carry fixtures_sha256 as a dict; synthetic rows hid a crash
    from analyze_persistence import void_reasons

    path = Path(__file__).resolve().parent.parent / "bench/results/run-20261010-005346.json"
    rows = [{**r, "requested_model": "claude-sonnet-5-5"} for r in json.loads(path.read_text())]
    assert isinstance(rows[0]["fixtures_sha256"], dict)
    assert void_reasons(rows, []) == []


def test_persistence_voids_rows_missing_provenance() -> None:
    from analyze_persistence import void_reasons

    session = {**_session("direct", 0.05), "requested_model": "claude-sonnet-5-5", "filler": "varied"}
    assert any("missing git_sha" in r for r in void_reasons([], [session]))


def _store_session(share_turns, error=None):
    s = _session("toolplane_nosession", 0.01, error=error)
    for t in s["turns"]:
        if 2 <= t["turn"] <= 5:
            t["reuse_mechanism"] = "result_store" if t["turn"] - 1 <= share_turns else "refetch"
    return s


def test_store_teaching_voids_sessions_run_on_the_original_prompt() -> None:
    # the original turn-1 prompt steers toward a session variable; rows
    # from it must not answer whether the description alone teaches the store
    from analyze_store import store_void_reasons

    row = {**_store_session(4), "filler": "varied", "requested_model": "claude-sonnet-5-5",
           "turn1": "sessions"}
    assert any("turn1" in r for r in store_void_reasons([row], row.get("git_sha")))


def test_store_teaching_voids_rows_from_another_commit() -> None:
    # uniform rows from an old or reverted description must not get a verdict
    from analyze_store import store_void_reasons

    row = {**_store_session(4), "filler": "varied", "requested_model": "claude-sonnet-5-5",
           "turn1": "neutral", "git_sha": "old"}
    assert any("registered" in r for r in store_void_reasons([row], "merge"))


def test_store_teaching_a_death_cannot_decide_t1(monkeypatch) -> None:
    # 4 of 7 survivors used the store: share 0.5 or 0.625 depending on the
    # died session, which straddles the majority line
    import analyze_store

    monkeypatch.setattr(analyze_store, "bootstrap", lambda g, stat: (stat(g),) * 3)
    sessions = [_session("toolplane", 0.01) for _ in range(8)] + [
        _store_session(4 if i < 4 else 0) for i in range(7)
    ] + [_store_session(0, error="TimeoutError")]
    hyps, _ = analyze_store.hypotheses(sessions)
    assert hyps[0][4].startswith("UNRESOLVED (depends")
