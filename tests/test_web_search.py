"""Testes do port de busca web (core/web_search — DeepResearch Fase A)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import web_search as ws


def test_no_key_raises_controlled(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "tavily")
    with pytest.raises(ws.WebSearchError):
        ws.web_search("qualquer coisa")


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "foobar")
    with pytest.raises(ws.WebSearchError):
        ws.web_search("x")


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_tavily_parsing(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    payload = {"results": [
        {"title": "T1", "url": "http://a", "content": "snippet a",
         "raw_content": "x" * 5000},
        {"title": "T2", "url": "http://b", "content": "snippet b"},  # sem raw_content
    ]}
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp(payload)

    monkeypatch.setattr(ws.requests, "post", fake_post)
    hits = ws.web_search("consulta", k=2)

    assert captured["url"] == ws._TAVILY_URL
    assert captured["json"]["query"] == "consulta"
    assert len(hits) == 2
    # raw_content truncado ao limite
    assert len(hits[0].content) == ws._CONTENT_CHAR_LIMIT
    assert hits[0].url == "http://a" and hits[0].title == "T1"
    # fallback: sem raw_content usa content
    assert hits[1].content == "snippet b"


def test_k_clamped(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BACKEND", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "fake")
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["max_results"] = json["max_results"]
        return _FakeResp({"results": []})

    monkeypatch.setattr(ws.requests, "post", fake_post)
    ws.web_search("q", k=50)
    assert seen["max_results"] == 10  # clamp em 10
