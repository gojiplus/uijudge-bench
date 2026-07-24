"""Dependency-light local annotation app for the design-quality track.

Stdlib ``http.server`` only — no web framework. A rater logs in (id + optional
demographics), reads the rubric, then judges one dimension per trial: two pages shown
side by side (the frozen ``page.html`` rendered in a sized iframe, so both members are
shown at the *same viewport*), with the dimension's behavioral anchors on screen, and a
forced choice of Left / Right / "genuinely cannot tell" (recorded, discouraged in the
instructions). The on-screen left/right side is randomized per trial and recorded; the
recorded ``choice`` is the chosen page's ``page_id``, never a side. Validity pairs are
injected as unlabeled catch trials among the preference trials.

Judgments append to ``design_track/judgments/<rater_id>.jsonl``; the session is resumable
(already-judged pair+dimension trials are skipped on return).

Run::

    python -m uijudge.design_track.app            # serves design_track/pairs_v1.jsonl
    python -m uijudge.design_track.app --demo     # 3 bundled pairs, for smoke-testing the flow

The pure core (:func:`build_trials`, :func:`assign_sides`, :class:`JudgmentStore`) is
importable and unit-tested without a browser.
"""

from __future__ import annotations

import html
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .pairs import CORPUS_ROOT, PAIRS_FILE, REPO_ROOT, load_pairs
from .rubric import DIMENSIONS_BY_KEY, RUBRIC_VERSION

# Iframe render sizes per viewport (CSS px). The frozen page is shown at its captured width.
VIEWPORT_PX: dict[str, tuple[int, int]] = {
    "desktop": (1280, 800),
    "mobile": (375, 667),
    "tablet_landscape": (1024, 768),
}


@dataclass(frozen=True)
class Trial:
    """One judgment task: a pair judged on a single dimension."""

    trial_id: str
    pair_id: str
    pair_type: str
    dimension: str
    viewport: str
    genre: str
    is_catch: bool
    known_worse: str | None
    member_a: dict[str, Any]
    member_b: dict[str, Any]


def build_trials(pairs: list[dict[str, Any]], rater_id: str, *, seed: int | None = None) -> list[Trial]:
    """Expand pairs into per-dimension trials, catch trials interleaved among preference ones.

    A preference pair yields one trial per dimension in its scope; a validity pair yields a
    single catch trial on its targeted dimension. Order is seeded per rater so different raters
    see different (but reproducible) sequences.
    """
    pref: list[Trial] = []
    catch: list[Trial] = []
    for p in pairs:
        for dim in p["dimension_scope"]:
            t = Trial(
                trial_id=f"{p['pair_id']}::{dim}",
                pair_id=p["pair_id"],
                pair_type=p["pair_type"],
                dimension=dim,
                viewport=p["viewport"],
                genre=p.get("genre", "unknown"),
                is_catch=(p["pair_type"] == "validity"),
                known_worse=p.get("known_worse"),
                member_a=p["member_a"],
                member_b=p["member_b"],
            )
            (catch if t.is_catch else pref).append(t)

    rng = random.Random(f"{rater_id}:{seed}" if seed is not None else rater_id)
    rng.shuffle(pref)
    rng.shuffle(catch)

    if not catch:
        return pref
    # Interleave: spread catch trials as evenly as possible through the preference stream.
    out: list[Trial] = []
    every = max(1, len(pref) // (len(catch) + 1) or 1)
    ci = 0
    for i, t in enumerate(pref):
        out.append(t)
        if ci < len(catch) and (i + 1) % every == 0:
            out.append(catch[ci])
            ci += 1
    out.extend(catch[ci:])  # any remainder
    return out


def assign_sides(rater_id: str, trial_id: str, page_a: str, page_b: str) -> tuple[str, str]:
    """Return ``(left_page_id, right_page_id)`` with the side randomized deterministically.

    Seeded by ``rater_id:trial_id`` so the assignment is reproducible and can be recomputed,
    but is nonetheless recorded verbatim on each judgment (the record, not the RNG, is truth).
    """
    coin = random.Random(f"{rater_id}:{trial_id}").random()
    return (page_a, page_b) if coin < 0.5 else (page_b, page_a)


class JudgmentStore:
    """Append-only per-rater judgment log with resume support."""

    def __init__(self, judgments_dir: Path | str):
        self.dir = Path(judgments_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, rater_id: str) -> Path:
        safe = "".join(c for c in rater_id if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError("rater_id must contain alphanumerics")
        return self.dir / f"{safe}.jsonl"

    def record(
        self,
        *,
        rater_id: str,
        trial_id: str,
        pair_id: str,
        pair_type: str,
        dimension: str,
        viewport: str,
        side_assignment: dict[str, str],
        chosen_side: str,
        ms_elapsed: int,
        is_catch: bool,
        known_worse: str | None,
    ) -> dict[str, Any]:
        """Record one judgment. ``choice`` is stored as the chosen page_id (or 'cannot_tell')."""
        if chosen_side in ("left", "right"):
            choice = side_assignment[chosen_side]
        elif chosen_side == "cannot_tell":
            choice = "cannot_tell"
        else:
            raise ValueError(f"chosen_side must be left/right/cannot_tell, got {chosen_side!r}")
        rec = {
            "rater": rater_id,
            "trial_id": trial_id,
            "pair_id": pair_id,
            "pair_type": pair_type,
            "dimension": dimension,
            "viewport": viewport,
            "choice": choice,
            "chosen_side": chosen_side,
            "side_assignment": side_assignment,
            "ms_elapsed": int(ms_elapsed),
            "is_catch": bool(is_catch),
            "known_worse": known_worse,
            "rubric_version": RUBRIC_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        with self.path(rater_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def completed(self, rater_id: str) -> set[tuple[str, str]]:
        """Return the set of ``(pair_id, dimension)`` this rater has already judged."""
        path = self.path(rater_id)
        if not path.exists():
            return set()
        done: set[tuple[str, str]] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                obj = json.loads(line)
                done.add((obj["pair_id"], obj["dimension"]))
        return done


# --------------------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------------------

_PAGE_CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:0;
color:#1a1a1a;background:#f4f4f5}
header{background:#111827;color:#fff;padding:12px 20px}
main{padding:20px;max-width:1500px;margin:0 auto}
.stage{display:flex;gap:20px;justify-content:center;align-items:flex-start;flex-wrap:wrap}
.panel{background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:8px}
.panel h3{margin:4px 0 8px;text-align:center;font-size:14px;color:#6b7280}
iframe{border:1px solid #e5e7eb;background:#fff}
.anchors{background:#fff;border:1px solid #d1d5db;border-radius:8px;padding:14px 18px;margin:14px 0}
.anchors li{margin:4px 0}
.controls{display:flex;gap:12px;justify-content:center;margin-top:14px}
button{font-size:15px;padding:12px 20px;border-radius:8px;border:1px solid #111827;cursor:pointer}
.pick{background:#111827;color:#fff}
.tell{background:#fff;color:#6b7280;border-color:#d1d5db}
.prompt{font-size:20px;font-weight:600;text-align:center;margin:6px 0}
.non{color:#6b7280;font-size:13px}
a{color:#2563eb}
"""

_LOGIN_HTML = """<!doctype html><meta charset="utf-8"><title>UIJudgeBench design annotation</title>
<style>{css}</style>
<header><b>UIJudgeBench</b> — design-quality annotation ({version})</header>
<main>
<h2>Sign in</h2>
<p>You will compare pairs of web pages, one design dimension at a time, and choose which
page is better on that dimension. This is a <b>forced choice</b>: pick a side. Use
"cannot tell" only when the two are genuinely indistinguishable on that dimension.</p>
<p><a href="/rubric?rater_id=">Read the rubric first &rarr;</a></p>
<form method="POST" action="/login">
<p><label>Rater id (no spaces): <input name="rater_id" required pattern="[A-Za-z0-9_-]+"></label></p>
<p><label>Optional: role / demographics <input name="demographics" size="40"></label></p>
<p><button class="pick" type="submit">Start</button></p>
</form>
</main>
"""


def _read_rubric_html(rater_id: str) -> str:
    rows = []
    for d in DIMENSIONS_BY_KEY.values():
        anchors = "".join(f"<li>{html.escape(a)}</li>" for a in d.anchors)
        nons = "".join(f"<li>{html.escape(n)}</li>" for n in d.non_criteria)
        rows.append(
            f"<h3>{html.escape(d.title)} <code>({html.escape(d.code)})</code></h3>"
            f"<p>{html.escape(d.definition)}</p>"
            f"<p><b>Endorse the page where:</b></p><ul>{anchors}</ul>"
            f"<p class='non'><b>Does NOT judge:</b></p><ul class='non'>{nons}</ul>"
        )
    back = f"/trial?rater_id={html.escape(rater_id)}" if rater_id else "/"
    return (
        f"<!doctype html><meta charset='utf-8'><title>Rubric</title><style>{_PAGE_CSS}</style>"
        f"<header><b>UIJudgeBench</b> rubric {RUBRIC_VERSION}</header><main>"
        + "".join(rows)
        + f"<p><a href='{back}'>&larr; back</a></p></main>"
    )


def make_handler(pairs: list[dict[str, Any]], judgments_dir: Path | str):
    """Build a request-handler class bound to a pair set and a judgments directory."""
    store = JudgmentStore(judgments_dir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the test output quiet
            pass

        def _send(self, body: str, status: int = 200, ctype: str = "text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _redirect(self, location: str):
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        def _body_params(self) -> dict[str, str]:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            return {k: v[0] for k, v in parse_qs(raw).items()}

        # -- GET --------------------------------------------------------------
        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/login"):
                self._send(_LOGIN_HTML.format(css=_PAGE_CSS, version=RUBRIC_VERSION))
            elif u.path == "/rubric":
                self._send(_read_rubric_html(q.get("rater_id", "")))
            elif u.path == "/trial":
                self._send_trial(q.get("rater_id", ""))
            elif u.path == "/page":
                self._serve_corpus_file(q.get("path", ""))
            elif u.path == "/done":
                self._send("<!doctype html><title>Done</title><h2>All trials complete. Thank you.</h2>")
            else:
                self._send("not found", status=404, ctype="text/plain")

        # -- POST -------------------------------------------------------------
        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/login":
                p = self._body_params()
                self._redirect(f"/trial?rater_id={p.get('rater_id', '')}")
            elif u.path == "/judge":
                self._handle_judge(self._body_params())
            else:
                self._send("not found", status=404, ctype="text/plain")

        # -- helpers ----------------------------------------------------------
        def _next_trial(self, rater_id: str) -> Trial | None:
            done = store.completed(rater_id)
            for t in build_trials(pairs, rater_id):
                if (t.pair_id, t.dimension) not in done:
                    return t
            return None

        def _send_trial(self, rater_id: str):
            if not rater_id:
                self._redirect("/")
                return
            t = self._next_trial(rater_id)
            if t is None:
                self._redirect("/done")
                return
            left_id, right_id = assign_sides(rater_id, t.trial_id, t.member_a["page_id"], t.member_b["page_id"])
            by_id = {t.member_a["page_id"]: t.member_a, t.member_b["page_id"]: t.member_b}
            w, h = VIEWPORT_PX.get(t.viewport, VIEWPORT_PX["desktop"])
            scale = min(1.0, 680 / w)  # fit two panels side by side
            dim = DIMENSIONS_BY_KEY[t.dimension]
            anchors = "".join(f"<li>{html.escape(a)}</li>" for a in dim.anchors)
            nons = "; ".join(html.escape(n) for n in dim.non_criteria)

            def panel(title: str, page_id: str) -> str:
                src = "/page?path=" + html.escape(by_id[page_id]["page_path"])
                return (
                    f"<div class='panel'><h3>{title}</h3>"
                    f"<iframe src='{src}' width='{w}' height='{h}' "
                    f"style='transform:scale({scale});transform-origin:top left;"
                    f"width:{w}px;height:{h}px' scrolling='no'></iframe></div>"
                )

            panel_w = int(w * scale)
            panel_h = int(h * scale)
            body = f"""<!doctype html><meta charset="utf-8"><title>Trial</title><style>{_PAGE_CSS}
.panel iframe{{width:{w}px;height:{h}px}}
.panel{{width:{panel_w}px;height:{panel_h + 20}px;overflow:hidden}}</style>
<header><b>UIJudgeBench</b> — {html.escape(rater_id)} &middot; <a style="color:#93c5fd"
href="/rubric?rater_id={html.escape(rater_id)}">rubric</a></header>
<main>
<div class="prompt">{html.escape(dim.prompt)}</div>
<div class="stage">{panel("Left", left_id)}{panel("Right", right_id)}</div>
<div class="anchors"><b>Prefer the page where:</b><ul>{anchors}</ul>
<span class="non"><b>Not judged:</b> {nons}</span></div>
<form method="POST" action="/judge" id="f">
<input type="hidden" name="rater_id" value="{html.escape(rater_id)}">
<input type="hidden" name="trial_id" value="{html.escape(t.trial_id)}">
<input type="hidden" name="pair_id" value="{html.escape(t.pair_id)}">
<input type="hidden" name="pair_type" value="{html.escape(t.pair_type)}">
<input type="hidden" name="dimension" value="{html.escape(t.dimension)}">
<input type="hidden" name="viewport" value="{html.escape(t.viewport)}">
<input type="hidden" name="left_page_id" value="{html.escape(left_id)}">
<input type="hidden" name="right_page_id" value="{html.escape(right_id)}">
<input type="hidden" name="is_catch" value="{"1" if t.is_catch else "0"}">
<input type="hidden" name="known_worse" value="{html.escape(t.known_worse or "")}">
<input type="hidden" name="chosen_side" id="chosen_side" value="">
<input type="hidden" name="ms_elapsed" id="ms_elapsed" value="0">
<div class="controls">
<button class="pick" type="button" onclick="pick('left')">Left is better</button>
<button class="tell" type="button" onclick="pick('cannot_tell')">Genuinely cannot tell</button>
<button class="pick" type="button" onclick="pick('right')">Right is better</button>
</div></form>
<script>
var t0=Date.now();
function pick(side){{document.getElementById('chosen_side').value=side;
document.getElementById('ms_elapsed').value=Date.now()-t0;document.getElementById('f').submit();}}
</script>
</main>"""
            self._send(body)

        def _handle_judge(self, p: dict[str, str]):
            side_assignment = {"left": p["left_page_id"], "right": p["right_page_id"]}
            store.record(
                rater_id=p["rater_id"],
                trial_id=p["trial_id"],
                pair_id=p["pair_id"],
                pair_type=p.get("pair_type", "preference"),
                dimension=p["dimension"],
                viewport=p.get("viewport", "desktop"),
                side_assignment=side_assignment,
                chosen_side=p["chosen_side"],
                ms_elapsed=int(p.get("ms_elapsed", 0)),
                is_catch=p.get("is_catch", "0") == "1",
                known_worse=(p.get("known_worse") or None),
            )
            self._redirect(f"/trial?rater_id={p['rater_id']}")

        def _serve_corpus_file(self, rel: str):
            # Sandbox: only files under corpus/ may be served.
            if not rel:
                self._send("missing path", status=400, ctype="text/plain")
                return
            target = (REPO_ROOT / rel).resolve()
            if not str(target).startswith(str(CORPUS_ROOT.resolve())) or not target.is_file():
                self._send("forbidden", status=403, ctype="text/plain")
                return
            ctype = "text/html; charset=utf-8" if target.suffix == ".html" else "image/png"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _demo_pairs() -> list[dict[str, Any]]:
    """Three bundled pairs (2 preference + 1 validity catch) for smoke-testing the flow."""
    from .pairs import build_pairs

    sampled = build_pairs(seed=1, n_validity=1, n_preference=2)
    return sampled or []


def serve(pairs: list[dict[str, Any]], judgments_dir: Path, host: str, port: int) -> None:
    """Start the blocking annotation server."""
    handler = make_handler(pairs, judgments_dir)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Design-track annotation app on http://{host}:{port}  ({len(pairs)} pairs)")
    print(f"Judgments -> {judgments_dir}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Design-track annotation app.")
    ap.add_argument("--demo", action="store_true", help="serve 3 bundled pairs")
    ap.add_argument("--pairs", type=Path, default=PAIRS_FILE, help="pairs JSONL to serve")
    ap.add_argument("--judgments", type=Path, default=REPO_ROOT / "design_track" / "judgments")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    pairs = _demo_pairs() if args.demo else load_pairs(args.pairs)
    if not pairs:
        print("no pairs to serve (build them: python -m uijudge.design_track.pairs --build)")
        return 1
    serve(pairs, args.judgments, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
