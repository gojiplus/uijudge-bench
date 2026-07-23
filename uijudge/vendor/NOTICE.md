# Vendored components — attribution

## Browser + accessibility machinery (from LayoutLens)

`uijudge/vendor/browser.py` and `uijudge/vendor/a11y.py` are derived from the
LayoutLens project:

- `layoutlens/browser.py` (`open_page`, viewport handling)
- `layoutlens/a11y/axe.py`, `layoutlens/a11y/types.py` (`AxeAuditor`, `A11yReport`)

LayoutLens is MIT-licensed (Copyright Gaurav Sood). Source:
https://github.com/gojiplus/layoutlens

These modules were **vendored** (copied and trimmed to self-contained form) rather than
imported as a dependency, to keep UIJudgeBench decoupled from LayoutLens's release
cadence and its heavier dependency set (openai, rich, pydantic, etc.). Behaviour is
unchanged; internal LayoutLens imports were inlined.

## axe-core

`uijudge/vendor/assets/axe.min.js` is axe-core 4.10.3 by Deque Systems, licensed under
the Mozilla Public License 2.0. Full license text: `assets/LICENSE-axe.txt`. Vendored
unmodified.
