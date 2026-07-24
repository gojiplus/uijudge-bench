"""Annotation app: trial building, position randomization, and judgment round-trip.

No browser needed — the HTTP handler is exercised via stdlib urllib against a real
ThreadingHTTPServer, and the pure core (build_trials / assign_sides / JudgmentStore)
is called directly.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from uijudge.design_track import app as A
from uijudge.design_track.rubric import RUBRIC_VERSION


def _sample_pairs():
    return [
        {
            "pair_id": "dp-p-x__y__desktop",
            "pair_type": "preference",
            "viewport": "desktop",
            "genre": "docs",
            "dimension_scope": ["visual_hierarchy", "color_use"],
            "known_worse": None,
            "member_a": {"page_id": "pageX", "page_path": "corpus/synthetic/syn-01000-clean/page.html"},
            "member_b": {"page_id": "pageY", "page_path": "corpus/synthetic/syn-01001-clean/page.html"},
        },
        {
            "pair_id": "dp-v-twin",
            "pair_type": "validity",
            "viewport": "desktop",
            "genre": "docs",
            "dimension_scope": ["spacing_alignment"],
            "known_worse": "B",
            "member_a": {"page_id": "cleanP", "page_path": "corpus/synthetic/syn-01000-clean/page.html"},
            "member_b": {"page_id": "twinP", "page_path": "corpus/synthetic/syn-01000-align-break/page.html"},
        },
    ]


def test_build_trials_expands_preference_over_dimensions_and_injects_catch():
    trials = A.build_trials(_sample_pairs(), rater_id="r1")
    # preference pair -> one trial per dimension (2); validity -> one catch trial (1).
    assert len(trials) == 3
    catch = [t for t in trials if t.is_catch]
    assert len(catch) == 1
    assert catch[0].dimension == "spacing_alignment"
    pref = [t for t in trials if not t.is_catch]
    assert {t.dimension for t in pref} == {"visual_hierarchy", "color_use"}


def test_position_is_randomized_per_trial_and_recorded():
    # Across many trial ids both left/right orderings of the same pair must appear.
    orders = set()
    for i in range(40):
        left, right = A.assign_sides("raterA", f"t{i}", "pageX", "pageY")
        orders.add((left, right))
    assert orders == {("pageX", "pageY"), ("pageY", "pageX")}


def test_judgment_records_choice_as_page_id(tmp_path):
    store = A.JudgmentStore(tmp_path)
    left, right = A.assign_sides("r1", "trial-0", "pageX", "pageY")
    # Rater clicked the LEFT page.
    rec = store.record(
        rater_id="r1",
        trial_id="trial-0",
        pair_id="dp-p-x__y__desktop",
        pair_type="preference",
        dimension="visual_hierarchy",
        viewport="desktop",
        side_assignment={"left": left, "right": right},
        chosen_side="left",
        ms_elapsed=4200,
        is_catch=False,
        known_worse=None,
    )
    assert rec["choice"] == left  # a page_id, not "left"
    assert rec["choice"] in ("pageX", "pageY")
    assert rec["side_assignment"] == {"left": left, "right": right}
    assert rec["ms_elapsed"] == 4200
    assert rec["rubric_version"] == RUBRIC_VERSION
    assert rec["dimension"] == "visual_hierarchy"
    assert "timestamp" in rec

    # Round-trip through the append-only file.
    lines = (tmp_path / "r1.jsonl").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["choice"] == left


def test_cannot_tell_is_recorded_not_a_page(tmp_path):
    store = A.JudgmentStore(tmp_path)
    rec = store.record(
        rater_id="r2",
        trial_id="t",
        pair_id="p",
        pair_type="preference",
        dimension="color_use",
        viewport="desktop",
        side_assignment={"left": "pageX", "right": "pageY"},
        chosen_side="cannot_tell",
        ms_elapsed=999,
        is_catch=False,
        known_worse=None,
    )
    assert rec["choice"] == "cannot_tell"


def test_session_is_resumable(tmp_path):
    store = A.JudgmentStore(tmp_path)
    store.record(
        rater_id="r3",
        trial_id="t1",
        pair_id="pairP",
        pair_type="preference",
        dimension="color_use",
        viewport="desktop",
        side_assignment={"left": "a", "right": "b"},
        chosen_side="left",
        ms_elapsed=1,
        is_catch=False,
        known_worse=None,
    )
    done = store.completed("r3")
    assert ("pairP", "color_use") in done


def test_http_round_trip(tmp_path):
    pairs = _sample_pairs()
    handler = A.make_handler(pairs=pairs, judgments_dir=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        # Log in.
        data = urllib.parse.urlencode({"rater_id": "http_rater", "demographics": "colleague"}).encode()
        urllib.request.urlopen(f"{base}/login", data=data)  # noqa: S310
        # Fetch a trial page.
        html = urllib.request.urlopen(f"{base}/trial?rater_id=http_rater").read().decode()  # noqa: S310
        assert "trial_id" in html and "iframe" in html.lower()
        # Post a judgment.
        payload = urllib.parse.urlencode(
            {
                "rater_id": "http_rater",
                "trial_id": "trial-http",
                "pair_id": "dp-v-twin",
                "pair_type": "validity",
                "dimension": "spacing_alignment",
                "viewport": "desktop",
                "left_page_id": "cleanP",
                "right_page_id": "twinP",
                "chosen_side": "left",
                "ms_elapsed": "3300",
                "is_catch": "1",
                "known_worse": "B",
            }
        ).encode()
        urllib.request.urlopen(f"{base}/judge", data=payload)  # noqa: S310
        recs = [json.loads(x) for x in (tmp_path / "http_rater.jsonl").read_text().splitlines()]
        assert recs and recs[-1]["choice"] == "cleanP"
        assert recs[-1]["side_assignment"] == {"left": "cleanP", "right": "twinP"}
    finally:
        server.shutdown()
