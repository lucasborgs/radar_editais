"""
FINEP Source Adapter — L1 (WIKI.md §12).

Converte PDFs em `bronze_data/finep_pdfs/{edital_id}/` em Documento Canônico
(§12.3): por PDF, lista de texto por página. Faz a descoberta (skip por
keyword + dedup por versão) e a extração per-página com pdfplumber.

Esta lógica era FINEP-específica e vivia espalhada em core/tasks.py
(descoberta) e core/ingestion/structurer.py (extração) — consolidada aqui por ser
L1 (per-fonte), não L2 (agnóstico).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from config import BRONZE_DIR, FINEP_PDFS_DIR

from .base import CanonicalDoc, SourceAdapter, coletado_em

logger = logging.getLogger(__name__)

_BRONZE_DIR = BRONZE_DIR / "finep_raw"


def _load_latest_bronze() -> list[dict]:
    """Lê o JSON bronze FINEP mais recente (lista de chamadas). [] se ausente."""
    if not _BRONZE_DIR.exists():
        return []
    files = sorted(_BRONZE_DIR.glob("*.json"))
    if not files:
        return []
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — bronze corrompido não derruba o build
        logger.warning("finep adapter: erro lendo %s: %s", files[-1].name, e)
        return []


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
    "aviso",
]


def _skip_keywords() -> list[str]:
    """Skip-list autoritativa do doc (wikis/finep.md §4.2); fallback à constante."""
    from core.kg import schema  # import tardio: evita ciclo no import do pipeline
    return schema.skip_keywords("finep") or _SKIP_KEYWORDS_FALLBACK


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()

_FAQ_VERSION_RE = re.compile(r"vers[aã]o[_\s\-]*(\d+)")
_FAQ_DATE_RE = re.compile(r"(\d{2})[_\s\-]?(\d{2})[_\s\-]?(\d{4})")
_RERR_NUM_RE = re.compile(r"(?:^|[_\s\-])(\d+)(?:[ªº°])?[_\s\-]*rerratifica")
_ANY_DATE_RE = re.compile(r"(?<!\d)(\d{2})[_.\s\-](\d{2})[_.\s\-](\d{4})(?!\d)")
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
    has_regulamento_tok = "regulamento" in tokens
    has_anexo_tok = "anexo" in tokens
    has_rerr_tok = any("rerratificad" in t for t in tokens)
    if has_edital_tok or has_regulamento_tok or has_anexo_tok:
        if has_regulamento_tok:
            family = "__regulamento__"
        elif has_anexo_tok:
            try:
                anexo_idx = tokens.index("anexo")
                anexo_num = tokens[anexo_idx + 1] if tokens[anexo_idx + 1].isdigit() else "geral"
            except (ValueError, IndexError):
                anexo_num = "geral"
            family = f"__anexo_{anexo_num}__"
        else:
            family = "__edital__"
        recency = 0
        date_match = _ANY_DATE_RE.search(s)
        if date_match:
            d, mo, y = date_match.groups()
            recency = int(y) * 10000 + int(mo) * 100 + int(d)
        m = _RERR_NUM_RE.search(s)
        if m:
            recency += int(m.group(1)) * 100_000_000
        elif has_rerr_tok:
            recency += 50_000_000
        return (family, recency)

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


def _versioned_documents(pdfs: list[Path]) -> list[tuple[Path, dict]]:
    """Classifica todas as versões, preservando históricas para auditoria."""
    classified = [(pdf, *_version_info(pdf.stem)) for pdf in pdfs]
    winners: dict[str, Path] = {}
    for pdf, group, recency in classified:
        if group is None:
            continue
        current = winners.get(group)
        if current is None or recency > _version_info(current.stem)[1]:
            winners[group] = pdf

    from core.kg import schema

    overrides = schema.document_authority_overrides("finep")
    result: list[tuple[Path, dict]] = []
    for pdf, group, recency in classified:
        date_match = _ANY_DATE_RE.search(pdf.stem.lower())
        published_at = None
        if date_match:
            d, mo, y = date_match.groups()
            published_at = f"{y}-{mo}-{d}"
        revision_match = _RERR_NUM_RE.search(pdf.stem.lower())
        metadata = {
            "family": (group or pdf.stem).strip("_"),
            "revision": int(revision_match.group(1)) if revision_match else 0,
            "published_at": published_at,
            "authority_state": (
                "superseded" if group and winners.get(group) != pdf else "vigente"
            ),
            "version_score": recency,
        }
        metadata.update(overrides.get(pdf.name) or {})
        result.append((pdf, metadata))
    return result


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
            if not any(_fold(kw) in _fold(p.stem) for kw in skip)
        ]
        provenance = self.provenance(edital_id)
        url_by_name = {
            _fold(Path(unquote(urlsplit(url).path)).name): url
            for url in provenance.get("urls_documentos", [])
        }
        documents: CanonicalDoc = []
        for pdf, metadata in _versioned_documents(candidates):
            pages = _extract_pages(pdf)
            if not any(p.strip() for p in pages):
                continue
            metadata = dict(metadata)
            source_url = url_by_name.get(_fold(pdf.name))
            if source_url:
                metadata["source_url"] = source_url
            documents.append({
                "doc_name": pdf.name, "units": pages, "metadata": metadata,
            })
        return documents

    def provenance(self, edital_id: str) -> dict:
        """URL oficial (`link`) + PDFs (`pdf_urls`) + data de coleta do bronze
        FINEP, casando por `chamada_id`. Determinístico (D14) — a URL não passa
        pela extração-LLM."""
        for ch in _load_latest_bronze():
            if str(ch.get("chamada_id")) != str(edital_id):
                continue
            prov: dict = {"fonte": "finep"}
            if ch.get("link"):
                prov["url"] = ch["link"]
            pdfs = [u for u in (ch.get("pdf_urls") or []) if u]
            if pdfs:
                prov["urls_documentos"] = pdfs
            if coletado_em(ch):
                prov["coletado_em"] = coletado_em(ch)
            return prov
        return {}
