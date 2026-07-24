"""LLMJudge tests — all offline. Two things are load-bearing here:

1. **Dry-run makes zero network calls.** We patch ``litellm.acompletion`` with a sentinel that
   raises on any invocation, then run the judge in dry-run mode and assert it was never called.
2. **The real path never hits the network in tests either** — a CannedLiteLLM mock replaces
   ``litellm.acompletion`` and returns fixed strings, so parse paths, refusal handling, order
   randomization, and N-run aggregation are all exercised without a paid API.
"""

from __future__ import annotations

import asyncio
from unittest import mock

from uijudge.constants import CANARY_GUID
from uijudge.harness.judges.llm import LLMJudge, parse_response
from uijudge.schema import validate_item


def _item(item_id, task_level, gt, *, anchor=None, question="Is it good?"):
    d = {
        "item_id": item_id,
        "page_id": "real-ada-home",  # a page that has committed screenshots
        "task_level": task_level,
        "track": "a11y" if task_level in ("L1", "L2", "L3") else "referring",
        "criterion_code": "wcag:1.4.3" if task_level != "L4" else "style:color",
        "question": question,
        "annotation_unit": "page" if task_level in ("L1", "L2") else "element",
        "anchor": anchor,
        "ground_truth": gt,
        "door": "mutation",
        "receipt": {"source": "x", "note": "y"},
        "evidence": "e",
        "split": "test",
        "canary": CANARY_GUID,
        "provenance": {"source": "handcrafted", "license": "MIT", "retrieval_date": "2026-07-22"},
    }
    return validate_item(d)


# --------------------------------------------------------------------------- parsing (pure)


def test_parse_strict_json_yes():
    out = parse_response('{"answer": "no", "confidence": 0.8, "rationale": "low contrast"}', "L1")
    assert out["answer"] == "no"
    assert out["confidence"] == 0.8
    assert out["refused"] is False


def test_parse_json_in_code_fence():
    out = parse_response('```json\n{"answer": "yes", "confidence": 0.9}\n```', "L4")
    assert out["answer"] == "yes"


def test_parse_yes_no_fallback_when_not_json():
    out = parse_response("After reviewing, my answer is: no.", "L1")
    assert out["answer"] == "no"
    assert out["confidence"] == 0.3  # fallback confidence


def test_parse_unparseable_is_ambiguous():
    out = parse_response("purple monkey dishwasher", "L1")
    assert out["answer"] == "unknown"
    assert out["refused"] is False


def test_parse_refusal_flagged():
    out = parse_response("I can't help with that request.", "L1")
    assert out["refused"] is True
    assert out["answer"] == "unknown"


def test_parse_l2_label_list():
    out = parse_response('{"answer": ["gds:buttons", "gds:links"], "confidence": 0.7}', "L2")
    assert out["answer"] == ["gds:buttons", "gds:links"]


def test_parse_design_ab():
    assert parse_response('{"answer": "B", "confidence": 0.6}', "design_pair")["answer"] == "B"


# --------------------------------------------------------------------------- dry-run: NO network


def test_dry_run_makes_zero_network_calls():
    """The safety proof: in dry-run mode litellm.acompletion must never be invoked."""
    items = [_item("a-L1", "L1", "no"), _item("b-L4", "L4", "yes", anchor={"selector": "#x"})]
    judge = LLMJudge(model="gpt-4o-mini", n_runs=3)

    sentinel = mock.MagicMock(side_effect=AssertionError("network call made during dry-run!"))
    with mock.patch("litellm.acompletion", sentinel):
        rows = asyncio.run(judge.run(items, dry_run=True))

    sentinel.assert_not_called()
    assert {r["item_id"] for r in rows} == {"a-L1", "b-L4"}
    assert all(r["dry_run"] for r in rows)
    assert all(r["planned_calls"] == 3 for r in rows)  # n_runs=3


def test_plan_enumerates_calls_without_network():
    items = [_item("a-L1", "L1", "no")]
    judge = LLMJudge(model="gpt-4o", n_runs=2)
    sentinel = mock.MagicMock(side_effect=AssertionError("network!"))
    with mock.patch("litellm.acompletion", sentinel):
        plan = judge.plan(items)
    sentinel.assert_not_called()
    assert len(plan) == 2  # one item x n_runs=2
    assert plan[0].model == "gpt-4o"


# --------------------------------------------------------------------------- real path with mock


class _CannedLiteLLM:
    """Replacement for litellm.acompletion returning fixed message strings in sequence."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return {"choices": [{"message": {"content": reply}}]}


def test_single_run_parses_mocked_response():
    item = _item("a-L1", "L1", "no")
    judge = LLMJudge(model="gpt-4o-mini", n_runs=1)
    canned = _CannedLiteLLM(['{"answer": "no", "confidence": 0.9}'])
    with mock.patch("litellm.acompletion", canned):
        rows = asyncio.run(judge.run([item]))
    assert canned.calls == 1
    assert rows[0]["answer"] == "no"
    assert rows[0]["confidence"] == 0.9
    assert rows[0]["judge"] == "llm:gpt-4o-mini:v1"


def test_n_run_aggregation_majority_and_agreement():
    item = _item("a-L1", "L1", "no")
    judge = LLMJudge(model="gpt-4o", n_runs=3)
    canned = _CannedLiteLLM(
        [
            '{"answer": "no", "confidence": 0.9}',
            '{"answer": "no", "confidence": 0.8}',
            '{"answer": "yes", "confidence": 0.6}',
        ]
    )
    with mock.patch("litellm.acompletion", canned):
        rows = asyncio.run(judge.run([item]))
    assert canned.calls == 3
    assert rows[0]["answer"] == "no"  # 2 of 3
    assert rows[0]["agreement"] == round(2 / 3, 4)
    assert len(rows[0]["runs"]) == 3


def test_refusal_recorded_on_row():
    item = _item("a-L1", "L1", "no")
    judge = LLMJudge(model="gpt-4o", n_runs=1)
    canned = _CannedLiteLLM(["I cannot help with that."])
    with mock.patch("litellm.acompletion", canned):
        rows = asyncio.run(judge.run([item]))
    assert rows[0]["refused"] is True
    assert rows[0]["answer"] == "unknown"


def test_design_pair_order_is_randomized_and_recorded():
    """Two members, order shuffled per (item, run) and recorded on each run row."""
    d = {
        "item_id": "dp-1",
        "page_id": "real-ada-home",
        "task_level": "design_pair",
        "track": "design",
        "criterion_code": "design:visual_hierarchy",
        "question": "Which is better?",
        "annotation_unit": "pair",
        "anchor": None,
        "ground_truth": "A",
        "door": "human",
        "receipt": {"source": "x", "n": 1},
        "evidence": "e",
        "split": "test",
        "canary": CANARY_GUID,
        "provenance": {"source": "handcrafted", "license": "MIT", "retrieval_date": "2026-07-22"},
        "metadata": {"pair_members": ["real-ada-home", "real-energy-home"]},
    }
    item = validate_item(d)
    judge = LLMJudge(model="gpt-4o", n_runs=1)
    # dry-run so no screenshots are read; we only check the recorded order.
    plan = judge.plan([item])
    assert set(plan[0].image_order) == {"real-ada-home", "real-energy-home"}
    # order is deterministic given (item_id, run_index)
    assert judge.plan([item])[0].image_order == plan[0].image_order
