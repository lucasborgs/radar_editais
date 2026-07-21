"""Testes da camada web canônica (core/web/fetch.py) — Finding E.

Cobre: (a) fetch_url respeita char_limit; (b) segunda chamada à mesma URL é
cache-hit (transporte HTTP mockado é chamado exatamente 1 vez).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.web import fetch as web_fetch

pytestmark = pytest.mark.unit

_HTML = (
    "<html><head><title>  Acme Corp  </title></head>"
    "<body>"
    "<nav>menu menu menu</nav>"
    "<p>" + ("palavra " * 5000) + "</p>"
    "<a href='/sobre'>Sobre</a>"
    "<footer>rodape</footer>"
    "</body></html>"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    web_fetch.clear_cache()
    yield
    web_fetch.clear_cache()


def _fake_response(text: str = _HTML):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_url_respects_char_limit():
    with patch("core.web.fetch.safe_get", return_value=_fake_response()) as get:
        out_small = web_fetch.fetch_url("http://example.com", char_limit=100)
    # Formato "title\n{text}". O texto (após o \n) é truncado em char_limit.
    title, _, body = out_small.partition("\n")
    assert title == "Acme Corp"
    assert len(body) == 100
    assert get.call_count == 1


def test_fetch_url_larger_limit_returns_more():
    with patch("core.web.fetch.safe_get", return_value=_fake_response()):
        small = web_fetch.fetch_url("http://example.com/a", char_limit=100)
        big = web_fetch.fetch_url("http://example.com/b", char_limit=500)
    assert len(big.partition("\n")[2]) == 500
    assert len(small.partition("\n")[2]) == 100


def test_second_fetch_is_cache_hit_no_io():
    with patch("core.web.fetch.safe_get", return_value=_fake_response()) as get:
        first = web_fetch.fetch_url("http://example.com/page", char_limit=3000)
        second = web_fetch.fetch_url("http://example.com/page", char_limit=3000)
    assert first == second
    # I/O real (safe_get) ocorre exatamente uma vez: a 2a é servida do cache.
    assert get.call_count == 1


def test_cache_disabled_when_ttl_zero(monkeypatch):
    monkeypatch.setenv("WEB_CACHE_TTL", "0")
    web_fetch.clear_cache()
    with patch("core.web.fetch.safe_get", return_value=_fake_response()) as get:
        web_fetch.fetch_url("http://example.com/x", char_limit=100)
        web_fetch.fetch_url("http://example.com/x", char_limit=100)
    # TTL=0 desliga o cache → dois GETs reais.
    assert get.call_count == 2


def test_cache_expires_after_ttl(monkeypatch):
    monkeypatch.setenv("WEB_CACHE_TTL", "900")
    web_fetch.clear_cache()
    times = [1000.0]
    with patch("core.web.fetch.time.monotonic", side_effect=lambda: times[0]), \
         patch("core.web.fetch.safe_get", return_value=_fake_response()) as get:
        web_fetch.fetch_url("http://example.com/y", char_limit=100)
        times[0] = 1000.0 + 901  # passou do TTL
        web_fetch.fetch_url("http://example.com/y", char_limit=100)
    assert get.call_count == 2


def test_web_search_is_cached():
    hits = [web_fetch.SearchHit(title="t", url="u", snippet="s", content="c")]
    with patch("core.web.fetch._ws.web_search", return_value=hits) as ws_mock:
        r1 = web_fetch.web_search("alguma query", k=5)
        r2 = web_fetch.web_search("Alguma Query", k=5)  # normalizada (lower/strip)
    assert r1 == r2 == hits
    assert ws_mock.call_count == 1
