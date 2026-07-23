"""TDD for the WCAG 2.x relative-luminance / contrast-ratio math.

Verified against published example pairs so the render-verifier's receipts rest on a
formula we can defend, not a black box. Reference values come from the WCAG 2.1
definition of contrast ratio and widely published checker outputs.
"""

from __future__ import annotations

import math

import pytest

from uijudge.engine.wcag import (
    contrast_ratio,
    parse_css_color,
    pick_color_for_ratio,
    relative_luminance,
)


def test_luminance_pure_black_and_white():
    assert relative_luminance((0, 0, 0)) == pytest.approx(0.0, abs=1e-9)
    assert relative_luminance((255, 255, 255)) == pytest.approx(1.0, abs=1e-9)


def test_black_on_white_is_21():
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0, abs=1e-3)


def test_ratio_is_symmetric():
    a = contrast_ratio((0x76, 0x76, 0x76), (255, 255, 255))
    b = contrast_ratio((255, 255, 255), (0x76, 0x76, 0x76))
    assert a == pytest.approx(b, abs=1e-9)


def test_767676_on_white_is_454():
    # #767676 on #FFFFFF is the canonical "just passes AA at 4.5" gray: 4.54:1.
    ratio = contrast_ratio((0x76, 0x76, 0x76), (0xFF, 0xFF, 0xFF))
    assert ratio == pytest.approx(4.54, abs=0.01)


def test_595959_on_white_is_7():
    # #595959 on white is the canonical AAA (7:1) gray: 7.00:1.
    ratio = contrast_ratio((0x59, 0x59, 0x59), (0xFF, 0xFF, 0xFF))
    assert ratio == pytest.approx(7.0, abs=0.02)


def test_777_on_white_below_aa():
    # #777777 on white is 4.48:1, just under the 4.5 AA threshold.
    ratio = contrast_ratio((0x77, 0x77, 0x77), (0xFF, 0xFF, 0xFF))
    assert ratio == pytest.approx(4.48, abs=0.02)
    assert ratio < 4.5


def test_parse_css_color_hex_and_rgb():
    assert parse_css_color("#767676") == (0x76, 0x76, 0x76)
    assert parse_css_color("#fff") == (255, 255, 255)
    assert parse_css_color("rgb(118, 118, 118)") == (118, 118, 118)
    assert parse_css_color("rgba(118, 118, 118, 1)") == (118, 118, 118)
    assert parse_css_color("rgb(255 255 255)") == (255, 255, 255)


def test_pick_color_for_ratio_hits_target_band():
    # Against a white background, a color chosen for a target ratio of 3.0 should
    # measure close to 3.0 and stay below the 4.5 AA threshold.
    bg = (255, 255, 255)
    for target in (2.0, 3.0, 4.0):
        fg = pick_color_for_ratio(bg, target)
        got = contrast_ratio(fg, bg)
        assert got == pytest.approx(target, abs=0.15)
        assert got < 4.5


def test_pick_color_is_deterministic():
    bg = (255, 255, 255)
    assert pick_color_for_ratio(bg, 3.0) == pick_color_for_ratio(bg, 3.0)


def test_luminance_matches_manual_formula():
    # Spot-check a mid gray against a hand-computed value.
    c = 118 / 255
    lin = ((c + 0.055) / 1.055) ** 2.4
    assert relative_luminance((118, 118, 118)) == pytest.approx(lin, abs=1e-9)
    assert not math.isnan(relative_luminance((118, 118, 118)))
