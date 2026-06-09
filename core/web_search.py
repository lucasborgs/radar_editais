"""Port de busca web — abstração para não acoplar o DeepResearch a um provedor.

DeepResearch Fase A (ver docs/spec_deepresearch.md). Backend default: Tavily
(API feita para agentes — devolve conteúdo limpo + URLs explícitas). Chamada via
REST com `requests` (sem SDK extra). Trocar de provedor = novo `_<backend>_search`
+ `WEB_SEARCH_BACKEND`; o resto do sistema não muda.

Sem `TAVILY_API_KEY` → `WebSearchError` (controlada). Quem chama (a tool do
subagente) converte em string amigável — o loop nunca quebra.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT", "30"))
_CONTENT_CHAR_LIMIT = 2000   # por hit — evita estourar tokens do subagente


class WebSearchError(Exception):
    """Falha controlada de busca (config ausente, provedor fora). A tool que
    chama converte em string; nunca propaga pro loop do agente."""


@dataclass
class SearchHit:
    """Um resultado de busca. `content` já vem limpo (Tavily raw_content).

    `agency`: órgão emissor quando a FONTE já o conhece de forma confiável (ex.:
    feeder DOU lê do `artCategory` do XML). Vazio para fontes cegas (Tavily), que
    deixam a triagem LLM adivinhar. Quem extrai prefere este campo ao palpite."""
    title: str
    url: str
    snippet: str
    content: str
    agency: str = ""


def web_search(query: str, k: int = 5) -> list[SearchHit]:
    """Busca na web via backend configurado (default Tavily).

    Raises:
        WebSearchError: backend desconhecido ou credencial ausente.
        requests.RequestException: falha de rede/HTTP (a tool captura).
    """
    backend = os.getenv("WEB_SEARCH_BACKEND", "tavily").lower()
    if backend == "tavily":
        return _tavily_search(query, k)
    raise WebSearchError(f"WEB_SEARCH_BACKEND desconhecido: {backend!r}")


def _tavily_search(query: str, k: int) -> list[SearchHit]:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise WebSearchError("TAVILY_API_KEY não configurada")

    resp = requests.post(
        _TAVILY_URL,
        json={
            "api_key": api_key,
            "query": query,
            "max_results": max(1, min(int(k), 10)),
            "search_depth": "advanced",
            "include_raw_content": True,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    hits: list[SearchHit] = []
    for r in data.get("results", []):
        content = (r.get("raw_content") or r.get("content") or "")[:_CONTENT_CHAR_LIMIT]
        hits.append(SearchHit(
            title=r.get("title", "") or "",
            url=r.get("url", "") or "",
            snippet=(r.get("content", "") or "")[:300],
            content=content,
        ))
    return hits
