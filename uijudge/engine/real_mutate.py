"""Apply the P2 mutation engine to *real* frozen pages via generic target selection.

The P2 mutators (:mod:`uijudge.engine.mutate`) pick targets by synthetic-page id prefixes
(``card-``, ``hero-text``, ...) that no real page has. This module keeps everything else
about the P2 pipeline identical — the same ``injection_record`` shape, the same
render-verifier (:mod:`uijudge.engine.verify`) that *measures* the claim and issues a
receipt, the same clean-twin negative controls, the same item builders
(:mod:`uijudge.engine.items`) — and only swaps in **generic, DOM-driven target selection**
over a frozen page's ``dom.json``.

Every frozen tier-A page carries stable ``uij-e*`` ids on all body elements (assigned at
freeze time), so a selector chosen here resolves identically on the frozen clean page and
on any mutated copy — which is exactly what the clean-twin control needs.

Only the subset of defect classes that generalise to arbitrary DOM are offered here:

    a11y:   contrast:degrade, alt:strip, alt:garble, heading:skip, target:shrink
    layout: clip:overflow, protrude:viewport

Classes that need synthetic-specific structure (card rows, a hero, a bound label/input
pair) are intentionally excluded rather than forced onto pages that lack that structure.
Real pages are messier, so more attempts fail the render-verifier; those are discarded and
counted, never silently kept — same contract as P2.
"""

from __future__ import annotations

import random
from typing import Any

from bs4 import BeautifulSoup

from .mutate import MutationError, MutationResult, _add_style, spec_for
from .wcag import parse_css_color, pick_color_for_ratio

# Defect classes this module can plant on an arbitrary real page.
REAL_CLASSES = (
    "contrast:degrade",
    "alt:strip",
    "alt:garble",
    "heading:skip",
    "target:shrink",
    "clip:overflow",
    "protrude:viewport",
)

_TEXT_TAGS = {
    "p",
    "li",
    "a",
    "span",
    "strong",
    "em",
    "td",
    "th",
    "dd",
    "dt",
    "label",
    "blockquote",
    "figcaption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}
_INTERACTIVE_TAGS = {"a", "button"}
_SEVERITY = "moderate"


def _rng(seed: int, salt: int) -> random.Random:
    """Deterministic per-(seed, defect) RNG (no builtin ``hash`` — it is process-salted)."""
    return random.Random(seed * 1_000_003 + salt + 7919)


def _elements(dom: dict) -> list[dict]:
    """Return recorded elements that carry a selector, in document order."""
    return [e for e in dom.get("captured", []) if e.get("selector")]


def applicable_classes(dom: dict, soup: BeautifulSoup) -> list[str]:
    """Return, in stable order, the real defect classes that have a candidate on this page."""
    out = []
    for dc in REAL_CLASSES:
        try:
            _candidates(dc, dom, soup)
            out.append(dc)
        except MutationError:
            continue
    return out


def _candidates(defect_class: str, dom: dict, soup: BeautifulSoup) -> list[dict]:
    """Return candidate elements (dom records) for ``defect_class`` or raise MutationError."""
    els = _elements(dom)
    if defect_class == "contrast:degrade":
        cands = [e for e in els if e["tag"] in _TEXT_TAGS and (e.get("text_prefix") or "").strip()]
    elif defect_class in ("alt:strip", "alt:garble"):
        cands = [e for e in els if e["tag"] == "img" and _img_has_alt(soup, e["selector"])]
    elif defect_class == "heading:skip":
        cands = [e for e in els if e["tag"] in _HEADING_TAGS]
    elif defect_class == "target:shrink":
        cands = [e for e in els if e["tag"] in _INTERACTIVE_TAGS and _area(e) >= 24 * 24]
    elif defect_class == "clip:overflow":
        cands = [
            e for e in els if e["tag"] in _TEXT_TAGS and _area(e) >= 24 * 60 and (e.get("text_prefix") or "").strip()
        ]
    elif defect_class == "protrude:viewport":
        cands = [
            e for e in els if e["tag"] in (_TEXT_TAGS | {"div", "section", "ul", "ol", "table"}) and _bbox(e)[2] >= 80
        ]
    else:
        raise MutationError(f"unsupported real defect class {defect_class!r}")
    if not cands:
        raise MutationError(f"no real candidate for {defect_class}")
    return cands


def _img_has_alt(soup: BeautifulSoup, selector: str) -> bool:
    """True if the img at ``selector`` currently has a non-empty alt attribute."""
    tag = soup.select_one(selector)
    return bool(tag and tag.has_attr("alt") and (tag.get("alt") or "").strip())


def _bbox(e: dict) -> list[int]:
    """Return the desktop bbox for a dom element record."""
    b = e.get("bbox") or {}
    return b.get("desktop") or [0, 0, 0, 0]


def _area(e: dict) -> int:
    """Return the desktop bbox area (px^2) for a dom element record."""
    x, y, w, h = _bbox(e)
    return w * h


def real_mutate(html: str, defect_class: str, seed: int, dom: dict) -> MutationResult:
    """Plant ``defect_class`` on a copy of a frozen real page; return the result + record.

    Reuses the P2 registry metadata (criterion code, track, verify viewports) via
    :func:`uijudge.engine.mutate.spec_for`, so the record is byte-compatible with the
    render-verifier. Target selection is generic (DOM-driven), keyed off the page's
    ``dom.json``.

    Raises:
        MutationError: If no suitable target element exists on the page.
    """
    spec = spec_for(defect_class)
    soup = BeautifulSoup(html, "html.parser")
    cands = _candidates(defect_class, dom, soup)
    rng = _rng(seed, spec.salt)
    target = rng.choice(cands)
    selector = target["selector"]
    tag = soup.select_one(selector)
    if tag is None:
        raise MutationError(f"selector {selector} vanished from soup")

    record: dict[str, Any] = _apply(defect_class, soup, tag, target, rng)
    record.update(
        {
            "selector": selector,
            "defect_class": defect_class,
            "criterion_code": spec.criterion_code,
            "track": spec.track,
            "severity": _SEVERITY,
            "seed": seed,
            "verify_viewports": list(spec.verify_viewports),
        }
    )
    return MutationResult(defect_class=defect_class, mutated_html=str(soup), injection_record=record)


def _apply(defect_class: str, soup: BeautifulSoup, tag: Any, target: dict, rng: random.Random) -> dict:
    """Apply the class-specific DOM/CSS edit and return the record's class-specific fields."""
    if defect_class == "contrast:degrade":
        target_ratio = 2.0
        bg = _element_background(target)
        r, g, b = pick_color_for_ratio(bg, target_ratio)
        prior = _add_style(tag, f"color: rgb({r},{g},{b})")
        return {
            "before": {"style": prior or "(stylesheet default)"},
            "after": {"color": f"rgb({r},{g},{b})"},
            "params": {"target_ratio": target_ratio, "background": f"rgb{bg}"},
        }

    if defect_class == "alt:strip":
        before = tag.get("alt")
        if "alt" in tag.attrs:
            del tag["alt"]
        return {"before": {"alt": before}, "after": {"alt": None}, "params": {}}

    if defect_class == "alt:garble":
        before = tag.get("alt")
        junk = rng.choice(["IMG_20240117_0932.jpg", "DSC_0042.JPG", "image1.png", "photo_final_v2.jpeg"])
        tag["alt"] = junk
        return {"before": {"alt": before}, "after": {"alt": junk}, "params": {"semantic": True}}

    if defect_class == "heading:skip":
        before = tag.name
        new_level = min(int(before[1]) + 2, 6)
        tag.name = f"h{new_level}"
        return {
            "before": {"tag": before},
            "after": {"tag": f"h{new_level}"},
            "params": {"skipped_level": f"h{int(before[1]) + 1}"},
        }

    if defect_class == "target:shrink":
        size = 12
        prior = _add_style(
            tag,
            f"min-width:0;min-height:0;width:{size}px;height:{size}px;padding:0;"
            f"font-size:8px;line-height:1;overflow:hidden;box-sizing:border-box;display:inline-block",
        )
        return {
            "before": {"style": prior or "(stylesheet default)"},
            "after": {"width": f"{size}px", "height": f"{size}px"},
            "params": {"threshold_px": 24},
        }

    if defect_class == "clip:overflow":
        height = 16
        prior = _add_style(tag, f"display:block;height:{height}px;overflow:hidden")
        return {
            "before": {"style": prior or "(none)"},
            "after": {"height": f"{height}px", "overflow": "hidden"},
            "params": {"clip_height_px": height},
        }

    if defect_class == "protrude:viewport":
        width = 3000
        prior = _add_style(tag, f"display:block;width:{width}px;max-width:none")
        return {
            "before": {"style": prior or "(none)"},
            "after": {"width": f"{width}px"},
            "params": {"forced_width_px": width},
        }

    raise MutationError(f"no edit for {defect_class}")


def _element_background(target: dict) -> tuple[int, int, int]:
    """Best-effort opaque background rgb for the target (from dom styles; white fallback)."""
    raw = (target.get("styles") or {}).get("background", "")
    try:
        rgb = parse_css_color(raw)
    except (ValueError, TypeError):
        return (255, 255, 255)
    # Transparent background -> the effective backdrop is unknown; assume the page's white.
    if raw.replace(" ", "").endswith(",0)") or "rgba(0,0,0,0)" in raw.replace(" ", ""):
        return (255, 255, 255)
    return rgb
