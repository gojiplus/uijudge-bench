"""The local page server shared by the harness and open_page."""

import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from uijudge.vendor.browser import serve_file


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


@pytest.fixture
def page(tmp_path: Path) -> Path:
    html = tmp_path / "page.html"
    html.write_text("<p>the page</p>")
    (tmp_path / "style.css").write_text("p { color: red }")
    return html


def test_root_serves_the_page(page: Path) -> None:
    with serve_file(page) as url:
        assert url.startswith("http://localhost:") and url.endswith("/")
        assert _get(url) == (200, b"<p>the page</p>")


def test_root_with_a_query_string_still_serves_the_page(page: Path) -> None:
    with serve_file(page) as url:
        assert _get(url + "?cache=1") == (200, b"<p>the page</p>")


def test_relative_assets_are_served_from_the_page_directory(page: Path) -> None:
    with serve_file(page) as url:
        assert _get(url + "style.css") == (200, b"p { color: red }")
        assert _get(url + "missing.css")[0] == 404


def test_the_port_is_released_on_exit(page: Path) -> None:
    with serve_file(page) as url:
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))


def test_cancelling_open_page_during_startup_stops_the_server(page: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from uijudge.vendor import browser

    servers = []
    make_server = browser._make_server

    def recording_make_server(path: Path):
        servers.append(make_server(path))
        return servers[-1]

    async def cancelled_sleep(_: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(browser, "_make_server", recording_make_server)
    monkeypatch.setattr(browser.asyncio, "sleep", cancelled_sleep)

    async def open_and_use() -> None:
        async with browser.open_page(page):
            pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(open_and_use())
    assert len(servers) == 1
    assert servers[0].socket.fileno() == -1
