"""
FINEP Source Adapter — L1 (WIKI.md §12).

Converte PDFs em `bronze_data/finep_pdfs/{edital_id}/` em Documento Canônico
(§12.3): por PDF, lista de texto por página. Faz a descoberta (skip por
keyword + dedup por versão) e a extração per-página com pdfplumber.

Esta lógica era FINEP-específica e vivia espalhada em core/tasks.py
(descoberta) e core/structurer.py (extração) — consolidada aqui por ser
L1 (per-fonte), não L2 (agnóstico).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from config import FINEP_PDFS_DIR

from .base import CanonicalDoc, SourceAdapter

logger = logging.getLogger(__name__)


# =============================================================================
# DESCOBERTA — qual PDF do edital é relevante (ex-tasks.py)
# =============================================================================

# Skip por keyword: arquivos que não acrescentam ao RAG nem à síntese
# (minuta, declarações, cartas, slides, ofícios, telas, ebook, peças judiciais).
# FONTE AUTORITATIVA = wikis/finep.md §4.2 (lido via wiki_schema.skip_keywords).
# Esta constante é só FALLBACK defensivo se o doc estiver ausente/ilegível.
_SKIP_KEYWORDS_FALLBACK = [
    "minuta", "declaracao", "carta_de_manifestacao",
    "apresentacao", "resultado", "oficio", "telas", "guia",
    "orientacoes_para_apresentacao",
    "orientacoes_para_despesas", "relatorio_parcial", "ebook", "agravo",
]


def _skip_keywords() -> list[str]:
    """Skip-list autoritativa do doc (wikis/finep.md §4.2); fallback à constante."""
    from core.kg import schema  # import tardio: evita ciclo no import do pipeline
    return schema.skip_keywords("finep") or _SKIP_KEYWORDS_FALLBACK

_FAQ_VERSION_RE = re.compile(r"vers[aã]o[_\s\-]*(\d+)")
_FAQ_DATE_RE = re.compile(r"(\d{2})[_\s\-]?(\d{2})[_\s\-]?(\d{4})")
_EDITAL_RERR_NUM_RE = re.compile(r"(\d+)[ªº°][_\s\-]*rerratifica")
_FILENAME_TOKEN_RE = re.compile(r"[_\s\-.]+")


def _version_info(stem: str) -> tuple[str | None, int]:
    """Classifica um PDF num grupo de versionamento + score de recência.

    Retorna `(group, recency)`. `group=None` significa "não-versionado" —
    deve ser preservado como-está. Recency é monotônico: maior = mais novo.
    """
    s = stem.lower()
    tokens = [t for t in _FILENAME_TOKEN_RE.split(s) if t]
    if not tokens:
        return (None, 0)

    is_faq = (tokens[0] == "faq"
              or ("perguntas" in tokens and "frequentes" in tokens))
    if is_faq:
        recency = 0
        m = _FAQ_VERSION_RE.search(s)
        if m:
            recency += int(m.group(1)) * 1000
        m = _FAQ_DATE_RE.search(s)
        if m:
            d, mo, y = m.groups()
            recency += int(y) * 10000 + int(mo) * 100 + int(d)
        return ("__faq__", recency)

    has_edital_tok = "edital" in tokens
    has_rerr_tok = any("rerratificad" in t for t in tokens)
    if has_edital_tok and (has_rerr_tok or s == "edital"):
        recency = 0
        m = _EDITAL_RERR_NUM_RE.search(s)
        if m:
            recency = int(m.group(1)) * 1000
        elif has_rerr_tok:
            recency = 500
        return ("__edital__", recency)

    return (None, 0)


def _filter_to_latest_versions(pdfs: list[Path]) -> list[Path]:
    """Mantém só a versão mais recente de cada grupo conhecido. Arquivos
    não-versionados passam livres."""
    groups: dict[str, list[tuple[int, Path]]] = {}
    keep: list[Path] = []
    for pdf in pdfs:
        group, recency = _version_info(pdf.stem)
        if group is None:
            keep.append(pdf)
            continue
        groups.setdefault(group, []).append((recency, pdf))

    for group, items in groups.items():
        items.sort(key=lambda x: x[0], reverse=True)
        winner = items[0][1]
        keep.append(winner)
        for _, loser in items[1:]:
            logger.info(
                "finep adapter: versão antiga descartada (group=%s, vencedor=%s): %s",
                group, winner.name, loser.name,
            )
    keep.sort(key=lambda p: p.name)
    return keep


# =============================================================================
# EXTRAÇÃO — per página, plain pdfplumber (ex-structurer.extract_pages)
# =============================================================================

def _extract_pages(pdf_path: Path) -> list[str]:
    """Texto por página. Vazio em falha — caller decide o fallback."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return [(p.extract_text() or "") for p in pdf.pages]
    except Exception as e:
        logger.warning("finep adapter: erro ao extrair %s: %s", pdf_path.name, e)
        return []


# =============================================================================
# ADAPTER
# =============================================================================

class Adapter(SourceAdapter):
    """L1 da FINEP — PDFs em FINEP_PDFS_DIR/<id>/ → Documento Canônico."""

    def to_documents(self, edital_id: str) -> CanonicalDoc:
        pdf_dir = FINEP_PDFS_DIR / edital_id
        if not pdf_dir.exists():
            logger.warning("finep adapter: diretório não encontrado: %s", pdf_dir)
            return []

        skip = _skip_keywords()
        candidates = [
            p for p in sorted(pdf_dir.glob("*.pdf"))
            if not any(kw in p.stem.lower() for kw in skip)
        ]
        candidates = _filter_to_latest_versions(candidates)

        documents: CanonicalDoc = []
        for pdf in candidates:
            pages = _extract_pages(pdf)
            if not any(p.strip() for p in pages):
                continue
            documents.append({"doc_name": pdf.name, "units": pages})
        return documents
