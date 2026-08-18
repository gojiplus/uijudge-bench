"""Offline tests for the LayoutLens batch judge — no network, no key, no real batch.

The adapter (:class:`~uijudge.harness.judges.layoutlens_batch.LayoutLensBatchJudge`) delegates
the transport + resume to ``LayoutLens.judge_batch``; these tests mock that call to return canned
``{item_id: JudgeResult}`` and verify the bench's half of the contract:

    * the ``BatchRequest.prompt`` is ``build_prompt(item, variant)`` VERBATIM;
    * rows are byte-compatible with :class:`~uijudge.harness.judges.llm.LLMJudge` on identical raw
      responses (same normalization, keys, aggregation) — the bench normalizes ``JudgeResult.raw``
      with its own ``parse_response``, NOT ``JudgeResult.answer``;
    * a missing screenshot yields an unknown row WITHOUT calling ``judge_batch``;
    * usage/refused are carried and batch cost is the 50%-off rate.

LayoutLens is an optional dependency; the whole module skips cleanly when the v1.8 batch
interface is absent, so the core bench suite passes without the lib.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from PIL import Image

pytest.importorskip("layoutlens.api.batch")
pytest.importorskip("layoutlens.api.judge")

from layoutlens.api.judge import JudgeResult  # noqa: E402

from uijudge.harness.judges.layoutlens_batch import LayoutLensBatchJudge  # noqa: E402
from uijudge.harness.judges.llm import (  # noqa: E402
    LLMJudge,
    _item_render_state,
    _item_viewport,
    build_prompt,
)
from uijudge.harness.screenshot_contract import (  # noqa: E402
    CAPTURE_SCHEMA_VERSION,
    JUDGE_SCREENSHOT_VERSION,
    InstrumentValidityError,
    capture_key,
    capture_metadata_path,
    file_sha256,
    grounding_bbox,
    judge_screenshot_filename,
    vision_judge_eligibility,
)
from uijudge.labels import read_items  # noqa: E402
from uijudge.schema import Item  # noqa: E402
from uijudge.vendor.browser import resolve_viewport  # noqa: E402

_MODEL = "gemini/gemini-3-flash-preview"
_PRICE = {"input": 0.50, "output": 3.00, "batch_discount": 0.5}


def _item(level: str) -> Item:
    """Return one committed dev item at ``level``; tests derive their own valid screenshot."""
    return next(
        item
        for item in read_items()
        if item.task_level == level and item.split == "dev" and vision_judge_eligibility(item)[0]
    )


def _install_capture(corpus_root: Path, item: Item) -> Path:
    """Create a minimal valid judge-v1 PNG+sidecar bundle for ``item``."""
    page_dir = corpus_root / "synthetic" / item.page_id
    page_dir.mkdir(parents=True)
    source = page_dir / "page.html"
    source.write_text("<!doctype html><title>fixture</title>", encoding="utf-8")
    viewport_name = _item_viewport(item)
    viewport = resolve_viewport(viewport_name)
    bbox = grounding_bbox(item)
    assert bbox is not None
    bx, by, bw, bh = (float(value) for value in bbox)
    clip = [max(0.0, bx - 10), max(0.0, by - 10), bw + 20, bh + 20]
    width, height = round(clip[2]), round(clip[3])
    render_state = _item_render_state(item)
    image = page_dir / judge_screenshot_filename(item, viewport_name, render_state)
    Image.new("L", (width, height)).save(image)
    metadata = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "screenshot_contract": JUDGE_SCREENSHOT_VERSION,
        "capture_key": capture_key(item, viewport_name, render_state),
        "page_id": item.page_id,
        "viewport": viewport_name,
        "viewport_css_pixels": [viewport.width, viewport.height],
        "capture_mode": "target-crop",
        "screenshot_scale": "css",
        "render_state": render_state,
        "evidence_bbox_page_css": [bx, by, bw, bh],
        "source_clip_page_css": clip,
        "page_to_image_scale": [1.0, 1.0],
        "screenshot_pixels": [width, height],
        "source_html_sha256": file_sha256(source),
        "screenshot_sha256": file_sha256(image),
    }
    capture_metadata_path(image).write_text(json.dumps(metadata), encoding="utf-8")
    return image


def _jr(
    raw: str,
    *,
    answer: str = "yes",
    refused: bool = False,
    usage: dict | None = None,
    truncated: bool = False,
) -> JudgeResult:
    """A canned JudgeResult. ``answer`` is set DIFFERENT from raw's verdict on purpose, so a test
    can prove the bench normalizes ``raw`` (not ``JudgeResult.answer``)."""
    return JudgeResult(
        answer=answer,
        confidence=0.0,
        rationale="",
        raw=raw,
        refused=refused,
        usage=usage or {"prompt_tokens": 1200, "completion_tokens": 700, "total_tokens": 1900},
        model=_MODEL,
        parse_mode="json",
        truncated=truncated,
    )


class _FakeLens:
    """Stand-in for a LayoutLens instance: captures the requests, returns canned results."""

    def __init__(self, results: dict[str, JudgeResult]):
        self._results = results
        self.model = _MODEL
        self.captured: list | None = None
        self.captured_kwargs: dict = {}
        self.calls = 0

    async def judge_batch(self, requests, *, max_tokens=None, **kwargs):
        self.calls += 1
        self.captured = list(requests)
        self.captured_kwargs = {"max_tokens": max_tokens, **kwargs}
        return {r.id: self._results[r.id] for r in requests if r.id in self._results}


def test_batch_request_prompt_equals_build_prompt(monkeypatch, tmp_path):
    """Each BatchRequest carries build_prompt(item, variant) verbatim, keyed by item_id."""
    item = _item("L1")
    _install_capture(tmp_path, item)
    j = LayoutLensBatchJudge(model=_MODEL, prompt_version="v1", corpus_root=tmp_path)
    fake = _FakeLens({item.item_id: _jr('{"answer": "no", "confidence": 0.9, "rationale": "x"}')})
    monkeypatch.setattr(j, "_get_lens", lambda: fake)

    asyncio.run(j.run([item]))

    assert fake.captured is not None and len(fake.captured) == 1
    req = fake.captured[0]
    assert req.prompt == build_prompt(item, "v1")
    assert req.id == item.item_id
    assert str(req.image_path) == j._screenshot_for(item)


def test_rows_byte_compatible_with_llm_judge(monkeypatch, tmp_path):
    """On the same raw response the batch adapter's row matches LLMJudge's field-for-field."""
    item = _item("L1")
    _install_capture(tmp_path, item)
    raw = '{"answer": "no", "confidence": 0.9, "rationale": "x"}'

    llm = LLMJudge(model=_MODEL, prompt_version="v1", corpus_root=tmp_path)

    async def _fake_complete(_messages):
        return raw, {}

    monkeypatch.setattr(llm, "_complete", _fake_complete)
    llm_rows = asyncio.run(llm.run([item]))

    j = LayoutLensBatchJudge(model=_MODEL, prompt_version="v1", corpus_root=tmp_path)
    # JudgeResult.answer is "yes" (deliberately wrong) — the bench must normalize raw ("no").
    fake = _FakeLens({item.item_id: _jr(raw, answer="yes")})
    monkeypatch.setattr(j, "_get_lens", lambda: fake)
    batch_rows = asyncio.run(j.run([item]))

    lr, br = llm_rows[0], batch_rows[0]
    for k in (
        "item_id",
        "page_id",
        "task_level",
        "criterion_code",
        "answer",
        "confidence",
        "refused",
        "n_runs",
        "agreement",
    ):
        assert br[k] == lr[k], f"row field {k!r} diverged: {br[k]!r} != {lr[k]!r}"
    lrun, brun = lr["runs"][0], br["runs"][0]
    for k in ("answer", "confidence", "refused", "raw", "image_order"):
        assert brun[k] == lrun[k], f"run field {k!r} diverged: {brun[k]!r} != {lrun[k]!r}"
    assert br["answer"] == "no"  # parse_response(raw) wins over JudgeResult.answer="yes"


def test_missing_screenshot_fails_closed_without_calling_judge_batch(monkeypatch):
    """A missing screenshot rejects the paid run before a provider client is touched."""
    item = _item("L1")
    j = LayoutLensBatchJudge(model=_MODEL)
    monkeypatch.setattr(j, "_screenshot_for", lambda _it: None)

    fake = _FakeLens({})
    monkeypatch.setattr(j, "_get_lens", lambda: fake)

    with pytest.raises(InstrumentValidityError, match="refusing provider submission"):
        asyncio.run(j.run([item]))
    assert fake.calls == 0


def test_missing_result_yields_unknown(monkeypatch, tmp_path):
    """An item with no entry in the judge_batch result maps to an unknown row (no crash)."""
    item = _item("L1")
    _install_capture(tmp_path, item)
    j = LayoutLensBatchJudge(model=_MODEL, corpus_root=tmp_path)
    fake = _FakeLens({})  # judge_batch returns nothing for the id
    monkeypatch.setattr(j, "_get_lens", lambda: fake)

    rows = asyncio.run(j.run([item]))
    assert fake.calls == 1
    assert rows[0]["answer"] == "unknown"


def test_fresh_submission_intent_is_forwarded(monkeypatch, tmp_path):
    item = _item("L1")
    _install_capture(tmp_path, item)
    j = LayoutLensBatchJudge(model=_MODEL, max_tokens=16_000, resume=False, corpus_root=tmp_path)
    fake = _FakeLens({item.item_id: _jr('{"answer": "no", "confidence": 0.9, "rationale": "x"}')})
    monkeypatch.setattr(j, "_get_lens", lambda: fake)

    asyncio.run(j.run([item]))

    assert fake.captured_kwargs == {
        "max_tokens": 16_000,
        "resume": False,
        "reasoning_effort": None,
        "image_detail": "auto",
    }


def test_usage_and_truncation_carried_and_batch_cost(monkeypatch, tmp_path):
    """Usage and truncation are carried onto the run; cost uses the Batch rate."""
    item = _item("L1")
    _install_capture(tmp_path, item)
    j = LayoutLensBatchJudge(model=_MODEL, corpus_root=tmp_path)
    usage = {"prompt_tokens": 1200, "completion_tokens": 700, "total_tokens": 1900}
    fake = _FakeLens(
        {
            item.item_id: _jr(
                '{"answer": "no", "confidence": 0.9, "rationale": "x"}',
                usage=usage,
                truncated=True,
            )
        }
    )
    monkeypatch.setattr(j, "_get_lens", lambda: fake)

    rows = asyncio.run(j.run([item]))
    assert rows[0]["runs"][0]["usage"] == usage
    assert rows[0]["runs"][0]["truncated"] is True
    assert j.batch_cost_usd(rows, _PRICE) == round((1200 / 1e6 * 0.50 + 700 / 1e6 * 3.00) * 0.5, 4)


def test_batch_cost_is_half_of_standard():
    """1M in + 1M out at standard = $3.50; batch = $1.75."""
    j = LayoutLensBatchJudge()
    rows = [
        {"runs": [{"usage": {"prompt_tokens": 600_000, "completion_tokens": 400_000}}]},
        {"runs": [{"usage": {"prompt_tokens": 400_000, "completion_tokens": 600_000}}]},
    ]
    assert j.batch_usage_totals(rows) == {
        "measured_calls": 2,
        "prompt_tokens": 1_000_000,
        "completion_tokens": 1_000_000,
        "total_tokens": 2_000_000,
    }
    assert j.batch_cost_usd(rows, _PRICE) == pytest.approx(1.75)


def test_batch_cost_fails_closed_when_usage_is_missing():
    j = LayoutLensBatchJudge()
    rows = [{"runs": [{"answer": "no", "confidence": 1.0, "refused": False}]}]

    assert j.batch_cost_usd(rows, _PRICE) is None


def test_batch_cost_fails_closed_on_zero_usage():
    j = LayoutLensBatchJudge()
    rows = [{"runs": [{"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}]}]

    assert j.batch_usage_totals(rows) is None
    assert j.batch_cost_usd(rows, _PRICE) is None


def test_batch_output_dir_is_cwd_independent(monkeypatch, tmp_path):
    expected = LayoutLensBatchJudge().output_dir
    monkeypatch.chdir(tmp_path)

    judge = LayoutLensBatchJudge()

    assert judge.output_dir == expected
    assert Path(judge._get_lens().output_dir) == expected


def test_find_manifest_requires_one_exact_full_fingerprint(tmp_path):
    judge = LayoutLensBatchJudge(output_dir=tmp_path, max_tokens=16_000)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    exact = batch_dir / f"manifest_{'a' * 64}.json"
    exact.write_text(
        json.dumps(
            {
                "fingerprint": "a" * 64,
                "model": _MODEL,
                "backend": "genai",
                "max_tokens": 16_000,
                "reasoning_effort": None,
                "image_detail": "auto",
                "jobs": [{"job_name": "batches/x", "ids": ["A", "B"]}],
            }
        ),
        encoding="utf-8",
    )
    (batch_dir / "manifest_legacy.json").write_text("{}", encoding="utf-8")

    assert judge._find_manifest({"A", "B"}) == exact
    assert judge._find_manifest({"A"}) is None
