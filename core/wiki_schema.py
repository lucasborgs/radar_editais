"""
Loader do schema autoritativo definido em WIKI.md e wikis/<source>.md.

Extrai todos os blocos ```yaml``` dos docs, faz merge em um dict, e expõe helpers
para os consumidores (etl_process, build_knowledge_graph, export_to_obsidian).

Contrato:
- Mudou WIKI.md → comportamento muda sem tocar em .py.
- Mudou WIKI.md e algo deixou de bater com o código → tests/test_wiki_schema_consistency.py quebra.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WIKI_MD = _ROOT / "WIKI.md"
_WIKIS_DIR = _ROOT / "wikis"

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


# =============================================================================
# SCHEMA DA WIKI PAGE
# =============================================================================

def wiki_page_fields(source: str | None = None) -> dict:
    """Retorna os 3 grupos de campos da wiki page."""
    s = load(source)
    return {
        "inherited":   s.get("wiki_page_inherited_fields", []),
        "synthesized": s.get("wiki_page_synthesized_fields", {}),
        "meta":        s.get("wiki_page_meta_fields", {}),
    }


# =============================================================================
# INGESTÃO
# =============================================================================

def skip_keywords(source: str) -> list[str]:
    return load(source).get("skip_keywords", [])


def extraction_prompt(source: str | None = None) -> str:
    return load(source)["extraction_prompt"]


def llm_params(source: str | None = None) -> dict:
    return load(source).get("llm_params", {})


def metadata_to_llm_keys(source: str) -> list[str]:
    return load(source).get("metadata_to_llm", [])


# =============================================================================
# VOCABULÁRIOS
# =============================================================================

def mechanism_vocab() -> dict:
    return load().get("mechanism", {})


def mechanism_label(key: str | None) -> str:
    if not key:
        return ""
    return mechanism_vocab().get(key, key)


def status_info(status: str) -> dict:
    """{emoji, tag, order} para um status. Fallback para 'Desconhecido'."""
    vocab = load().get("status", {})
    return vocab.get(status) or vocab.get("Desconhecido", {"emoji": "⚪", "tag": "desconhecido", "order": 9})


def fontes_canonicas() -> dict:
    return load().get("fontes_canonicas", {})


def publicos_canonicos() -> dict:
    return load().get("publicos_canonicos", {})


def subprogramas_canonicos() -> dict:
    return load().get("subprogramas_canonicos", {})


def modalidades_drop_list() -> list[str]:
    return load().get("modalidades_drop_list", [])


def trl_faixas() -> dict:
    return load().get("trl_faixas", {})


def trl_range_to_faixas(trl_min: int | None, trl_max: int | None) -> list[str]:
    """Mapeia um `trl_range` (§4.2) para as faixas semânticas (§5.8) que sobrepõem."""
    if trl_min is None or trl_max is None:
        return []
    out: list[str] = []
    for key, faixa in trl_faixas().items():
        if max(trl_min, faixa["min"]) <= min(trl_max, faixa["max"]):
            out.append(key)
    return out


# =============================================================================
# VIGÊNCIA
# =============================================================================

def parse_deadline(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_pub_year(pub_date: str | None) -> int | str:
    """Deriva o ano de `pub_date` ("dd/mm/yyyy"). Fallback: rótulo `unknown_label` do nó `ano` (§6.1)."""
    d = parse_deadline(pub_date)
    if d:
        return d.year
    return node_types().get("ano", {}).get("unknown_label", "desconhecido")


def is_vigente(deadline_str: str | None) -> bool:
    """Vigente == prazo parseável E prazo > hoje (conforme §7.1 WIKI.md)."""
    d = parse_deadline(deadline_str)
    return d is not None and d > date.today()


def normalize_status(raw_status: str, deadline_str: str | None) -> str:
    """Regra de §7.2 WIKI.md: prazo futuro → ABERTA; senão ENCERRADA preservado; senão raw."""
    d = parse_deadline(deadline_str)
    if d and d > date.today():
        return "ABERTA"
    if raw_status == "ENCERRADA":
        return "ENCERRADA"
    return raw_status


def status_order(status: str) -> int:
    return status_info(status).get("order", 9)


# =============================================================================
# GRAFO
# =============================================================================

def node_types() -> dict:
    return load().get("node_types", {})


def graph_overrides(source: str) -> dict:
    return load(source).get("graph_overrides", {})


def slugify(text: str) -> str:
    """Conforme §6.3 WIKI.md."""
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


# =============================================================================
# FONTES
# =============================================================================

def source_info(source: str) -> dict:
    """Bloco `source` do doc fonte-específico: {key, display_name, graph_tag}."""
    return load(source).get("source", {})


def bronze_mapping(source: str) -> dict:
    return load(source).get("bronze_mapping", {})


# =============================================================================
# RELOAD (testes)
# =============================================================================

def clear_cache() -> None:
    """Limpa o cache do loader (útil em testes após editar os docs)."""
    load.cache_clear()
