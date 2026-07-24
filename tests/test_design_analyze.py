"""Analysis + promotion gate: alpha, catch screening, and schema-clean promotion."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from uijudge.design_track import analyze as AN
from uijudge.design_track import pairs as P
from uijudge.schema import validate_item

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((REPO_ROOT / "schemas" / "item.schema.json").read_text())


def _write_judgments(tmp_path, judgments):
    for rec in judgments:
        with (tmp_path / f"{rec['rater']}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


def test_zero_design_items_in_committed_labels():
    # The instrument is built but no human annotation has run: labels must hold NO design items.
    labels = (REPO_ROOT / "labels" / "items.jsonl").read_text().splitlines()
    for line in labels:
        if line.strip():
            obj = json.loads(line)
            assert obj["track"] != "design"
            assert obj["task_level"] != "design_pair"


def test_selftest_runs_offline():
    assert AN._main(["--selftest"]) == 0


def test_alpha_high_when_raters_agree():
    pairs = P.build_pairs(seed=1, n_validity=0, n_preference=6)
    # 5 raters, near-perfect agreement -> alpha ~ 1.
    judgments = AN.synth_judgments(pairs, n_raters=5, seed=3, accuracy=1.0)
    alphas = AN.alpha_by_dimension(judgments, pairs)
    for dim, a in alphas.items():
        assert a > 0.8, f"{dim} alpha={a}"


def test_alpha_low_when_raters_are_random():
    pairs = P.build_pairs(seed=1, n_validity=0, n_preference=8)
    judgments = AN.synth_judgments(pairs, n_raters=6, seed=9, accuracy=0.5)
    alphas = AN.alpha_by_dimension(judgments, pairs)
    # Coin-flip raters -> alpha near 0, certainly below the 0.667 gate.
    assert all(a < 0.667 for a in alphas.values())


def test_catch_pass_rate_flags_bad_rater():
    pairs = P.build_pairs(seed=1, n_validity=6, n_preference=0)
    good = AN.synth_judgments(pairs, n_raters=1, seed=1, accuracy=1.0, rater_prefix="good")
    bad = AN.synth_judgments(pairs, n_raters=1, seed=1, accuracy=0.0, rater_prefix="bad")
    rates = AN.catch_pass_rates(good + bad, pairs)
    assert rates["good0"]["pass_rate"] == pytest.approx(1.0)
    assert rates["bad0"]["pass_rate"] == pytest.approx(0.0)
    flagged = AN.flagged_raters(rates, threshold=0.8)
    assert "bad0" in flagged and "good0" not in flagged


def test_promote_refuses_under_judged_pairs(tmp_path):
    pairs = P.build_pairs(seed=1, n_validity=0, n_preference=4)
    judgments = AN.synth_judgments(pairs, n_raters=3, seed=1, accuracy=1.0)  # only 3 judgments/pair-dim
    _write_judgments(tmp_path, judgments)
    labels = tmp_path / "items.jsonl"
    promoted = AN.promote(tmp_path, pairs, labels, n_min=10, alpha_min=0.667, rater_pool_desc="test")
    assert promoted == []
    assert not labels.exists() or labels.read_text().strip() == ""


def test_promote_refuses_low_alpha(tmp_path):
    pairs = P.build_pairs(seed=1, n_validity=0, n_preference=6)
    judgments = AN.synth_judgments(pairs, n_raters=12, seed=9, accuracy=0.5)  # random -> low alpha
    _write_judgments(tmp_path, judgments)
    labels = tmp_path / "items.jsonl"
    promoted = AN.promote(tmp_path, pairs, labels, n_min=5, alpha_min=0.667, rater_pool_desc="test")
    assert promoted == []


def test_promote_preference_items_are_schema_clean(tmp_path):
    pairs = P.build_pairs(seed=1, n_validity=0, n_preference=6)
    judgments = AN.synth_judgments(pairs, n_raters=12, seed=3, accuracy=1.0)
    _write_judgments(tmp_path, judgments)
    labels = tmp_path / "items.jsonl"
    promoted = AN.promote(tmp_path, pairs, labels, n_min=5, alpha_min=0.667, rater_pool_desc="pilot: 12 colleagues")
    assert promoted, "high-agreement pairs should promote"
    for item in promoted:
        assert item["door"] == "human"
        assert item["task_level"] == "design_pair"
        assert item["track"] == "design"
        assert item["annotation_unit"] == "pair"
        assert item["criterion_code"].startswith("design:")
        assert item["ground_truth"] in ("A", "B")
        assert item["receipt"]["rater_pool_desc"] == "pilot: 12 colleagues"
        assert "n_judgments" in item["receipt"] and "alpha_dimension" in item["receipt"]
        validate_item(item)
        jsonschema.validate(item, SCHEMA)


def test_promote_validity_items_use_mutation_door(tmp_path):
    pairs = P.build_pairs(seed=1, n_validity=8, n_preference=0)
    # Validity pairs carry construction ground truth; they promote with door=mutation
    # regardless of human judgments.
    labels = tmp_path / "items.jsonl"
    promoted = AN.promote(tmp_path, pairs, labels, n_min=5, alpha_min=0.667, rater_pool_desc="test")
    assert promoted
    for item in promoted:
        assert item["door"] == "mutation"
        assert item["ground_truth"] in ("A", "B")
        validate_item(item)
        jsonschema.validate(item, SCHEMA)
    # Ground truth points at the CLEAN member, i.e. opposite of the mutated side.
    by_pair = {p["pair_id"]: p for p in pairs}
    for item in promoted:
        pair = by_pair[item["metadata"]["pair_id"]]
        clean_side = "A" if pair["known_worse"] == "B" else "B"
        assert item["ground_truth"] == clean_side


def test_promoted_items_appended_to_labels_file(tmp_path):
    pairs = P.build_pairs(seed=1, n_validity=4, n_preference=0)
    labels = tmp_path / "items.jsonl"
    promoted = AN.promote(tmp_path, pairs, labels, n_min=5, alpha_min=0.667, rater_pool_desc="test")
    lines = [line for line in labels.read_text().splitlines() if line.strip()]
    assert len(lines) == len(promoted)
    for line in lines:
        validate_item(json.loads(line))


def test_prepare_dedups_last_wins_and_restores_authoritative_pair_type():
    pairs = P.build_pairs(seed=1, n_validity=2, n_preference=2)
    val = next(p for p in pairs if p["pair_type"] == "validity")
    clean_id = val["member_a"]["page_id"] if val["known_worse"] == "B" else val["member_b"]["page_id"]
    # A tampered record: falsified pair_type, and an earlier stale choice for the same key.
    stale = {
        "rater": "r1",
        "pair_id": val["pair_id"],
        "dimension": val["dimension_scope"][0],
        "choice": "wrong",
        "chosen_side": "left",
        "side_assignment": {"left": "x", "right": "y"},
        "pair_type": "preference",
        "is_catch": False,
        "known_worse": None,
    }
    fresh = {**stale, "choice": clean_id}
    prepared = AN.prepare_judgments([stale, fresh], pairs)
    assert len(prepared) == 1  # deduped on (rater, pair_id, dimension)
    assert prepared[0]["choice"] == clean_id  # last record wins
    assert prepared[0]["pair_type"] == "validity"  # restored from pairs, not the falsified "preference"
    assert prepared[0]["known_worse"] == val["known_worse"]


def test_falsified_pair_type_scored_per_pairs_truth():
    # A validity trial whose record claims pair_type="preference" must still be scored as a catch.
    pairs = P.build_pairs(seed=1, n_validity=1, n_preference=0)
    val = pairs[0]
    mutated_id = val["member_b"]["page_id"] if val["known_worse"] == "B" else val["member_a"]["page_id"]
    tampered = {
        "rater": "cheat",
        "pair_id": val["pair_id"],
        "dimension": val["dimension_scope"][0],
        "choice": mutated_id,
        "chosen_side": "left",
        "side_assignment": {"left": mutated_id, "right": "z"},
        "pair_type": "preference",
        "is_catch": False,
        "known_worse": None,  # falsified
    }
    report = AN.analyze_report([tampered], pairs)
    # Counted as a catch (n=1) despite the falsified pair_type, and scored a miss (chose mutated).
    assert report["raters"]["cheat"]["n"] == 1
    assert report["raters"]["cheat"]["pass_rate"] == 0.0


def test_duplicates_do_not_change_analysis_or_promotion(tmp_path):
    pairs = P.build_pairs(seed=1, n_validity=4, n_preference=6)
    singles = AN.synth_judgments(pairs, n_raters=12, seed=3, accuracy=1.0)
    dups = singles + list(singles)  # every record appears twice

    r_single = AN.analyze_report(singles, pairs)
    r_dup = AN.analyze_report(dups, pairs)
    assert r_single["n_judgments"] == r_dup["n_judgments"]
    assert r_single["per_dimension"] == r_dup["per_dimension"]
    assert r_single["raters"] == r_dup["raters"]
    assert r_single["position_bias"] == r_dup["position_bias"]

    d_single = tmp_path / "single"
    d_dup = tmp_path / "dup"
    d_single.mkdir()
    d_dup.mkdir()
    _write_judgments(d_single, singles)
    _write_judgments(d_dup, dups)
    p_single = AN.promote(d_single, pairs, d_single / "items.jsonl", n_min=10, alpha_min=0.667, rater_pool_desc="t")
    p_dup = AN.promote(d_dup, pairs, d_dup / "items.jsonl", n_min=10, alpha_min=0.667, rater_pool_desc="t")
    assert [i["item_id"] for i in p_single] == [i["item_id"] for i in p_dup]
    assert [i["receipt"].get("n_judgments") for i in p_single] == [i["receipt"].get("n_judgments") for i in p_dup]


def test_position_bias_measures_left_choice_rate():
    def rec(rater, side):
        return {
            "rater": rater,
            "pair_id": "p",
            "dimension": "color_use",
            "choice": "x",
            "chosen_side": side,
            "side_assignment": {"left": "x", "right": "y"},
        }

    judgments = [
        rec("a", "left"),
        rec("a", "left"),
        rec("a", "right"),  # 2/3 left
        rec("b", "right"),
        rec("b", "right"),  # 0/2 left
        rec("c", "cannot_tell"),  # excluded from denominator
    ]
    pos = AN.position_bias(judgments)
    assert pos["expected_left_rate"] == 0.5
    assert pos["n_decided"] == 5
    assert pos["overall_left_rate"] == pytest.approx(2 / 5)
    assert pos["per_rater"]["a"]["left_rate"] == pytest.approx(2 / 3)
    assert pos["per_rater"]["b"]["left_rate"] == pytest.approx(0.0)
    assert "c" not in pos["per_rater"]
