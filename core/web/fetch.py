"""Fetch HTTP + busca web canônicos, com cache TTL (Finding E).

Antes desta camada havia ~3 implementações de fetch/web sem cache, e
`deep_research.fetch_url` reusava `profile_tools._fetch_and_parse` (acoplamento
invertido: pesquisa dependendo de internals de uma tool de perfil). Agora:

  • `fetch_and_parse(url)`  → dict {text, title, links} — texto limpo + links.
                             É a unidade que o cache guarda. profile_tools usa o
                             dict inteiro (precisa de links/title); deep_research
                             usa só text/title via `fetch_url`.
  • `fetch_url(url, *, char_limit)` → str — texto limpo truncado, formato
                             "title\\n{text}". API simples pro deep_research.
  • `web_search(query, k)`  → list[SearchHit] — wrapper canônico sobre
                             core.web_search, com o mesmo cache TTL.

Cache: dict em memória por (kind, chave-normalizada), com expiração TTL.
TTL configurável por env `WEB_CACHE_TTL` (segundos, default 900 = 15 min) e
desligável com `WEB_CACHE_TTL=0`. Curto de propósito — o risco do cache é servir
conteúdo velho (ver spec 03). O cache guarda o objeto parseado/os hits, então um
cache-hit não faz NENHUM I/O de rede.

IMPORTANTE: a limpeza de HTML em `fetch_and_parse` é byte-a-byte idêntica à antiga
`profile_tools._fetch_and_parse` — não mudar o texto que o LLM vê.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from bs4 import BeautifulSoup

from core import web_search as _ws
from core.infra.net_guard import safe_get
from core.web_search import SearchHit, WebSearchError  # re-export p/ chamadores

logger = logging.getLogger(__name__)

# Limites/headers — herdados de profile_tools._fetch_and_parse (não mudar).
PAGE_CHAR_LIMIT = 12000          # cap de fetch_and_parse; chamador trunca ainda mais
_HTTP_TIMEOUT = 12.0
_USER_AGENT = "Mozilla/5.0 (compatible; RadarEditais-Agent/1.0)"

__all__ = [
    "fetch_and_parse",
    "fetch_url",
    "web_search",
    "clear_cache",
    "SearchHit",
    "WebSearchError",
]


def _ttl() -> float:
    """TTL do cache em segundos (env WEB_CACHE_TTL, default 900). 0 = desligado."""
    try:
        return float(os.getenv("WEB_CACHE_TTL", "900"))
    except (TypeError, ValueError):
        return 900.0


# Cache em memória: chave -> (expira_em_epoch, valor). Lido a cada processo.
_cache: dict[tuple[str, str], tuple[float, Any]] = {}


def _cache_get(kind: str, key: str) -> Any | None:
    entry = _cache.get((kind, key))
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        _cache.pop((kind, key), None)
        return None
    return value


def _cache_put(kind: str, key: str, value: Any) -> None:
    ttl = _ttl()
    if ttl <= 0:
        return
    _cache[(kind, key)] = (time.monotonic() + ttl, value)


def clear_cache() -> None:
    """Esvazia o cache (uso em testes)."""
    _cache.clear()


def fetch_and_parse(url: str) -> dict[str, Any]:
    """HTTP GET + extração de texto limpo e lista de (texto, href).

    Retorna {"text", "title", "links"}. `text` truncado em PAGE_CHAR_LIMIT.
    Levanta exceção em falha (requests.* ou ValueError do anti-SSRF) — o chamador
    (tool) captura e converte em string-erro. Cacheado por TTL: um hit não faz
    I/O de rede.

    A limpeza de HTML é idêntica à antiga profile_tools._fetch_and_parse.
    """
    cached = _cache_get("page", url)
    if cached is not None:
        return cached

    resp = safe_get(
        url,
        headers={"User-Agent": _USER_AGENT},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else url
    text = " ".join(soup.get_text(separator=" ").split())
    text = text[:PAGE_CHAR_LIMIT]

    links: list[dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        link_text = " ".join(a.get_text(separator=" ").split())[:80]
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        links.append({"text": link_text, "href": href})

    data = {"text": text, "title": title, "links": links}
    _cache_put("page", url, data)
    return data


def fetch_url(url: str, *, char_limit: int = PAGE_CHAR_LIMIT) -> str:
    """Lê uma URL e devolve texto limpo truncado em `char_limit`, formato
    "title\\n{text}".

    `char_limit` é o cap específico do chamador (ex.: 3000 no deep_research). Como
    `fetch_and_parse` já trunca em PAGE_CHAR_LIMIT, qualquer char_limit <= 12000
    produz exatamente o mesmo texto de antes. Levanta exceção em falha de rede.
    """
    data = fetch_and_parse(url)
    return f"{data.get('title', url)}\n{data.get('text', '')[:char_limit]}"


def web_search(query: str, k: int = 5) -> list[SearchHit]:
    """Busca web canônica (wrapper sobre core.web_search), com cache TTL.

    Chave de cache = (query normalizada, k). Repete os mesmos SearchHit num hit,
    sem chamar o provedor de novo. Propaga WebSearchError/requests.* como o
    original — quem chama converte em string amigável.
    """
    key = f"{query.strip().lower()}|{int(k)}"
    cached = _cache_get("search", key)
    if cached is not None:
        return cached

    hits = _ws.web_search(query, k)
    _cache_put("search", key, hits)
    return hits
