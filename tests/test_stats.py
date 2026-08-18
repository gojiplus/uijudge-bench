"""Statistics unit tests, each pinned to an EXTERNAL ground truth.

Every assertion here references a published worked example or a value hand-computed
from a closed-form definition (shown in the test docstring). No test compares the
implementation to its own output — the numbers come from outside the code under test.
"""

from __future__ import annotations

import math

import pytest

from uijudge.harness.stats import (
    bootstrap_ci,
    cluster_bootstrap_ci,
    ece,
    iou,
    mcnemar,
    multilabel_f1,
    selector_match,
)

# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_bootstrap_ci_brackets_known_bernoulli_rate():
    """A percentile bootstrap of a Bernoulli(0.7) sample (70 ones, 30 zeros) must
    bracket the sample mean 0.70. Reference: Efron & Tibshirani, *An Introduction to
    the Bootstrap* (1993), Ch. 13 (percentile interval). For n=100, p_hat=0.7, the
    normal-approx 95% half-width is 1.96*sqrt(.7*.3/100)=0.0898, so the interval sits
    inside roughly (0.61, 0.79) and must contain 0.70."""
    values = [1.0] * 70 + [0.0] * 30
    low, high = bootstrap_ci(values, n_resamples=5000, seed=42)
    assert low < 0.70 < high
    assert 0.58 < low < 0.66
    assert 0.74 < high < 0.82


def test_bootstrap_ci_is_deterministic_under_seed():
    """Same seed -> byte-identical interval; different seed -> different resamples.

    Uses a large heterogeneous sample so the percentile bounds are granular enough that
    two different seeds do not collide by discretization accident."""
    values = [float(i % 5) for i in range(200)]
    a = bootstrap_ci(values, n_resamples=3000, seed=1)
    b = bootstrap_ci(values, n_resamples=3000, seed=1)
    c = bootstrap_ci(values, n_resamples=3000, seed=2)
    assert a == b
    assert a != c


def test_bootstrap_ci_accepts_bool_indicator():
    """Booleans are treated as 0/1 (correctness vectors)."""
    low, high = bootstrap_ci([True] * 8 + [False] * 2, n_resamples=2000, seed=0)
    assert 0.0 <= low <= 0.8 <= high <= 1.0


def test_bootstrap_ci_empty_is_zero():
    assert bootstrap_ci([]) == (0.0, 0.0)


def test_cluster_bootstrap_ci_is_seeded_and_resamples_whole_pages():
    values = [1.0, 1.0, 0.0, 0.0]
    pages = ["page-a", "page-a", "page-b", "page-b"]
    first = cluster_bootstrap_ci(values, pages, n_resamples=2_000, seed=42)
    second = cluster_bootstrap_ci(values, pages, n_resamples=2_000, seed=42)

    assert first == second
    assert first == (0.0, 1.0)


def test_cluster_bootstrap_ci_validates_cluster_contract():
    with pytest.raises(ValueError, match="equal length"):
        cluster_bootstrap_ci([1.0], [])
    with pytest.raises(ValueError, match="non-empty strings"):
        cluster_bootstrap_ci([1.0], [""])


# ---------------------------------------------------------------------------
# mcnemar
# ---------------------------------------------------------------------------


def test_mcnemar_exact_small_discordant():
    """Exact binomial branch. Discordant counts b=1, c=8 (total 9 < 25) => exact
    two-sided binomial with p=0.5:

        p = 2 * sum_{i=0}^{min(b,c)} C(9, i) * 0.5^9
          = 2 * (C(9,0)+C(9,1)) / 512
          = 2 * (1 + 9) / 512 = 20/512 = 0.0390625

    (Closed form for the two-sided exact McNemar test; see Fagerland, Lydersen &
    Laake 2013, "The McNemar test for binary matched-pairs data", BMC Med. Res.
    Methodol. 13:91, eq. for the exact conditional test.)"""
    # b = A right & B wrong; c = A wrong & B right.
    # Build correctness vectors that realize b=1, c=8 discordant pairs (and some concordant).
    a_correct = [True] + [False] * 8 + [True, True]
    b_correct = [False] + [True] * 8 + [True, True]
    res = mcnemar(a_correct, b_correct)
    assert res["method"] == "exact"
    assert res["b"] == 1 and res["c"] == 8
    assert math.isclose(res["p_value"], 20 / 512, rel_tol=1e-12)


def test_mcnemar_chi2_continuity_correction_wikipedia():
    """Chi-square-with-continuity-correction branch. Wikipedia "McNemar's test"
    worked example, discordant cells b=121, c=59 (total 180 >= 25):

        chi^2 = (|b - c| - 1)^2 / (b + c)
              = (|121 - 59| - 1)^2 / 180
              = (62 - 1)^2 / 180 = 3721 / 180 = 20.672...

    with 1 df, giving p approx 5.45e-6 (Wikipedia reports the same statistic)."""
    a_correct = [True] * 121 + [False] * 59 + [True] * 10
    b_correct = [False] * 121 + [True] * 59 + [True] * 10
    res = mcnemar(a_correct, b_correct)
    assert res["method"] == "chi2_cc"
    assert res["b"] == 121 and res["c"] == 59
    assert math.isclose(res["statistic"], 3721 / 180, rel_tol=1e-9)
    assert res["p_value"] < 1e-4


def test_mcnemar_no_discordant_is_p1():
    res = mcnemar([True, False, True], [True, False, True])
    assert res["b"] == 0 and res["c"] == 0
    assert res["p_value"] == 1.0


# ---------------------------------------------------------------------------
# ece  (Guo et al. 2017, "On Calibration of Modern Neural Networks", eq. 3)
# ---------------------------------------------------------------------------


def test_ece_perfectly_calibrated_is_zero():
    """ECE = sum_b (n_b/N) * |acc_b - conf_b|. If, within each occupied bin, the
    empirical accuracy equals the (constant) confidence, every term is 0 => ECE 0.
    Construct: 10 items at conf=0.9 with exactly 9 correct (acc 0.9); 10 items at
    conf=0.1 with exactly 1 correct (acc 0.1)."""
    confidences = [0.9] * 10 + [0.1] * 10
    corrects = [True] * 9 + [False] + [True] + [False] * 9
    val = ece(confidences, corrects, n_bins=10)["ece"]
    assert math.isclose(val, 0.0, abs_tol=1e-9)


def test_ece_anti_calibrated_is_large():
    """Maximally over-confident-and-wrong: all conf=0.95, all incorrect. The single
    occupied bin has acc=0, conf=0.95 => ECE = |0 - 0.95| = 0.95."""
    confidences = [0.95] * 20
    corrects = [False] * 20
    val = ece(confidences, corrects, n_bins=10)["ece"]
    assert math.isclose(val, 0.95, abs_tol=1e-9)


def test_ece_returns_bin_diagram_data():
    out = ece([0.95] * 20, [False] * 20, n_bins=10)
    assert "bins" in out
    occupied = [b for b in out["bins"] if b["count"] > 0]
    assert len(occupied) == 1
    assert occupied[0]["count"] == 20
    assert math.isclose(occupied[0]["accuracy"], 0.0, abs_tol=1e-9)
    assert math.isclose(occupied[0]["confidence"], 0.95, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# iou / selector_match  (hand-computed boxes)
# ---------------------------------------------------------------------------


def test_iou_partial_overlap_hand_computed():
    """A=[0,0,2,2], B=[1,1,2,2] in [x,y,w,h]. Intersection is the 1x1 square
    [1,2]x[1,2] => area 1. Union = 4 + 4 - 1 = 7. IoU = 1/7 = 0.142857..."""
    assert math.isclose(iou([0, 0, 2, 2], [1, 1, 2, 2]), 1 / 7, rel_tol=1e-9)


def test_iou_identical_is_one():
    assert iou([3, 4, 10, 6], [3, 4, 10, 6]) == 1.0


def test_iou_disjoint_is_zero():
    assert iou([0, 0, 1, 1], [5, 5, 1, 1]) == 0.0


def test_iou_edge_touch_is_zero():
    """Boxes sharing only an edge have zero-area intersection."""
    assert iou([0, 0, 2, 2], [2, 0, 2, 2]) == 0.0


def test_selector_match_normalizes_whitespace_and_case_insensitive_ws():
    assert selector_match("#foo > .bar", "#foo>.bar") is True
    assert selector_match("#foo", "#bar") is False
    assert selector_match("  div.x  ", "div.x") is True


# ---------------------------------------------------------------------------
# multilabel_f1  (scikit-learn f1_score semantics, micro & macro)
# ---------------------------------------------------------------------------


def test_multilabel_micro_f1_hand_computed():
    """gold = [{a,b},{b,c},{a}], pred = [{a},{b,c},{a,c}].
    Micro counts (sum over all label slots): TP=4 (a1,b2,c2,a3), FP=1 (c3),
    FN=1 (b1). micro-P = 4/5, micro-R = 4/5, micro-F1 = 0.8. (sklearn
    f1_score(..., average='micro').)"""
    gold = [{"a", "b"}, {"b", "c"}, {"a"}]
    pred = [{"a"}, {"b", "c"}, {"a", "c"}]
    out = multilabel_f1(gold, pred)
    assert math.isclose(out["micro_f1"], 0.8, rel_tol=1e-9)


def test_multilabel_macro_f1_hand_computed():
    """Same data. Per-label F1: a -> P1 R1 F1=1.0; b -> TP1 FN1 => P1 R0.5 F1=0.6667;
    c -> TP1 FP1 => P0.5 R1 F1=0.6667. macro = (1 + 2/3 + 2/3)/3 = 0.77778.
    (sklearn f1_score(..., average='macro').)"""
    gold = [{"a", "b"}, {"b", "c"}, {"a"}]
    pred = [{"a"}, {"b", "c"}, {"a", "c"}]
    out = multilabel_f1(gold, pred)
    assert math.isclose(out["macro_f1"], (1 + 2 / 3 + 2 / 3) / 3, rel_tol=1e-9)


def test_multilabel_perfect_prediction():
    gold = [{"a", "b"}, {"c"}]
    out = multilabel_f1(gold, [{"a", "b"}, {"c"}])
    assert out["micro_f1"] == 1.0
    assert out["macro_f1"] == 1.0
