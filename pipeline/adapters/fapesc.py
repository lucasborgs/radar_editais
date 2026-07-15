"""
FAPESC Source Adapter — L1 (WIKI.md §12, wikis/fapesc.md).

O texto autoritativo do edital FAPESC vive num PDF anexo (não no HTML do post —
ver wikis/fapesc.md §8). O scraper (pipeline/extractors/fapesc.py) já baixa esse
PDF e extrai o texto inline, gravando-o em `texto_cru`. Este adapter lê o bronze,
encontra a chamada pelo `native_id` (gravado pelo scraper) e retorna 1 entrada de
Documento Canônico (§12.3) com o corpo fatiado em units — agnóstico de onde o
texto veio (PDF ou, em fallback, o resumo HTML).

Estratégia §12.4: `pdf` (extração no scraper). O adapter só fatia `texto_cru`.
"""
from __future__ import annotations

import json
import logging

from config import BRONZE_DIR

from .base import CanonicalDoc, SourceAdapter, coletado_em, split_into_units

logger = logging.getLogger(__name__)

_BRONZE_DIR = BRONZE_DIR / "fapesc_raw"


def _load_latest_bronze() -> list[dict]:
    """Lê o JSON bronze FAPESC mais recente. [] se diretório/arquivos ausentes."""
    if not _BRONZE_DIR.exists():
        logger.warning("fapesc adapter: bronze dir ausente: %s", _BRONZE_DIR)
        return []
    files = sorted(_BRONZE_DIR.glob("*.json"))
    if not files:
        logger.warning("fapesc adapter: nenhum bronze em %s", _BRONZE_DIR)
        return []
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("fapesc adapter: erro lendo %s: %s", files[-1].name, e)
        return []


class Adapter(SourceAdapter):
    """L1 FAPESC — bronze JSON → Documento Canônico (1 doc, units do texto_cru)."""

    def to_documents(self, edital_id: str) -> CanonicalDoc:
        """Recebe o native_id (ex.: '37-2026') e retorna o conteúdo da chamada
        como Documento Canônico fatiado em units (o `texto_cru` do bronze)."""
        bronze = _load_latest_bronze()
        if not bronze:
            return []

        # Dedup interno por URL — primeira ocorrência vence; casa por native_id.
        seen: set[str] = set()
        match: dict | None = None
        for ch in bronze:
            url = (ch.get("url") or "").replace("http://", "https://").rstrip("/")
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            if ch.get("native_id") == edital_id:
                match = ch
                break

        if match is None:
            logger.info("fapesc adapter: chamada %s não encontrada no bronze", edital_id)
            return []

        normative = match.get("documentos_normativos") or []
        if normative:
            documents: CanonicalDoc = []
            for index, item in enumerate(normative):
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                documents.append({
                    "doc_name": item.get("doc_name") or f"documento-{index + 1}",
                    "units": split_into_units(text),
                    "metadata": {
                        "family": item.get("family") or "edital-base",
                        "revision": index,
                        "source_url": item.get("url") or "",
                        "authority_state": "vigente",
                        "composition_order": index,
                    },
                })
            if documents:
                return documents

        texto = (match.get("texto_cru") or "").strip()
        if not texto:
            logger.info("fapesc adapter: chamada %s sem texto_cru — sem documento", edital_id)
            return []

        return [{
            "doc_name": "pagina-chamada",
            "units": split_into_units(texto),
            "metadata": {
                "family": "edital-base", "revision": 0,
                "source_url": match.get("edital_pdf_url") or match.get("url") or "",
                "authority_state": "vigente", "composition_order": 0,
            },
        }]

    def provenance(self, edital_id: str) -> dict:
        """URL oficial (`url`) + PDF anexo (`edital_pdf_url`) + data de coleta do
        bronze FAPESC, casando por `native_id`."""
        for ch in _load_latest_bronze():
            if ch.get("native_id") != edital_id:
                continue
            url = (ch.get("url") or "").replace("http://", "https://").rstrip("/")
            prov: dict = {"fonte": "fapesc"}
            if url:
                prov["url"] = url
            urls = [
                d.get("url") for d in (ch.get("documentos_normativos") or [])
                if d.get("url")
            ]
            if not urls and ch.get("edital_pdf_url"):
                urls = [ch["edital_pdf_url"]]
            if urls:
                prov["urls_documentos"] = urls
            if coletado_em(ch):
                prov["coletado_em"] = coletado_em(ch)
            return prov
        return {}
