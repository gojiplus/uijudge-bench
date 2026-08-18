"""Tests for the criterion registry."""

from __future__ import annotations

import pytest

from uijudge.criteria import (
    WCAG_CONFORMANCE_LEVELS,
    WCAG_STANDARD_URI,
    WCAG_STANDARD_VERSION,
    WCAG_SUCCESS_CRITERIA,
    criterion_standard_metadata,
    is_valid_criterion,
    parse_criterion,
    wcag_axe_tag,
)


@pytest.mark.parametrize(
    "code",
    ["wcag:1.4.3", "wcag:4.1.2", "redecheck:element-collision", "style:text-align", "gds:colour-and-contrast"],
)
def test_known_criteria_are_valid(code):
    assert is_valid_criterion(code)


@pytest.mark.parametrize(
    "code",
    ["wcag:9.9.9", "redecheck:nope", "style:not-a-prop", "gds:unknown", "contrast", "wcag:", ":1.4.3", "wcag:1.4.3:x"],
)
def test_unknown_or_malformed_criteria_are_invalid(code):
    assert not is_valid_criterion(code)


def test_parse_criterion_roundtrip():
    assert parse_criterion("wcag:1.4.3") == ("wcag", "1.4.3")


def test_parse_criterion_rejects_bad_shape():
    with pytest.raises(ValueError):
        parse_criterion("nocolon")


def test_wcag_axe_tag_mapping():
    assert wcag_axe_tag("wcag:1.4.3") == "wcag143"
    assert wcag_axe_tag("wcag:4.1.2") == "wcag412"


def test_wcag_axe_tag_rejects_non_wcag():
    with pytest.raises(ValueError):
        wcag_axe_tag("redecheck:element-collision")


def test_wcag_registry_is_complete_and_locked_to_22():
    assert WCAG_STANDARD_VERSION == "2.2"
    assert WCAG_STANDARD_URI == "https://www.w3.org/TR/2024/REC-WCAG22-20241212/"
    assert set(WCAG_CONFORMANCE_LEVELS) == set(WCAG_SUCCESS_CRITERIA)
    assert len(WCAG_SUCCESS_CRITERIA) == 86
    assert "4.1.1" not in WCAG_SUCCESS_CRITERIA
    assert WCAG_SUCCESS_CRITERIA["2.4.12"] == "Focus Not Obscured (Enhanced)"
    assert WCAG_SUCCESS_CRITERIA["2.4.13"] == "Focus Appearance"
    assert WCAG_SUCCESS_CRITERIA["3.3.9"] == "Accessible Authentication (Enhanced)"


def test_wcag_standard_metadata_is_normative_and_not_applied_to_layout():
    assert criterion_standard_metadata("wcag:2.5.8") == {
        "title": "Web Content Accessibility Guidelines (WCAG) 2.2",
        "version": "2.2",
        "uri": WCAG_STANDARD_URI,
        "success_criterion": "2.5.8",
        "conformance_level": "AA",
    }
    assert criterion_standard_metadata("layout:occlusion") is None
