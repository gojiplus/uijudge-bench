"""Offline ingestion unit tests (no network): outcome interleaving + annotation_unit.

These call ingest-module internals with mock upstream records, so they run with no
network and no browser.
"""

from __future__ import annotations

from uijudge.engine.ingest import act, gds
from uijudge.schema import validate_item


def _act_case(rule_id: str, tc_id: str, expected: str) -> dict:
    return {
        "ruleId": rule_id,
        "ruleName": "Some rule",
        "testcaseId": tc_id,
        "testcaseTitle": f"{expected} example",
        "expected": expected,
        "url": f"https://example.org/{rule_id}/{tc_id}.html",
        "rulePage": f"https://www.w3.org/WAI/.../{rule_id}/",
        "ruleAccessibilityRequirements": {
            "wcag20:1.4.3": {"forConformance": True, "failed": "not satisfied", "passed": "satisfied"}
        },
    }


def test_interleave_alternates_by_outcome():
    cases = [
        _act_case("r1", "f3", "failed"),
        _act_case("r1", "p2", "passed"),
        _act_case("r1", "f1", "failed"),
        _act_case("r1", "p1", "passed"),
        _act_case("r1", "f2", "failed"),
    ]
    interleaved = act._interleave_by_outcome(cases)
    outcomes = [c["expected"] for c in interleaved]
    assert outcomes == ["failed", "passed", "failed", "passed", "failed"]
    # within an outcome, ordering is by testcaseId (deterministic)
    assert [c["testcaseId"] for c in interleaved] == ["f1", "p1", "f2", "p2", "f3"]


def test_select_approaches_outcome_balance():
    # Two rules, each 4 failed + 2 passed. A limit of 8 should pull a balanced 4/4,
    # which the old (failed-before-passed) sort could not do.
    cases = []
    for rule in ("rA", "rB"):
        for i in range(4):
            cases.append(_act_case(rule, f"{rule}-f{i}", "failed"))
        for i in range(2):
            cases.append(_act_case(rule, f"{rule}-p{i}", "passed"))
    selected = act._select(cases, limit=8)
    passed = sum(1 for c in selected if c["expected"] == "passed")
    failed = sum(1 for c in selected if c["expected"] == "failed")
    assert (passed, failed) == (4, 4)


def test_act_build_item_is_page_unit_and_valid():
    case = _act_case("r1", "abc123", "failed")
    item = act._build_item(case, ["1.4.3"], "2026-07-22")
    assert item.annotation_unit == "page"
    assert item.anchor is None
    assert item.ground_truth == "no"  # failed -> does not satisfy
    validate_item(item.to_dict())  # must be admissible

    passed_item = act._build_item(_act_case("r1", "def456", "passed"), ["1.4.3"], "2026-07-22")
    assert passed_item.ground_truth == "yes"
    validate_item(passed_item.to_dict())


def test_gds_build_items_are_page_unit_and_valid():
    items = gds._build_items(
        case_name="Missing form label",
        snippet="<input type='text'>",
        slug="forms",
        display="Forms",
        results={"axe": "found"},
        page_id="gds-forms-00",
        date="2026-07-22",
    )
    assert [i.task_level for i in items] == ["L1", "L2"]
    for item in items:
        assert item.annotation_unit == "page"
        assert item.anchor is None
        validate_item(item.to_dict())
