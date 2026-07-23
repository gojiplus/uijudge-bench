"""WCAG 2.x relative-luminance and contrast-ratio math (pure, browser-free).

This is the arithmetic the contrast render-verifier depends on. It is deliberately
free of Playwright so it can be unit-tested against published example pairs (see
``tests/test_wcag.py``); the verifier reads the *computed* foreground and effective
background colours off a live page and feeds them here.

Formulae are from the WCAG 2.1 definitions of *relative luminance* and *contrast ratio*:

- linearise each sRGB channel ``c8/255`` via the piecewise gamma expansion,
- ``L = 0.2126 R + 0.7152 G + 0.0722 B`` on the linearised channels,
- ``ratio = (L_light + 0.05) / (L_dark + 0.05)``.
"""

from __future__ import annotations

import re

RGB = tuple[int, int, int]

# WCAG AA contrast threshold for normal-size text (SC 1.4.3).
AA_NORMAL_TEXT = 4.5


def _linearize_channel(c8: int) -> float:
    """Linearise one 8-bit sRGB channel to its 0..1 linear-light value."""
    c = c8 / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: RGB) -> float:
    """Return the WCAG relative luminance (0..1) of an sRGB colour."""
    r, g, b = (_linearize_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(rgb1: RGB, rgb2: RGB) -> float:
    """Return the WCAG contrast ratio (1..21) between two sRGB colours (symmetric)."""
    l1 = relative_luminance(rgb1)
    l2 = relative_luminance(rgb2)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


_HEX3 = re.compile(r"^#([0-9a-fA-F]{3})$")
_HEX6 = re.compile(r"^#([0-9a-fA-F]{6})$")
_RGB = re.compile(r"^rgba?\(\s*([0-9.]+)[,\s]+([0-9.]+)[,\s]+([0-9.]+)")


def parse_css_color(value: str) -> RGB:
    """Parse a CSS colour string into an ``(r, g, b)`` tuple.

    Supports ``#rgb``, ``#rrggbb``, ``rgb(...)`` and ``rgba(...)`` (comma- or
    space-separated). Alpha is ignored — the verifier resolves the effective opaque
    background separately by walking the ancestor chain.

    Args:
        value: A CSS colour string.

    Returns:
        The ``(r, g, b)`` channels as integers 0..255.

    Raises:
        ValueError: If ``value`` is not a recognised colour form.
    """
    s = value.strip()
    m = _HEX6.match(s)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    m = _HEX3.match(s)
    if m:
        h = m.group(1)
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
    m = _RGB.match(s)
    if m:
        return (round(float(m.group(1))), round(float(m.group(2))), round(float(m.group(3))))
    raise ValueError(f"unrecognised CSS colour: {value!r}")


def _luminance_to_gray(target_l: float) -> int:
    """Return the 8-bit gray value whose relative luminance is closest to ``target_l``."""
    target_l = max(0.0, min(1.0, target_l))
    # For a neutral gray every channel is equal, so luminance == linearised channel.
    s = target_l * 12.92 if target_l <= 0.0031308 else 1.055 * (target_l ** (1 / 2.4)) - 0.055
    return max(0, min(255, round(s * 255)))


def pick_color_for_ratio(bg: RGB, target_ratio: float) -> RGB:
    """Return a neutral-gray foreground whose contrast with ``bg`` is ~``target_ratio``.

    Used by the ``contrast:degrade`` mutation to plant a defect in a chosen ratio band.
    Assumes a light background (the darker foreground case); solves the contrast formula
    for the foreground luminance, then inverts luminance to an 8-bit gray. The mutation is
    always followed by a *measured* verification, so this only needs to land in the band.

    Args:
        bg: The effective background colour.
        target_ratio: Desired contrast ratio (e.g. ``3.0``).

    Returns:
        An ``(r, g, b)`` gray foreground colour.
    """
    l_bg = relative_luminance(bg)
    # (L_bg + 0.05) / (L_fg + 0.05) = ratio  ->  L_fg = (L_bg + 0.05) / ratio - 0.05
    l_fg = (l_bg + 0.05) / target_ratio - 0.05
    gray = _luminance_to_gray(l_fg)
    return (gray, gray, gray)
