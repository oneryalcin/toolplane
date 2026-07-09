"""The bench validators gate every published correctness claim (#72).

The production bug each prevents: an over-accepting validator would let a
wrong agent answer into a public results table (or, as actually happened
pre-publication, an over-strict one marks correct answers wrong).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from run import _check_filter, _check_region_totals, _check_single  # noqa: E402


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
    assert "ranges of the two arms overlap" in table


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
