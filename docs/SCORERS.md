# Deterministic scorers: provenance and the LayoutLens adoption path

UIJudgeBench's ground truth is built by **deterministic scorers** — code that
*measures* whether a page has a defect, with no LLM in the loop — and issues a
receipt carrying the measured numbers. This note records where those scorers
live and how they relate to the [LayoutLens](https://github.com/gojiplus/layoutlens)
product, so future corpus builds have one source of truth instead of two drifting
copies.

## The two families of deterministic scorer

| Family | What it measures | Bench module | LayoutLens |
|---|---|---|---|
| **Accessibility (axe)** | axe-core WCAG rule violations | `uijudge/vendor/a11y.py` (`AxeAuditor`) | **owns it** — `layoutlens/a11y/` (the bench *vendored* it from LayoutLens; see `uijudge/vendor/NOTICE.md`) |
| **Geometry + contrast** | WCAG contrast ratio; sibling bbox overlap; `scrollHeight`/`clientHeight` clipping; right-edge-vs-viewport protrusion; target size; computed-style values | `uijudge/engine/verify.py`, `wcag.py`, `referring.py` | **owns it as of v1.9.0** — `layoutlens/layout/` (`LayoutScorer`, `check_contrast`, `read_computed_styles`) |

The a11y scorer already flows LayoutLens → bench. As of LayoutLens **v1.9.0**,
the geometry/contrast measurement math is also available as a first-class,
keyless product API (`layoutlens.layout`), ported from this repo's render-verifier
and generalised from *verifying one claimed selector* to *scanning a whole page*.

## Adoption path (v0.2 corpus builds, not v0.1.0)

- **The published v0.1.0 corpus, labels, and ground truth are frozen.** They were
  built and receipted by `uijudge/engine/verify.py` at v0.1.0 and are never
  regenerated or retroactively re-scored against a different implementation. Do not
  point the frozen corpus at `layoutlens.layout`.
- **Future (v0.2+) corpus builds** should import the geometry/contrast primitives
  from `layoutlens.layout` (`LayoutScorer`, `check_contrast`, `contrast_ratio`,
  `read_computed_styles`) rather than maintaining a second copy in
  `uijudge/engine/`, so the product and the benchmark share one measurement
  implementation. This is tracked as part of the v0.2 work (alongside the L2/L3
  instrument fixes); it is intentionally **not** done retroactively here.

The measurement math is identical on both sides by construction (the LayoutLens
port is verbatim), so a spot-check on any single page — `LayoutScorer` finding vs.
`verify.py` receipt — must agree.
