"""Bradley-Terry model fit by MM (minorization-maximization), stdlib only.

Design quality is measured by pairwise forced choice, never absolute scores; the
Bradley-Terry model turns a bag of "page i beat page j" comparisons into a latent
strength per page. We fit the MLE with Hunter's (2004) MM iteration, which needs
no gradient library:

    p_i <- W_i / sum_{j != i} n_ij / (p_i + p_j)

where ``W_i`` is i's win count and ``n_ij`` the number of i-vs-j comparisons.
Strengths ``p`` are normalized to a geometric mean of 1; the reported score is
``log(p)`` (a Bradley-Terry "ability" on the logit scale, directly comparable to
the generating strengths in the recovery test).

Reference: Hunter, D. R. (2004), "MM algorithms for generalized Bradley-Terry
models", Annals of Statistics 32(1).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class BTFit:
    """Result of a Bradley-Terry fit.

    Attributes:
        scores: ``item -> log-strength`` (centered so mean log-strength is 0).
        strengths: ``item -> strength`` on the positive multiplicative scale.
        ranking: Items with their scores, best first.
        iterations: MM iterations run before convergence.
    """

    scores: dict[Hashable, float]
    strengths: dict[Hashable, float]
    ranking: list[tuple[Hashable, float]]
    iterations: int

    def margin(self, a: Hashable, b: Hashable) -> float:
        """Return the log-strength margin ``score[a] - score[b]``."""
        return self.scores[a] - self.scores[b]


def bradley_terry(
    comparisons: Iterable[tuple[Hashable, Hashable]],
    *,
    max_iter: int = 10_000,
    tol: float = 1e-9,
) -> BTFit:
    """Fit Bradley-Terry strengths from ``(winner, loser)`` comparisons.

    Args:
        comparisons: Iterable of ``(winner, loser)`` item pairs. Ties are not
            represented (forced choice); feed a "cannot tell" outcome as no
            comparison.
        max_iter: Maximum MM iterations.
        tol: Convergence tolerance on the max relative strength change.

    Returns:
        A :class:`BTFit`. Deterministic given the same comparisons.

    Raises:
        ValueError: If there are no comparisons.
    """
    wins: dict[Hashable, float] = defaultdict(float)
    pair_counts: dict[tuple[Hashable, Hashable], float] = defaultdict(float)
    items: list[Hashable] = []
    seen: set[Hashable] = set()

    for winner, loser in comparisons:
        for it in (winner, loser):
            if it not in seen:
                seen.add(it)
                items.append(it)  # preserve first-seen order for determinism
        wins[winner] += 1.0
        key = (winner, loser) if _order(winner, loser) else (loser, winner)
        pair_counts[key] += 1.0

    if not items:
        raise ValueError("no comparisons provided")

    # Total comparisons between each unordered pair.
    n_ij: dict[frozenset, float] = defaultdict(float)
    for (a, b), c in pair_counts.items():
        n_ij[frozenset((a, b))] += c

    strengths = dict.fromkeys(items, 1.0)
    iterations = 0
    for step in range(1, max_iter + 1):
        iterations = step
        new: dict[Hashable, float] = {}
        for i in items:
            denom = 0.0
            for j in items:
                if j == i:
                    continue
                nij = n_ij.get(frozenset((i, j)), 0.0)
                if nij:
                    denom += nij / (strengths[i] + strengths[j])
            # Wins for i; floor at a tiny epsilon so an item that never wins gets a
            # finite (very negative) log-strength instead of log(0). The MLE for such
            # an item is 0; the floor is a numerical stand-in that does not perturb
            # well-connected items.
            wi = wins.get(i, 0.0)
            raw = (wi / denom) if denom > 0 else strengths[i]
            new[i] = max(raw, 1e-12)

        _normalize_geometric_mean(new)
        max_change = max(abs(new[i] - strengths[i]) / strengths[i] for i in items)
        strengths = new
        if max_change < tol:
            break

    scores = {i: math.log(strengths[i]) for i in items}
    # Center scores so the mean is exactly 0 (geometric-mean-1 already implies this
    # up to floating point; re-center defensively).
    mean = sum(scores.values()) / len(scores)
    scores = {i: s - mean for i, s in scores.items()}
    ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return BTFit(scores=scores, strengths=strengths, ranking=ranking, iterations=iterations)


def _order(a: Hashable, b: Hashable) -> bool:
    """Return True if ``a`` sorts before ``b`` (stable canonical pair ordering)."""
    return repr(a) <= repr(b)


def _normalize_geometric_mean(strengths: dict[Hashable, float]) -> None:
    """Scale ``strengths`` in place so their geometric mean is 1."""
    log_mean = sum(math.log(v) for v in strengths.values()) / len(strengths)
    factor = math.exp(-log_mean)
    for k in strengths:
        strengths[k] *= factor


def spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation between two equal-length score lists."""
    rx = _ranks(x)
    ry = _ranks(y)
    n = len(x)
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy)


def _ranks(values: list[float]) -> list[float]:
    """Return average ranks (ties shared) of ``values``."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks
