"""Offline unit test for the ``verify_control`` empty-measurement guard.

No browser: ``_collect`` is stubbed so we exercise the guard logic in isolation. A clean-twin
control whose measured dict is empty must be discarded (``fires=True``) and never emitted with
a bare receipt.
"""

from __future__ import annotations

import asyncio

from uijudge.engine.verify import Verifier

_INJECTION = {
    "defect_class": "contrast:degrade",
    "criterion_code": "wcag:1.4.3",
    "selector": "#cta",
}


def _run_control(collect_return):
    v = Verifier()

    async def fake_collect(source, injection_record):
        return collect_return

    v._collect = fake_collect  # type: ignore[assignment]
    return asyncio.run(v.verify_control("clean.html", _INJECTION))


def test_empty_measured_is_discarded():
    # _collect reports the check does NOT fire, but measured is empty -> must still discard.
    receipt, fires = _run_control((False, {}, None, None))
    assert fires is True, "empty-measurement control must be discarded (fires forced True)"
    assert receipt["measured"] == {}
    assert "discard_reason" in receipt
    assert receipt["control"] is True


def test_nonempty_measured_clean_control_is_emitted():
    receipt, fires = _run_control((False, {"ratio": 7.1}, [1, 2, 3, 4], None))
    assert fires is False, "a genuinely clean control with a real measurement is emitted"
    assert receipt["measured"] == {"ratio": 7.1}
    assert "discard_reason" not in receipt
