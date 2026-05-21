"""Tests for reference_parser pure-function helpers.

These helpers (`get_ref_type`, `get_ref_number`) are designed to classify
text that has *already* been identified as a reference-section line — they
do NOT promise to reject arbitrary body text. So tests cover the contract
they actually fulfill: numbered-prefix recognition and number extraction
for the inputs the upstream pipeline will hand them.
"""

from __future__ import annotations

import pytest

from reference_parser import get_ref_number, get_ref_type


@pytest.mark.parametrize(
    "text",
    [
        "[1] Zhou P, et al. Title.",
        "[12] Smith J, 2020. Paper.",
        "(1) Author A. Title.",
        "1. Smith J, 2020. Title.",
        "1 Smith J et al, 2020. Title.",
    ],
)
def test_get_ref_type_detects_numbered_references(text: str) -> None:
    assert get_ref_type(text) != -1


def test_get_ref_type_rejects_text_without_letters_or_numbers() -> None:
    # The regex group set requires some recognisable token; the all-letter-
    # spaced gibberish below has no ref-like prefix at all.
    assert get_ref_type("q w e r t y u") == -1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[1] Zhou P et al.", 1),
        ("[12] Smith.", 12),
        ("(7) Author A.", 7),
        ("3. Smith J, 2020.", 3),
        ("42 Author B, 2021. Some paper.", 42),
    ],
)
def test_get_ref_number_parses_leading_number(text: str, expected: int) -> None:
    assert get_ref_number(text) == expected


def test_get_ref_number_returns_none_when_no_ref_prefix_recognised() -> None:
    # When get_ref_type returns -1, get_ref_number must return None.
    assert get_ref_number("q w e r t y u") is None
