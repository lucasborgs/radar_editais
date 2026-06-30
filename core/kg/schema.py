"""Loader do schema autoritativo definido em WIKI.md e wikis/<source>.md.

Extrai blocos ```yaml``` dos docs e expõe helpers.

Substitui `wiki_schema.py` (removido) — mesma lógica de parse, sem as funções
que só serviam ao ETL legacy (build_knowledge_graph, etl_process).

Contrato:
- Mudou WIKI.md → comportamento muda sem tocar em .py.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

from config import ROOT as _ROOT

_WIKI_MD = _ROOT / "WIKI.md"
_WIKIS_DIR = _ROOT / "wikis"
_PME_FILTER_MD = _WIKIS_DIR / "_pme_filter.md"
_DISCOVERY_MD = _WIKIS_DIR / "_discovery.md"

_YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


def _parse_md(md_path: Path) -> dict:
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding="utf-8")
    merged: dict = {}
    for match in _YAML_BLOCK_RE.finditer(text):
        block = yaml.safe_load(match.group(1))
        if isinstance(block, dict):
            merged.update(block)
    return merged


@lru_cache(maxsize=16)
def load(source: str | None = None) -> dict:
    """Schema global (WIKI.md) + overrides da fonte (wikis/<source>.md)."""
    schema = _parse_md(_WIKI_MD)
    if source:
        schema.update(_parse_md(_WIKIS_DIR / f"{source}.md"))
    return schema


# ---------------------------------------------------------------------------
# Utilitários de data
# ---------------------------------------------------------------------------

def iso_to_br_date(value: str | None) -> str:
    """ISO yyyy-mm-dd → dd/mm/yyyy (WIKI.md §4.1). Já-BR passa direto."""
    if not value:
        return ""
    s = str(value).strip()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        pass
    try:
        datetime.strptime(s, "%d/%m/%Y")
        return s
    except ValueError:
        return ""


def parse_deadline(value: str | None) -> date | None:
    """dd/mm/yyyy → date ou None."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_pub_year(pub_date: str | None) -> int | str:
    """Ano de \"dd/mm/yyyy\". Fallback: `unknown_label` em WIKI.md."""
    d = parse_deadline(pub_date)
    if d:
        return d.year
    return node_types().get("ano", {}).get("unknown_label", "desconhecido")


# ---------------------------------------------------------------------------
# Ingestão
# ---------------------------------------------------------------------------

def skip_keywords(source: str) -> list[str]:
    return load(source).get("skip_keywords", [])


# ---------------------------------------------------------------------------
# Documento estruturado (silver — WIKI.md §11)
# ---------------------------------------------------------------------------

def structured_doc_schema() -> dict:
    return load().get("structured_doc_schema", {})


def structurer_params() -> dict:
    return load().get("structurer_params", {})


def structurer_prompt() -> str:
    return load()["structurer_prompt"]


# ---------------------------------------------------------------------------
# Vocabulários
# ---------------------------------------------------------------------------

def mechanism_label(key: str | None) -> str:
    if not key:
        return ""
    return load().get("mechanism", {}).get(key, key)


def status_info(status: str) -> dict:
    vocab = load().get("status", {})
    return vocab.get(status) or vocab.get("Desconhecido", {"emoji": "⚪", "tag": "desconhecido", "order": 9})


def tema_vocab() -> list[str]:
    return load().get("tema_vocab", [])


def node_types() -> dict:
    return load().get("node_types", {})


def trl_faixas() -> dict:
    return load().get("trl_faixas", {})


def trl_range_to_faixas(trl_min: int | None, trl_max: int | None) -> list[str]:
    if trl_min is None or trl_max is None:
        return []
    out: list[str] = []
    for key, faixa in trl_faixas().items():
        if max(trl_min, faixa["min"]) <= min(trl_max, faixa["max"]):
            out.append(key)
    return out


def wiki_page_fields(source: str | None = None) -> dict:
    s = load(source)
    return {
        "inherited":   s.get("wiki_page_inherited_fields", []),
        "synthesized": s.get("wiki_page_synthesized_fields", {}),
        "meta":        s.get("wiki_page_meta_fields", {}),
    }


# ---------------------------------------------------------------------------
# Catálogo de fontes (adapters registry)
# ---------------------------------------------------------------------------

def source_adapters() -> dict:
    return load().get("source_adapters", {})


def setor_vocab() -> list[str]:
    return load().get("setor_vocab", [])


def estagio_vocab() -> list[str]:
    return load().get("estagio_vocab", [])


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    rules = load().get("slugify_rules", {})
    max_len = rules.get("max_len", 80)
    fallback = rules.get("fallback", "sem-nome")
    s = unicodedata.normalize("NFD", text)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")[:max_len] or fallback


# ---------------------------------------------------------------------------
# Filtro PME (wikis/_pme_filter.md)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def pme_filter_rules() -> dict:
    parsed = _parse_md(_PME_FILTER_MD)
    return parsed.get("target_relevance_rules", {})


# ---------------------------------------------------------------------------
# Descoberta (wikis/_discovery.md)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def discovery_config() -> dict:
    return _parse_md(_DISCOVERY_MD).get("discovery", {})


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    load.cache_clear()
    pme_filter_rules.cache_clear()
