"""Tests for the criterion registry."""

from __future__ import annotations

import pytest

from uijudge.criteria import (
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
