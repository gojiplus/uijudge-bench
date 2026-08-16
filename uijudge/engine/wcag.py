"""WCAG contrast math — re-exported from layoutlens (single measurement source).

Since v0.2 the relative-luminance / contrast-ratio / CSS-color arithmetic comes from
``layoutlens.layout.contrast``, the productionized port of this repo's original
verifier math (see ``docs/SCORERS.md``): one implementation, verified by both
projects' test suites. ``tests/test_wcag.py`` still asserts the published WCAG
example pairs through this module, so a regression in the upstream math cannot land
here silently.

Only :func:`pick_color_for_ratio` stays local: it is a mutation *planting* helper
(solve the contrast formula for a foreground gray in a target band), not a
measurement, and layoutlens has no counterpart yet.
"""

from __future__ import annotations

from layoutlens.layout.contrast import (
    AA_NORMAL_TEXT,
    RGB,
    contrast_ratio,
    parse_css_color,
    relative_luminance,
)

__all__ = [
    "AA_NORMAL_TEXT",
    "RGB",
    "contrast_ratio",
    "parse_css_color",
    "pick_color_for_ratio",
    "relative_luminance",
]


def _luminance_to_gray(target_l: float) -> int:
    """Return the 8-bit gray value whose relative luminance is closest to ``target_l``."""
    target_l = max(0.0, min(1.0, target_l))
    # For a neutral gray every channel is equal, so luminance == linearized channel.
    s = target_l * 12.92 if target_l <= 0.0031308 else 1.055 * (target_l ** (1 / 2.4)) - 0.055
    return max(0, min(255, round(s * 255)))


def pick_color_for_ratio(bg: RGB, target_ratio: float) -> RGB:
    """Return a neutral-gray foreground whose contrast with ``bg`` is ~``target_ratio``.

    Used by the ``contrast:degrade`` mutation to plant a defect in a chosen ratio band.
    Assumes a light background (the darker foreground case); solves the contrast formula
    for the foreground luminance, then inverts luminance to an 8-bit gray. The mutation is
    always followed by a *measured* verification, so this only needs to land in the band.

    Args:
        bg: The effective background color.
        target_ratio: Desired contrast ratio (e.g. ``3.0``).

    Returns:
        An ``(r, g, b)`` gray foreground color.
    """
    l_bg = relative_luminance(bg)
    # (L_bg + 0.05) / (L_fg + 0.05) = ratio  ->  L_fg = (L_bg + 0.05) / ratio - 0.05
    l_fg = (l_bg + 0.05) / target_ratio - 0.05
    gray = _luminance_to_gray(l_fg)
    return (gray, gray, gray)
