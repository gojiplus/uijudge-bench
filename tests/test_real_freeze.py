"""Browser-marked tests for the freeze pipeline and real-page mutation.

A synthetic fixture (with an external stylesheet, an external image, and a script) is
served over a local HTTP server *as if it were a remote page*, then frozen. We assert the
frozen artifact is self-contained (opening it fires zero external network requests), the
re-render stability receipt is present, the dom/styles/axe sidecars are well-formed, and
the P2 mutation engine (via generic real-page target selection) fires with a measured
receipt while the clean-twin control does not.

Run with: ``uv run pytest -m browser`` (requires ``playwright install chromium``).
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from uijudge.engine.freeze import Freezer
from uijudge.engine.real_mutate import applicable_classes, real_mutate
from uijudge.engine.verify import Verifier
from uijudge.schema import validate_page_record

pytestmark = pytest.mark.browser

_FIXTURE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Freeze Fixture</title>
  <link rel="stylesheet" href="style.css">
  <link rel="icon" href="favicon.ico">
  <!--[if lt IE 9]>
    <script src="//cdnjs.cloudflare.com/ajax/libs/html5shiv/3.6.2/html5shiv.min.js"></script>
  <![endif]-->
</head>
<body>
  <header><h1>Fixture Landing</h1></header>
  <main>
    <h2>Welcome</h2>
    <p class="lead">This is a reasonably long paragraph of body text that should be recorded
       above the size floor and offer a target for contrast and clip mutations on the page.</p>
    <h3>Details</h3>
    <p>Another paragraph with enough words to be a real text block for the freeze pipeline.</p>
    <img src="pixel.png" alt="A descriptive alt text for the fixture image element here">
    <a href="/somewhere" class="btn">A reasonably sized call to action link element</a>
  </main>
  <footer><p>Footer text content for the fixture page.</p></footer>
  <script>document.body.dataset.hydrated = "yes";</script>
</body>
</html>
"""

# 1x1 red PNG.
_PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c49444154789c6360000002000154a24f2f0000000049454e44ae426082"
)
_CSS = "body{font-family:sans-serif;margin:0} .lead{font-size:18px;color:#111} .btn{padding:12px 20px;display:inline-block}"


def _serve(dir_path: Path) -> tuple[http.server.HTTPServer, int]:
    """Start a threaded HTTP server rooted at ``dir_path``; return (server, port)."""
    with socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(dir_path), **k)  # noqa: E731
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


@pytest.fixture()
def served(tmp_path):
    """Write the fixture + its resources to a temp dir and serve them over HTTP."""
    (tmp_path / "index.html").write_text(_FIXTURE_HTML, encoding="utf-8")
    (tmp_path / "style.css").write_text(_CSS, encoding="utf-8")
    (tmp_path / "pixel.png").write_bytes(_PIXEL)
    (tmp_path / "favicon.ico").write_bytes(_PIXEL)
    httpd, port = _serve(tmp_path)
    try:
        yield f"http://127.0.0.1:{port}/index.html"
    finally:
        httpd.shutdown()


def _freeze(url: str, out_root: Path):
    async def run():
        async with Freezer() as fz:
            return await fz.freeze(
                url,
                "fixture-page",
                tier="tier-a",
                genre="landing",
                license_info={"license": "Test", "evidence": "fixture", "license_url": ""},
                retrieval_date="2026-07-22",
                corpus_root=out_root,
                viewports=["desktop", "mobile"],
            )

    return asyncio.run(run())


def test_freeze_produces_self_contained_stable_artifact(served, tmp_path):
    """Freeze the served fixture; assert self-containment, stability, and valid sidecars."""
    out = tmp_path / "corpus"
    fr = _freeze(served, out)
    assert fr is not None and fr.stable, f"freeze unstable: {fr.stability if fr else None}"

    page_dir = out / "real" / "fixture-page"
    for name in (
        "page.html",
        "provenance.json",
        "dom.json",
        "computed_styles.json",
        "axe.json",
        "screenshot_desktop.png",
        "screenshot_mobile.png",
    ):
        assert (page_dir / name).exists(), f"missing artifact {name}"

    html = (page_dir / "page.html").read_text(encoding="utf-8")
    assert "<script" not in html.lower(), "scripts must be stripped from the frozen page"
    assert "http://" not in html and "https://" not in html.replace("http-equiv", ""), "no external URLs"
    assert "data:image" in html, "the fixture image should be inlined as a data URI"

    prov = json.loads((page_dir / "provenance.json").read_text())
    validate_page_record(prov)
    stab = prov["metadata"]["stability"]
    assert stab["stable"] is True
    assert stab["bbox_digest"] and stab["screenshot_dims"], "stability receipt must carry measurements"
    assert prov["metadata"]["canary_in_html"] is True  # tier-A gets the canary

    dom = json.loads((page_dir / "dom.json").read_text())
    assert dom["captured"], "dom.json should record elements above the size floor"
    assert all(e.get("selector") for e in dom["captured"]), "every recorded element has a selector"

    axe = json.loads((page_dir / "axe.json").read_text())
    assert "violations" in axe and "passes" in axe


def test_frozen_page_fires_no_external_requests(served, tmp_path):
    """The strongest self-containment check: opening the frozen page makes zero external requests."""
    out = tmp_path / "corpus"
    fr = _freeze(served, out)
    assert fr is not None and fr.stable
    frozen = out / "real" / "fixture-page" / "page.html"

    async def run():
        from playwright.async_api import async_playwright

        external: list[str] = []
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            page = await b.new_page()
            page.on(
                "request", lambda req: external.append(req.url) if not req.url.startswith(("file:", "data:")) else None
            )
            await page.goto(frozen.resolve().as_uri(), wait_until="networkidle")
            await b.close()
        return external

    external = asyncio.run(run())
    assert external == [], f"frozen page issued external requests: {external}"


def test_real_mutation_fires_and_clean_control_passes(served, tmp_path):
    """A real-page mutation render-verifies on the frozen fixture; the clean control does not."""
    out = tmp_path / "corpus"
    fr = _freeze(served, out)
    assert fr is not None and fr.stable
    clean_html = (out / "real" / "fixture-page" / "page.html").read_text(encoding="utf-8")

    from bs4 import BeautifulSoup

    classes = applicable_classes(fr.dom, BeautifulSoup(clean_html, "html.parser"))
    assert classes, "the fixture should offer at least one applicable mutation class"
    assert "target:shrink" not in classes
    assert "protrude:viewport" in classes

    async def run():
        res = real_mutate(clean_html, "protrude:viewport", 123, fr.dom)
        mp = tmp_path / "mutated.html"
        mp.write_text(res.mutated_html, encoding="utf-8")
        cp = tmp_path / "clean.html"
        cp.write_text(clean_html, encoding="utf-8")
        async with Verifier() as v:
            receipt = await v.verify(mp, res.injection_record)
            control, fires = await v.verify_control(cp, res.injection_record)
        return res, receipt, control, fires

    res, receipt, control, fires = asyncio.run(run())
    assert receipt is not None, "real mutation did not render-verify"
    assert receipt["verified"] is True and receipt["measured"], "receipt must carry measured values"
    assert res.injection_record["selector"].startswith("#"), "target selector should be a stable id"
    assert fires is False, "clean-twin control wrongly fired on the unmutated fixture"
