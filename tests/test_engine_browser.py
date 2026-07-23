"""Browser-marked golden tests for the render-verifier and the corpus builder.

These are the construct-validity spine of P2: for every mutation class the render-verifier
must (a) fire on the mutated page with a *measured* receipt and (b) NOT fire on the clean
control. The corpus-builder tests then prove determinism (byte-identical items across two
builds), schema-admissibility of every emitted item, that discarded mutations never reach
labels, and L4 true/false balance.

Run with: ``uv run pytest -m browser`` (requires ``playwright install chromium``).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from uijudge.engine import mutate as M
from uijudge.engine.synth import build_page_html
from uijudge.engine.verify import Verifier
from uijudge.schema import validate_item

pytestmark = pytest.mark.browser

_SEED = 1000


def test_every_mutation_class_verifies_and_clean_control_passes(tmp_path):
    """Each class: receipt fires on mutated page, returns None on the clean control."""
    clean = build_page_html(_SEED)

    async def run():
        results = {}
        async with Verifier() as v:
            for dc in M.registered_classes():
                res = M.mutate(clean, dc, "moderate", _SEED)
                mp = tmp_path / f"{dc.replace(':', '_')}.html"
                mp.write_text(res.mutated_html, encoding="utf-8")
                cp = tmp_path / f"{dc.replace(':', '_')}_clean.html"
                cp.write_text(clean, encoding="utf-8")
                receipt = await v.verify(mp, res.injection_record)
                control = await v.verify(cp, res.injection_record)
                results[dc] = (receipt, control)
        return results

    results = asyncio.run(run())
    for dc, (receipt, control) in results.items():
        assert receipt is not None, f"{dc}: mutation did not render-verify"
        assert receipt["verified"] is True
        assert receipt["measured"], f"{dc}: receipt carries no measured values"
        assert control is None, f"{dc}: clean control wrongly triggered the check"


def test_contrast_receipt_records_measured_ratio(tmp_path):
    """The contrast receipt must carry a measured ratio below the AA threshold."""
    clean = build_page_html(_SEED)
    res = M.mutate(clean, "contrast:degrade", "severe", _SEED)
    mp = tmp_path / "contrast.html"
    mp.write_text(res.mutated_html, encoding="utf-8")

    async def run():
        async with Verifier() as v:
            return await v.verify(mp, res.injection_record)

    receipt = asyncio.run(run())
    assert receipt is not None
    ratio = receipt["measured"]["contrast_ratio"]
    assert ratio < 4.5
    assert receipt["axe"]["rule"] == "color-contrast"
    assert receipt["axe"]["violation"] is True  # axe catches contrast (construct validity)


def _build_small(monkeypatch, tmp_path, seed_count=3):
    """Run a small deterministic build redirected into ``tmp_path``; return report + labels text."""
    from uijudge.engine import corpus_synth
    from uijudge.engine.ingest import _common

    corpus_dir = tmp_path / "corpus"
    labels_file = tmp_path / "labels" / "items.jsonl"
    reports_dir = tmp_path / "reports"
    (corpus_dir / "synthetic").mkdir(parents=True, exist_ok=True)
    labels_file.parent.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(_common, "CORPUS_DIR", corpus_dir)
    monkeypatch.setattr(_common, "LABELS_FILE", labels_file)
    monkeypatch.setattr(_common, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(corpus_synth, "CORPUS_DIR", corpus_dir)
    monkeypatch.setattr(corpus_synth, "REPORTS_DIR", reports_dir)

    report = asyncio.run(corpus_synth.build_corpus(seed_count=seed_count))
    labels_text = labels_file.read_text(encoding="utf-8")
    return report, labels_text, corpus_dir


def test_corpus_build_is_byte_deterministic(monkeypatch, tmp_path):
    """Two independent builds of the same seeds produce byte-identical labels."""
    r1, labels1, _ = _build_small(monkeypatch, tmp_path / "a", seed_count=3)
    r2, labels2, _ = _build_small(monkeypatch, tmp_path / "b", seed_count=3)
    assert labels1 == labels2
    assert r1["items_written"] == r2["items_written"]


def test_every_built_item_is_admissible_and_pages_exist(monkeypatch, tmp_path):
    """Every emitted item validates and references a page that is actually on disk."""
    report, labels_text, corpus_dir = _build_small(monkeypatch, tmp_path, seed_count=3)
    lines = [json.loads(x) for x in labels_text.splitlines() if x.strip()]
    assert lines
    on_disk = {p.parent.name for p in (corpus_dir / "synthetic").glob("*/page.html")}
    for raw in lines:
        validate_item(raw)  # raises on any inadmissible item
        assert raw["page_id"] in on_disk, f"item references missing page {raw['page_id']}"
    # discarded mutations are recorded but never produce a page or an item
    for d in report["mutations"]["per_class"].values():
        assert d["attempted"] == d["verified"] + d["discarded"]


def test_l4_true_false_balance(monkeypatch, tmp_path):
    """The emitted L4 batch is 40-60% true (balanced assertions)."""
    report, _, _ = _build_small(monkeypatch, tmp_path, seed_count=4)
    frac = report["l4_balance"]["true_fraction"]
    assert report["l4_balance"]["total"] >= 20
    assert 0.4 <= frac <= 0.6, f"L4 true fraction out of band: {frac}"
