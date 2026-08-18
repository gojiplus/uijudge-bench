"""Construct-coverage gates for the complete WCAG 2.2 matrix."""

from __future__ import annotations

import json
from pathlib import Path

from uijudge.criteria import WCAG_SUCCESS_CRITERIA
from uijudge.labels import read_items
from uijudge.standards.report import render_markdown
from uijudge.standards.wcag22 import BEHAVIORAL_TESTS, COVERAGE_STATUSES, build_wcag22_coverage


def test_committed_corpus_has_complete_reasoned_wcag22_matrix():
    report = build_wcag22_coverage(read_items())

    assert len(report["criteria"]) == 86
    assert {row["criterion"] for row in report["criteria"]} == set(WCAG_SUCCESS_CRITERIA)
    assert set(report["counts"]) == set(COVERAGE_STATUSES)
    assert sum(report["counts"].values()) == 86
    for row in report["criteria"]:
        assert row["status"] in COVERAGE_STATUSES
        assert row["reason"].strip()


def test_covered_status_requires_pair_and_all_behavioral_tests():
    report = build_wcag22_coverage(read_items())
    for row in report["criteria"]:
        if row["status"] != "covered":
            continue
        assert row["evidence"]["paired_oracle_families"]
        assert row["evidence"]["covered_oracle_families"]
        assert all(row["evidence"]["behavioral_tests"].values())
        cases = row["evidence"]["behavioral_test_cases"]
        for family in row["evidence"]["covered_oracle_families"]:
            assert set(cases[family]) == set(BEHAVIORAL_TESTS)
            for node_id in cases[family].values():
                path_text, test_name = node_id.split("::", 1)
                source = Path(path_text)
                assert source.is_file()
                assert f"def {test_name}(" in source.read_text(encoding="utf-8")


def test_target_size_is_covered_only_by_the_exception_aware_oracle():
    report = build_wcag22_coverage(read_items())
    target = next(row for row in report["criteria"] if row["criterion"] == "2.5.8")

    assert target["status"] == "covered"
    assert target["evidence"]["exception_aware_target_size_oracle"] is True
    assert target["evidence"]["covered_oracle_families"] == ["target:shrink"]


def test_focus_not_obscured_is_covered_only_with_an_observable_focus_state():
    report = build_wcag22_coverage(read_items())
    focus = next(row for row in report["criteria"] if row["criterion"] == "2.4.11")

    assert focus["status"] == "covered"
    assert focus["evidence"]["covered_oracle_families"] == ["focus:obscure"]
    assert focus["evidence"]["observable_render_states"]["focus:obscure"] > 0


def test_markdown_is_rendered_from_the_canonical_matrix():
    report = build_wcag22_coverage(read_items())
    markdown = render_markdown(report)

    assert "# Web Content Accessibility Guidelines (WCAG) 2.2 construct coverage" in markdown
    assert markdown.count("| 2.5.8 Target Size (Minimum) |") == 1


def test_committed_reports_match_the_corpus_and_renderer():
    """The human report cannot drift from its machine-readable source or current labels."""
    report = build_wcag22_coverage(read_items())
    committed_json = json.loads(Path("reports/wcag22_coverage.json").read_text(encoding="utf-8"))
    committed_markdown = Path("reports/wcag22_coverage.md").read_text(encoding="utf-8")

    assert committed_json == report
    assert committed_markdown == render_markdown(report)
