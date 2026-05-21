"""Tests for the LLM publication-year coercion helper (Phase 7.6).

The LLM may return the year as int, str, or junk. _coerce_year is the
last-line defense against bad values reaching the DB.
"""

from __future__ import annotations

import pytest

from llm_analyzer import _coerce_year


@pytest.mark.parametrize(
    "raw,expected",
    [
        (2023, 2023),
        ("2023", 2023),
        ("Published 2023.", 2023),
        ("1999", 1999),
        (2024, 2024),
    ],
)
def test_coerce_year_accepts_plausible_years(raw, expected) -> None:
    assert _coerce_year(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "n/a",
        "unknown",
        1800,         # outside plausible range
        2200,         # outside plausible range
        True,         # bool → reject
        False,
        {"y": 2023},  # garbage shape
    ],
)
def test_coerce_year_rejects_invalid(raw) -> None:
    assert _coerce_year(raw) is None


def test_coerce_year_finds_year_in_mixed_string() -> None:
    assert _coerce_year("arXiv:2308.12345 (2023)") == 2023
    assert _coerce_year("Conference Proceedings, 2019.") == 2019


def test_coerce_year_rejects_garbage_with_no_year() -> None:
    assert _coerce_year("abc def") is None
    assert _coerce_year("year=42") is None
