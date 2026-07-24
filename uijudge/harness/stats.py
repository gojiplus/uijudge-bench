"""Pure-Python statistics for judge evaluation — CIs, significance, calibration, localization.

No numpy/scipy dependency: every routine is closed-form or a seeded resample, so the
harness runs in the minimal wheel environment. Each public function documents the
external definition it implements (with a citation) so the numbers can be checked
against published worked examples rather than the code's own output.

References
----------
- Bootstrap CI: Efron & Tibshirani, *An Introduction to the Bootstrap* (1993), Ch. 13
  (percentile method).
- McNemar (exact + continuity-corrected chi-square): Fagerland, Lydersen & Laake (2013),
  "The McNemar test for binary matched-pairs data", *BMC Med. Res. Methodol.* 13:91; and
  the worked example in the Wikipedia "McNemar's test" article.
- ECE (equal-width binning): Guo, Pleiss, Sun & Weinberger (2017), "On Calibration of
  Modern Neural Networks", ICML, eq. (3).
- Multi-label F1 (micro/macro): matches scikit-learn ``f1_score`` averaging semantics.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any


def _as_floats(values: Sequence[Any]) -> list[float]:
    """Coerce a sequence of numbers/bools into floats (True->1.0, False->0.0)."""
    return [1.0 if v is True else 0.0 if v is False else float(v) for v in values]


# ---------------------------------------------------------------------------
# Bootstrap confidence interval
# ---------------------------------------------------------------------------


def bootstrap_ci(
    values: Sequence[Any],
    n_resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
    statistic=None,
) -> tuple[float, float]:
    """Percentile bootstrap CI for a scalar statistic of ``values``.

    Draws ``n_resamples`` resamples with replacement, computes ``statistic`` on each
    (mean by default), and returns the central ``1 - alpha`` percentile interval.
    Works for any per-item metric expressed as a value vector (accuracy from a 0/1
    correctness vector, or F1 via a custom ``statistic`` over resampled indices).

    When ``statistic`` is None the values are coerced to floats and the mean is taken;
    when a ``statistic`` callable is supplied the raw items are resampled untouched, so
    non-scalar per-item records (e.g. ``(gold, pred)`` pairs feeding an F1) are supported.

    Args:
        values: Per-item values (numbers/bools for the default mean; any objects when a
            ``statistic`` is given).
        n_resamples: Number of bootstrap resamples.
        alpha: Two-sided miss rate; ``alpha=0.05`` -> a 95% interval.
        seed: RNG seed for reproducibility.
        statistic: Callable mapping a resampled list to a float. Defaults to the mean.

    Returns:
        ``(low, high)`` percentile bounds; ``(0.0, 0.0)`` if ``values`` is empty.
    """
    if not values:
        return (0.0, 0.0)
    if statistic is None:
        data = _as_floats(values)
        statistic = lambda xs: sum(xs) / len(xs)  # noqa: E731 - trivial default
    else:
        data = list(values)
    rng = random.Random(seed)
    n = len(data)
    stats = []
    for _ in range(n_resamples):
        sample = [data[rng.randrange(n)] for _ in range(n)]
        stats.append(statistic(sample))
    stats.sort()
    lo_idx = int((alpha / 2) * n_resamples)
    hi_idx = min(int((1 - alpha / 2) * n_resamples), n_resamples - 1)
    return (stats[lo_idx], stats[hi_idx])


# ---------------------------------------------------------------------------
# McNemar's test for two paired judges
# ---------------------------------------------------------------------------


def _binom_coeff(n: int, k: int) -> int:
    """Exact binomial coefficient C(n, k)."""
    return math.comb(n, k)


def _exact_mcnemar_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: conditional binomial with p=0.5.

    p = 2 * sum_{i=0}^{min(b,c)} C(n, i) * 0.5^n,  n = b + c,  capped at 1.0.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(_binom_coeff(n, i) for i in range(k + 1)) * (0.5**n)
    return min(1.0, 2.0 * tail)


def _chi2_sf_df1(x: float) -> float:
    """Survival function of the chi-square(df=1) distribution: P(X > x).

    For 1 df, P(X > x) = erfc(sqrt(x/2)) (standard identity via the normal tail).
    """
    if x <= 0:
        return 1.0
    return math.erfc(math.sqrt(x / 2.0))


def mcnemar(model_a_correct: Sequence[bool], model_b_correct: Sequence[bool]) -> dict[str, Any]:
    """Paired McNemar test comparing two judges on the same items.

    Discordant pairs: ``b`` = A correct & B wrong, ``c`` = A wrong & B correct. For a
    small discordant total (``b + c < 25``) the exact two-sided binomial test is used;
    otherwise the chi-square test with Yates' continuity correction:
    ``chi2 = (|b - c| - 1)^2 / (b + c)`` on 1 df.

    Args:
        model_a_correct: Per-item correctness for judge A.
        model_b_correct: Per-item correctness for judge B (same order/items as A).

    Returns:
        Dict with ``b``, ``c``, ``n_discordant``, ``method`` ("exact"|"chi2_cc"),
        ``statistic`` (chi-square value; ``None`` on the exact branch), and ``p_value``.

    Raises:
        ValueError: If the two vectors differ in length.
    """
    if len(model_a_correct) != len(model_b_correct):
        raise ValueError("mcnemar requires paired vectors of equal length")
    b = sum(1 for a, bb in zip(model_a_correct, model_b_correct, strict=True) if a and not bb)
    c = sum(1 for a, bb in zip(model_a_correct, model_b_correct, strict=True) if bb and not a)
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "n_discordant": 0, "method": "none", "statistic": 0.0, "p_value": 1.0}
    if n < 25:
        return {
            "b": b,
            "c": c,
            "n_discordant": n,
            "method": "exact",
            "statistic": None,
            "p_value": _exact_mcnemar_p(b, c),
        }
    stat = (abs(b - c) - 1) ** 2 / n
    return {"b": b, "c": c, "n_discordant": n, "method": "chi2_cc", "statistic": stat, "p_value": _chi2_sf_df1(stat)}


# ---------------------------------------------------------------------------
# Expected calibration error
# ---------------------------------------------------------------------------


def ece(confidences: Sequence[float], corrects: Sequence[bool], n_bins: int = 10) -> dict[str, Any]:
    """Expected Calibration Error with equal-width confidence bins (Guo et al. 2017).

    ECE = sum_b (n_b / N) * |acc_b - conf_b|, where the sum runs over ``n_bins`` equal
    width bins on [0, 1]; ``acc_b`` is the empirical accuracy in the bin and ``conf_b``
    the mean predicted confidence. Bins are half-open ``[lo, hi)`` except the last which
    includes 1.0.

    Args:
        confidences: Predicted confidence per item in [0, 1].
        corrects: Whether each item's prediction was correct.
        n_bins: Number of equal-width bins.

    Returns:
        Dict with ``ece`` and ``bins`` (per-bin ``lo``, ``hi``, ``count``, ``accuracy``,
        ``confidence`` — reliability-diagram data).

    Raises:
        ValueError: If the inputs differ in length.
    """
    if len(confidences) != len(corrects):
        raise ValueError("ece requires confidences and corrects of equal length")
    n = len(confidences)
    bins = []
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins
        bins.append({"lo": lo, "hi": hi, "count": 0, "_correct": 0, "_conf_sum": 0.0})
    for conf, correct in zip(confidences, corrects, strict=True):
        conf = min(max(float(conf), 0.0), 1.0)
        idx = min(int(conf * n_bins), n_bins - 1)  # conf==1.0 -> last bin
        b = bins[idx]
        b["count"] += 1
        b["_correct"] += 1 if correct else 0
        b["_conf_sum"] += conf
    total_ece = 0.0
    out_bins = []
    for b in bins:
        count = b["count"]
        acc = b["_correct"] / count if count else 0.0
        conf = b["_conf_sum"] / count if count else 0.0
        if count and n:
            total_ece += (count / n) * abs(acc - conf)
        out_bins.append({"lo": b["lo"], "hi": b["hi"], "count": count, "accuracy": acc, "confidence": conf})
    return {"ece": total_ece, "bins": out_bins, "n": n}


# ---------------------------------------------------------------------------
# Localization primitives (L3)
# ---------------------------------------------------------------------------


def iou(bbox_a: Sequence[float], bbox_b: Sequence[float]) -> float:
    """Intersection-over-union of two axis-aligned boxes in ``[x, y, w, h]`` form.

    Returns 0.0 for non-overlapping or degenerate (zero-area) boxes.
    """
    ax, ay, aw, ah = bbox_a
    bx, by, bw, bh = bbox_b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    inter_w = max(0.0, min(ax2, bx2) - max(ax, bx))
    inter_h = max(0.0, min(ay2, by2) - max(ay, by))
    inter = inter_w * inter_h
    area_a = max(0.0, aw) * max(0.0, ah)
    area_b = max(0.0, bw) * max(0.0, bh)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _normalize_selector(sel: str) -> str:
    """Normalize a CSS selector for equality: strip and collapse whitespace, incl. around combinators."""
    collapsed = " ".join((sel or "").split())
    for combinator in (">", "+", "~"):
        collapsed = collapsed.replace(f" {combinator} ", combinator)
    return collapsed


def selector_match(pred: str, gold: str) -> bool:
    """Exact CSS-selector match after whitespace/combinator normalization."""
    return _normalize_selector(pred) == _normalize_selector(gold)


# ---------------------------------------------------------------------------
# Multi-label F1 (L2)
# ---------------------------------------------------------------------------


def multilabel_f1(gold: Sequence[set[str]], pred: Sequence[set[str]]) -> dict[str, Any]:
    """Micro- and macro-averaged multi-label F1 (scikit-learn ``f1_score`` semantics).

    Args:
        gold: Per-item gold label sets.
        pred: Per-item predicted label sets (same order/items as ``gold``).

    Returns:
        Dict with ``micro_f1``, ``macro_f1``, ``micro_precision``, ``micro_recall``,
        and ``per_label`` (per-label tp/fp/fn/precision/recall/f1).

    Raises:
        ValueError: If the two sequences differ in length.
    """
    if len(gold) != len(pred):
        raise ValueError("multilabel_f1 requires gold and pred of equal length")
    labels: set[str] = set()
    for g, p in zip(gold, pred, strict=True):
        labels |= set(g) | set(p)

    per_label: dict[str, dict[str, float]] = {}
    micro_tp = micro_fp = micro_fn = 0
    macro_f1_sum = 0.0
    for label in sorted(labels):
        tp = sum(1 for g, p in zip(gold, pred, strict=True) if label in g and label in p)
        fp = sum(1 for g, p in zip(gold, pred, strict=True) if label not in g and label in p)
        fn = sum(1 for g, p in zip(gold, pred, strict=True) if label in g and label not in p)
        micro_tp += tp
        micro_fp += fp
        micro_fn += fn
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_label[label] = {"tp": tp, "fp": fp, "fn": fn, "precision": prec, "recall": rec, "f1": f1}
        macro_f1_sum += f1

    micro_prec = micro_tp / (micro_tp + micro_fp) if (micro_tp + micro_fp) else 0.0
    micro_rec = micro_tp / (micro_tp + micro_fn) if (micro_tp + micro_fn) else 0.0
    micro_f1 = 2 * micro_prec * micro_rec / (micro_prec + micro_rec) if (micro_prec + micro_rec) else 0.0
    macro_f1 = macro_f1_sum / len(labels) if labels else 0.0
    return {
        "micro_f1": micro_f1,
        "macro_f1": macro_f1,
        "micro_precision": micro_prec,
        "micro_recall": micro_rec,
        "per_label": per_label,
    }
