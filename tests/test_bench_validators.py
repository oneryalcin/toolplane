"""The bench validators gate every published correctness claim (#72).

The production bug each prevents: an over-accepting validator would let a
wrong agent answer into a public results table (or, as actually happened
pre-publication, an over-strict one marks correct answers wrong).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from run import _check_filter, _check_region_totals, _check_single  # noqa: E402


def _load_order_server(monkeypatch, granularity: str, record_bytes: int = 0):
    monkeypatch.setenv("BENCH_API_GRANULARITY", granularity)
    monkeypatch.setenv("BENCH_RECORD_BYTES", str(record_bytes))
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


def test_task_server_env_carries_payload_and_granularity() -> None:
    from run import task_server_env

    assert task_server_env("loop", 20000, "bulk") == {
        "BENCH_ORDERS_N": "30",
        "BENCH_API_GRANULARITY": "bulk",
        "BENCH_RECORD_BYTES": "20000",
    }


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
    direct = (transcripts / "single-direct-m1-rep1.jsonl").read_text()
    toolplane = (transcripts / "single-toolplane-m1-rep1.jsonl").read_text()
    assert _unique_request_ids(direct) == 3
    assert _unique_request_ids(toolplane) == 5
