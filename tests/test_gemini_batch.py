"""Offline tests for the Gemini Batch judge — no google-genai, no network, no key.

Cover the pure payload/scoring logic and the transport orchestration with a fake client, so
the batch path is verified without spending. The live ``google.genai`` import is lazy, so these
run even when the SDK is absent.
"""

from __future__ import annotations

import pytest

from uijudge.harness.judges.gemini_batch import GeminiBatchJudge
from uijudge.harness.judges.layoutlens_judge import LayoutLensJudge
from uijudge.labels import read_items
from uijudge.schema import Item


def _first(level: str) -> Item:
    return next(i for i in read_items() if i.task_level == level and i.split == "dev")


def test_prompt_payload_byte_identical_to_sync_judge():
    """The batch request's text part must equal what LayoutLensJudge sends verbatim."""
    item = _first("L1")
    batch = GeminiBatchJudge(prompt_version="v1")
    sync = LayoutLensJudge(model="gemini/gemini-3-flash-preview", prompt_version="v1")
    req = batch._inline_request_dict(item)
    assert req is not None
    sent_text = req["contents"][0]["parts"][0]["text"]
    assert sent_text == sync.build_prompt(item)
    assert req["metadata"] == {"item_id": item.item_id}
    # image is attached as inline base64 png
    inline = req["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "image/png" and inline["data"]


def test_usage_split_derives_output_from_total_minus_prompt():
    """Gemini bills thinking as output; billed output = total - prompt (>=0)."""

    class UM:
        prompt_token_count = 1274
        candidates_token_count = 56  # excludes thinking — must NOT be used as output
        total_token_count = 1948

    u = GeminiBatchJudge._usage_from(UM())
    assert u == {"prompt_tokens": 1274, "completion_tokens": 674, "total_tokens": 1948}
    assert GeminiBatchJudge._usage_from(None) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_batch_cost_is_half_of_standard():
    j = GeminiBatchJudge()
    rows = [{"runs": [{"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}}]}]
    # standard = 0.50 + 3.00 = 3.50; batch = 1.75
    assert j.batch_cost_usd(rows) == pytest.approx(1.75)


def test_chunking_respects_size_cap():
    j = GeminiBatchJudge()
    # three ~10MB payloads -> the cap (18MB) forces at least two chunks
    big = "x" * (10 * 1024 * 1024)
    payloads = []
    for _i in range(3):
        it = _first("L1")
        payloads.append((it, {"contents": [{"parts": [{"text": "t"}, {"inline_data": {"data": big}}]}]}))
    chunks = j._chunk(payloads)
    assert len(chunks) >= 2
    assert sum(len(c) for c in chunks) == 3


def test_run_maps_responses_by_metadata_with_fake_client(monkeypatch):
    """End-to-end orchestration with a fake genai client: responses keyed by metadata round-trip."""
    items = [_first("L1")]

    class _Resp:
        def __init__(self, text, prompt, total):
            self.text = text

            class _UM:
                prompt_token_count = prompt
                candidates_token_count = 10
                total_token_count = total

            self.usage_metadata = _UM()

    class _Inlined:
        def __init__(self, item_id, resp):
            self.metadata = {"item_id": item_id}
            self.response = resp
            self.error = None

    class _Dest:
        def __init__(self, inlined):
            self.inlined_responses = inlined

    class _Job:
        def __init__(self, name, dest):
            self.name = name
            self.state = "JOB_STATE_SUCCEEDED"
            self.dest = dest

    class _Batches:
        def __init__(self, outer):
            self._outer = outer

        def create(self, model, src, config):
            # capture the item_ids submitted for the fake response
            self._outer._submitted = [r.metadata["item_id"] for r in src]
            return _Job("batches/fake", None)

        def get(self, name):
            inlined = [
                _Inlined(iid, _Resp('{"answer": "no", "confidence": 0.9, "rationale": "x"}', 1200, 1900))
                for iid in self._outer._submitted
            ]
            return _Job(name, _Dest(inlined))

    class _FakeClient:
        def __init__(self):
            self.batches = _Batches(self)

    j = GeminiBatchJudge(prompt_version="v1", poll_interval=0.0)
    # _submit_chunk uses google.genai types — bypass it with a shim that skips the SDK wrap
    monkeypatch.setattr(j, "_client", lambda: _FakeClient())

    def _fake_submit(client, chunk):
        return client.batches.create(
            model=j.model, src=[type("R", (), {"metadata": {"item_id": it.item_id}})() for it, _ in chunk], config={}
        ).name

    monkeypatch.setattr(j, "_submit_chunk", _fake_submit)
    rows = j.run(items)
    assert len(rows) == 1
    assert rows[0]["answer"] == "no"
    assert rows[0]["runs"][0]["usage"]["completion_tokens"] == 700  # 1900 - 1200
    assert j.batch_cost_usd(rows) == round((1200 / 1e6 * 0.50 + 700 / 1e6 * 3.00) * 0.5, 4)


def test_missing_screenshot_yields_unknown_without_batch(monkeypatch):
    item = _first("L1")
    j = GeminiBatchJudge()
    monkeypatch.setattr(j, "_screenshot_for", lambda it: None)

    called = {"n": 0}
    monkeypatch.setattr(j, "_client", lambda: called.__setitem__("n", called["n"] + 1))
    rows = j.run([item])
    assert rows[0]["answer"] == "unknown"
    assert called["n"] == 1  # client built but no chunk submitted (payloads empty)
