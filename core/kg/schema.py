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


# ---------------------------------------------------------------------------
# Schema do hipergrado v2 (WIKI.md §6.4) — enums validados no build
# ---------------------------------------------------------------------------

def hypergraph_schema() -> dict:
    """Bloco `hypergraph_schema` do WIKI.md (node_types + kinds/dims/apertures/edges)."""
    return load().get("hypergraph_schema", {})


def valid_v2_kinds() -> set[str]:
    """União de todos os kinds válidos (Oportunidade + Ator) do schema v2."""
    s = hypergraph_schema()
    return set(s.get("oportunidade_kinds", [])) | set(s.get("ator_kinds", []))


def macro_temas_vocab() -> list[str]:
    """Vocabulário controlado de macro-temas (D8, KG v2 PR3) — tier estável do
    two-tier: propriedade `macro_temas[]` da Oportunidade. Versionado no bloco
    `hypergraph_schema` do WIKI.md (§6.4); mudar o vocabulário = editar o doc."""
    return hypergraph_schema().get("macro_temas", [])


def constraint_tipos() -> list[str]:
    """Tipos válidos de constraint de elegibilidade dura (D6/PR5), do WIKI §6.4."""
    return hypergraph_schema().get("constraint_tipos", [])


def constraint_ops() -> list[str]:
    """Operadores válidos de constraint (in|not_in|lte|gte|exige), do WIKI §6.4."""
    return hypergraph_schema().get("constraint_ops", [])


def validate_v2_node(node: dict) -> str | None:
    """Valida os enums de um nó v2 contra o WIKI (§6.4). Retorna a violação como
    string, ou None se ok. Usado como sanity no build (não levanta — loga/coleta)."""
    s = hypergraph_schema()
    if not s:  # WIKI sem o bloco → sem validação (não falha)
        return None
    t = node.get("type")
    if t not in s.get("node_types", []):
        return f"type inválido: {t!r}"
    if t == "Conceito":
        if node.get("dim") not in s.get("conceito_dims", []):
            return f"Conceito com dim inválida: {node.get('dim')!r}"
    elif t == "Oportunidade":
        if node.get("kind") not in s.get("oportunidade_kinds", []):
            return f"Oportunidade com kind inválido: {node.get('kind')!r}"
        ap = node.get("aperture")
        if ap is not None and ap not in s.get("apertures", []):
            return f"aperture inválida: {ap!r}"
        vocab = s.get("macro_temas")
        if vocab:
            fora = [m for m in node.get("macro_temas", []) if m not in vocab]
            if fora:
                return f"macro_temas fora do vocabulário: {fora!r}"
        tipos, ops = s.get("constraint_tipos", []), s.get("constraint_ops", [])
        if tipos:
            for c in node.get("constraints", []) or []:
                if c.get("tipo") not in tipos:
                    return f"constraint com tipo inválido: {c.get('tipo')!r}"
                if c.get("op") not in ops:
                    return f"constraint com op inválido: {c.get('op')!r}"
    elif t == "Ator":
        if node.get("kind") not in s.get("ator_kinds", []):
            return f"Ator com kind inválido: {node.get('kind')!r}"
    return None


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
