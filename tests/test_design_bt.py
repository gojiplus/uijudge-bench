"""Bradley-Terry MLE: recover a KNOWN ranking from seeded synthetic comparisons.

The test fixes latent strengths beta = [2, 1, 0, -1, -2] for five pages, draws
pairwise forced-choice outcomes from the Bradley-Terry model
P(i beats j) = sigmoid(beta_i - beta_j) with a seeded RNG, fits the MLE, and
asserts the recovered ranking equals the true ranking and the recovered scores
correlate ~1.0 with the generating strengths. This is a parameter-recovery test,
not a fixed-output snapshot.
"""

from __future__ import annotations

import math
import random

from uijudge.design_track.bradley_terry import bradley_terry, spearman


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _simulate(beta: dict[str, float], n_per_pair: int, seed: int):
    rng = random.Random(seed)
    items = list(beta)
    comparisons = []
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            i, j = items[a], items[b]
            p = _sigmoid(beta[i] - beta[j])
            for _ in range(n_per_pair):
                winner, loser = (i, j) if rng.random() < p else (j, i)
                comparisons.append((winner, loser))
    rng.shuffle(comparisons)
    return comparisons


def test_recovers_known_ranking():
    beta = {"p0": 2.0, "p1": 1.0, "p2": 0.0, "p3": -1.0, "p4": -2.0}
    comparisons = _simulate(beta, n_per_pair=200, seed=7)

    fit = bradley_terry(comparisons)
    recovered_order = [item for item, _ in fit.ranking]
    true_order = sorted(beta, key=lambda k: beta[k], reverse=True)
    assert recovered_order == true_order


def test_recovered_scores_correlate_with_truth():
    beta = {"p0": 2.0, "p1": 1.0, "p2": 0.0, "p3": -1.0, "p4": -2.0}
    comparisons = _simulate(beta, n_per_pair=300, seed=11)

    fit = bradley_terry(comparisons)
    items = list(beta)
    truth = [beta[i] for i in items]
    est = [fit.scores[i] for i in items]
    assert spearman(truth, est) > 0.99
    # Pearson on the log-strength scale should also be high.
    assert _pearson(truth, est) > 0.95


def test_deterministic_given_comparisons():
    comparisons = [("a", "b"), ("a", "b"), ("b", "c"), ("a", "c")]
    f1 = bradley_terry(comparisons)
    f2 = bradley_terry(comparisons)
    assert f1.scores == f2.scores


def test_margin_between_two_items():
    # 8 wins for a, 2 for b -> a strictly stronger, positive margin a over b.
    comparisons = [("a", "b")] * 8 + [("b", "a")] * 2
    fit = bradley_terry(comparisons)
    assert fit.scores["a"] > fit.scores["b"]
    assert fit.margin("a", "b") > 0


def _pearson(x, y):
    n = len(x)
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in x))
    vy = math.sqrt(sum((b - my) ** 2 for b in y))
    return cov / (vx * vy)
