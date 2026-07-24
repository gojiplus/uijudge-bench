"""Krippendorff's alpha (inter-rater reliability) from stdlib only.

Alpha is the design track's agreement statistic: per rubric dimension we compute
alpha over the raters' forced-choice judgments and gate promotion on it
(>= 0.667 to admit a dimension, per the plan). The implementation is the
coincidence-matrix method (Krippendorff 2011), which handles any number of
raters and missing data, with pluggable difference metrics.

    alpha = 1 - D_o / D_e
          = 1 - (n - 1) * sum_ck o_ck * delta(c, k)
                          / sum_ck n_c * n_k * delta(c, k)

where ``o`` is the coincidence matrix, ``n_c`` its marginals, ``n`` the number
of pairable values, and ``delta`` the squared difference metric (0/1 for
``nominal``; ``(c - k) ** 2`` for ``interval``).

Validated against the published worked example in
``tests/test_design_alpha.py`` (nominal 0.691, interval 0.811).
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence

ReliabilityData = Sequence[Sequence[Hashable | None]]


def _units(data: ReliabilityData) -> list[list[Hashable]]:
    """Transpose coder-major data into per-unit lists of present values.

    Args:
        data: Rows are coders, columns are units; ``None`` marks a missing cell.

    Returns:
        One list of present (non-``None``) values per unit column.
    """
    if not data:
        return []
    width = len(data[0])
    for row in data:
        if len(row) != width:
            raise ValueError("all coder rows must have the same number of units")
    units: list[list[Hashable]] = []
    for j in range(width):
        units.append([row[j] for row in data if row[j] is not None])
    return units


def _delta(level: str):
    """Return the squared difference metric for the given measurement level."""
    if level == "nominal":
        return lambda a, b: 0.0 if a == b else 1.0
    if level == "interval":
        return lambda a, b: float(a - b) ** 2
    raise ValueError(f"unsupported level {level!r} (use 'nominal' or 'interval')")


def _coincidence_matrix(
    data: ReliabilityData, level: str
) -> tuple[dict[Hashable, dict[Hashable, float]], dict[Hashable, float], float]:
    """Build the coincidence matrix, its marginals, and the pairable-value count.

    Each multiply-coded unit (>= 2 present values) contributes each ordered pair
    of its values with weight ``1 / (m_u - 1)`` so the unit adds exactly ``m_u``
    to the marginals.

    Returns:
        ``(o, n_marginals, n)`` where ``o[c][k]`` is the coincidence count,
        ``n_marginals[c] = sum_k o[c][k]``, and ``n`` is the total pairable
        values.
    """
    values: set[Hashable] = set()
    for unit in _units(data):
        values.update(unit)
    o: dict[Hashable, dict[Hashable, float]] = {c: dict.fromkeys(values, 0.0) for c in values}

    for unit in _units(data):
        m = len(unit)
        if m < 2:
            continue  # a singly-coded unit carries no reliability information
        w = 1.0 / (m - 1)
        for i, a in enumerate(unit):
            for j, b in enumerate(unit):
                if i != j:
                    o[a][b] += w

    marginals = {c: sum(o[c].values()) for c in values}
    n = sum(marginals.values())
    return o, marginals, n


def krippendorff_alpha(data: ReliabilityData, level: str = "nominal") -> float:
    """Compute Krippendorff's alpha over coder-major reliability data.

    Args:
        data: A sequence of coder rows of equal length; column ``j`` is unit
            ``j`` and ``None`` marks a missing observation. Values may be any
            hashable (strings like ``"A"``/``"B"`` for forced-choice, or ints).
        level: ``"nominal"`` (default) or ``"interval"``.

    Returns:
        Alpha in ``[-1, 1]``. Returns ``1.0`` when there is no observed
        disagreement (``D_e == 0``), the conventional degenerate-case value.

    Raises:
        ValueError: If rows differ in length, ``level`` is unknown, or there are
            fewer than two pairable values.
    """
    delta = _delta(level)
    o, marginals, n = _coincidence_matrix(data, level)
    if n < 2:
        raise ValueError("need at least two pairable values to compute alpha")

    vals = list(marginals)
    d_o = sum(o[c][k] * delta(c, k) for c in vals for k in vals)
    d_e = sum(marginals[c] * marginals[k] * delta(c, k) for c in vals for k in vals) / (n - 1)

    if d_e == 0:
        return 1.0
    return 1.0 - d_o / d_e
