"""Krippendorff's alpha validated against a published worked example.

Worked example source: the "Computational example" in the Wikipedia article
*Krippendorff's alpha* (https://en.wikipedia.org/wiki/Krippendorff%27s_alpha),
which reproduces the canonical 3-coder x 15-unit reliability matrix from
Krippendorff, K. (2011), "Computing Krippendorff's Alpha-Reliability"
(University of Pennsylvania, Annenberg School for Communication).

That matrix has 26 pairable values across 12 multiply-coded units and the
published reliabilities are:

    nominal  alpha = 0.691
    interval alpha = 0.811

Reproducing both to three decimals is the acceptance test for this module.
"""

from __future__ import annotations

import math

from uijudge.design_track.alpha import krippendorff_alpha

# The published reliability data matrix (rows = coders, columns = units).
# None marks a missing observation (an unlabeled unit-coder cell).
_M = None
WORKED_EXAMPLE = [
    [_M, _M, _M, _M, _M, 3, 4, 1, 2, 1, 1, 3, 3, _M, 3],  # Coder A
    [1, _M, 2, 1, 3, 3, 4, 3, _M, _M, _M, _M, _M, _M, _M],  # Coder B
    [_M, _M, 2, 1, 3, 4, 4, _M, 2, 1, 1, 3, 3, _M, 4],  # Coder C
]


def test_nominal_alpha_matches_published_value():
    alpha = krippendorff_alpha(WORKED_EXAMPLE, level="nominal")
    assert math.isclose(alpha, 0.691, abs_tol=0.001)


def test_interval_alpha_matches_published_value():
    alpha = krippendorff_alpha(WORKED_EXAMPLE, level="interval")
    assert math.isclose(alpha, 0.811, abs_tol=0.001)


def test_pairable_value_count_is_26():
    # A construct-validity check: the published example reports 26 pairable values.
    from uijudge.design_track.alpha import _coincidence_matrix

    _, _, n = _coincidence_matrix(WORKED_EXAMPLE, level="nominal")
    assert n == 26


def test_perfect_agreement_is_one():
    data = [
        [1, 2, 3, 1, 2],
        [1, 2, 3, 1, 2],
    ]
    assert math.isclose(krippendorff_alpha(data, level="nominal"), 1.0, abs_tol=1e-9)


def test_alpha_handles_two_rater_pair_lists():
    # Forced-choice A/B judgments as strings must work under the nominal metric.
    data = [
        ["A", "A", "B", "B", "A"],
        ["A", "B", "B", "B", "A"],
    ]
    alpha = krippendorff_alpha(data, level="nominal")
    assert -1.0 <= alpha <= 1.0
