"""
FAPESP Source Adapter — L1 (WIKI.md §12, wikis/fapesp.md).

Diferente do FINEP (que extrai PDFs página-a-página), FAPESP serve o texto
autoritativo do edital inline no HTML da página individual. O bronze grava
esse texto em `texto_cru`. Adapter lê o bronze, encontra a chamada pelo
`native_id`, e retorna 1 entrada de Documento Canônico (§12.3) com o html_body
como unit única.

Estratégia §12.4: `html_body`. Sem download de PDF, sem `pdfplumber`.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from config import BRONZE_DIR

from .base import CanonicalDoc, SourceAdapter

logger = logging.getLogger(__name__)

_BRONZE_DIR = BRONZE_DIR / "fapesp_raw"
_URL_ID_RE = re.compile(r"/(\d+)(?:/|$)")

# Tamanho-alvo por unidade. O structurer reproduz texto VERBATIM, e o cliente
# LLM tem timeout de 60s (core.llm_client). gpt-4o-mini gera ~50-80 tok/s, então
# uma unit que force perto de max_tokens=4000 de saída (~40-80s) estoura o
# timeout. Mantemos ~3500 chars (~900 tokens → saída verbatim ~1000-1200 tokens)
# bem abaixo do limite de tempo. Sem split, FAPESP serve o edital inteiro como
# um corpo de 35k-100k chars e o structurer falhava (truncava → silver vazio, ou
# timeout). O contrato §12.3 já prevê "HTML → split por âncora".
_UNIT_MAX_CHARS = 3500


def _split_into_units(text: str, max_chars: int = _UNIT_MAX_CHARS) -> list[str]:
    """Quebra o corpo HTML em unidades ~page-sized, em fronteira de parágrafo.

    Mantém parágrafos inteiros juntos até estourar `max_chars`; cai para quebra
    por linha simples se não houver parágrafos; nunca devolve lista vazia para
    texto não-vazio.
    """
    paras = [p for p in text.split("\n\n") if p.strip()]
    if not paras:
        paras = [p for p in text.split("\n") if p.strip()] or [text]
    units: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > max_chars:
            units.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        units.append(buf)
    return units


def _load_latest_bronze() -> list[dict]:
    """Lê o JSON bronze FAPESP mais recente. [] se diretório/arquivos ausentes."""
    if not _BRONZE_DIR.exists():
        logger.warning("fapesp adapter: bronze dir ausente: %s", _BRONZE_DIR)
        return []
    files = sorted(_BRONZE_DIR.glob("*.json"))
    if not files:
        logger.warning("fapesp adapter: nenhum bronze em %s", _BRONZE_DIR)
        return []
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("fapesp adapter: erro lendo %s: %s", files[-1].name, e)
        return []


def _extract_native_id(url: str) -> str | None:
    """`https://fapesp.br/18064/...` → `'18064'`. None se URL não casa."""
    if not url:
        return None
    m = _URL_ID_RE.search(url)
    return m.group(1) if m else None


class Adapter(SourceAdapter):
    """L1 FAPESP — bronze JSON → Documento Canônico (1 unit/HTML body)."""

    def to_documents(self, edital_id: str) -> CanonicalDoc:
        """Recebe o native_id (ex.: '18064'), retorna o conteúdo da chamada
        como Documento Canônico de 1 unidade (o `texto_cru` do bronze)."""
        bronze = _load_latest_bronze()
        if not bronze:
            return []

        # Dedup interno por URL — primeira ocorrência vence (gotcha §8 wikis/fapesp.md)
        seen: set[str] = set()
        match: dict | None = None
        for ch in bronze:
            url = (ch.get("url") or "").replace("http://", "https://").rstrip("/")
            if not url or url in seen:
                continue
            seen.add(url)
            if _extract_native_id(url) == edital_id:
                match = ch
                break

        if match is None:
            logger.info("fapesp adapter: chamada %s não encontrada no bronze", edital_id)
            return []

        texto = (match.get("texto_cru") or "").strip()
        if not texto:
            logger.info("fapesp adapter: chamada %s sem texto_cru — sem documento", edital_id)
            return []

        return [{"doc_name": "pagina-chamada", "units": _split_into_units(texto)}]
