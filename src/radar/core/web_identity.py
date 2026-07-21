"""
Identidade de página web — normalização de URL + hash estável.

A fonte `web` (docs/domain/schema.md §12.4, strategy=html_clean) identifica cada página por um
hash do URL normalizado: `web:<url_hash>`. Vive em `core/` (não em
`pipeline/extractors/web.py`) porque DOIS produtores precisam do mesmo hash —
o WebScraper (seed list manual, em `pipeline/`) e a Descoberta
(`core/ingestion/opportunity_discovery.py`, busca livre por termos). Centralizar aqui
evita um import `core → pipeline` e garante que as duas torneiras produzam o
MESMO `url_hash` para a mesma página (senão viram dois editais).

Função pura: sem I/O, determinística.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

# Query params de tracking que não mudam o conteúdo — removidos na normalização
# para que a mesma página não gere url_hash diferente conforme o link de origem.
_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid")


def normalize_web_url(url: str) -> str:
    """Normaliza um URL para identidade estável: lowercase host, sem fragment,
    sem trailing slash, sem params de tracking. Determinístico → a mesma página
    sempre produz o mesmo `url_hash`, independente do link por onde chegou."""
    parts = urlsplit((url or "").strip())
    netloc = parts.netloc.lower()
    query = "&".join(
        kv for kv in parts.query.split("&")
        if kv and not kv.lower().startswith(_TRACKING_PREFIXES)
    )
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def web_url_hash(url: str) -> str:
    """`native_id` da fonte web: sha1 do URL normalizado, 12 hex chars.

    12 chars (48 bits) é colisão-seguro para milhares de URLs e mantém o
    edital_id (`web:ab12cd34ef56`) curto. Único lugar onde o hash é calculado;
    o adapter e o `_ID_EXTRACTORS['web']` apenas LEEM `url_hash` do bronze."""
    norm = normalize_web_url(url)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
