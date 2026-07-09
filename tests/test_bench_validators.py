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
