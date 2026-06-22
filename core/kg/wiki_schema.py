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

# Raiz do repo via config (canônico) — não derivar de __file__: o módulo já
# mudou de profundidade uma vez (core/ → core/kg/) e o path relativo quebraria
# silenciosamente de novo a cada move.
from config import ROOT as _ROOT

_WIKI_MD = _ROOT / "WIKI.md"
_WIKIS_DIR = _ROOT / "wikis"
_PME_FILTER_MD = _WIKIS_DIR / "_pme_filter.md"
_DISCOVERY_MD = _WIKIS_DIR / "_discovery.md"

_YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.DOTALL)


# =============================================================================
# Campos de match: o que o índice (card durável) precisa carregar
# =============================================================================
# Os campos estruturados que o HybridMatch Stage 1 + Stage 2 usam nascem na
# SÍNTESE (wiki page), não no scrape (index entry). Promovê-los ao índice o torna
# o card durável que o request-path lê em prod (onde a wiki page por-arquivo não
# existe). Single source p/ etl_process (promove ao sintetizar) E
# build_knowledge_graph (carrega adiante ao rebuildar, p/ o índice nunca degradar
# se a síntese ainda não rodou / falhou). Stage 1: mechanism/trl_range/
# counterpart_required/eligible_entities(+value_range display). Stage 2: objective/
# key_requirements. NÃO incluir estruturais extras nem texto cheio (ver
# core/hybrid_match_service: separação Stage1 determinístico × Stage2 temático).
MATCH_FIELDS: tuple[str, ...] = (
    "mechanism", "trl_range", "counterpart_required",
    "eligible_entities", "value_range",
    "objective", "key_requirements",
    # Elegibilidade dura tipada (região/idade/faturamento) — carregada ao índice
    # para a dimensão soft `_score_elegibilidade_dura` (e durabilidade em prod,
    # onde o consumo lê o índice quando a wiki page-arquivo não existe).
    "eligibility_constraints",
)


def promote_match_fields(entry: dict, wiki_page: dict) -> None:
    """Copia os MATCH_FIELDS da wiki page para o index entry (in place)."""
    for k in MATCH_FIELDS:
        if k in wiki_page:
            entry[k] = wiki_page[k]


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
# DOCUMENTO ESTRUTURADO (silver — WIKI.md §11)
# =============================================================================

def structured_doc_schema() -> dict:
    """Schema do bloco silver: block_fields, kinds, meta_sidecar (§11.1)."""
    return load().get("structured_doc_schema", {})


def structurer_params() -> dict:
    """Parâmetros do structurer: versões, modelo, per_page (§11.2)."""
    return load().get("structurer_params", {})


def structurer_prompt() -> str:
    """Prompt por página do structurer (§11.3). Fonte-agnóstico."""
    return load()["structurer_prompt"]


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

def iso_to_br_date(value: str | None) -> str:
    """Normaliza data para o contrato do schema: `dd/mm/yyyy` (WIKI.md §4.1).

    A API Liferay da FINEP entrega `yyyy-mm-dd` (ISO, possivelmente com hora).
    Converte para `dd/mm/yyyy`. Já-`dd/mm/yyyy` passa direto. Vazio/None/
    inválido → `""`. Boundary de ingestão: tudo a jusante assume dd/mm/yyyy.
    """
    if not value:
        return ""
    s = str(value).strip()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        pass
    try:
        datetime.strptime(s, "%d/%m/%Y")  # valida e mantém
        return s
    except ValueError:
        return ""


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


def ict_schema() -> dict:
    """Schema do nó `ict` e do artefato icts.json (WIKI.md §6.1.2)."""
    return load().get("ict_schema", {})


def investidor_schema() -> dict:
    """Schema do nó `investidor` e do artefato investidores.json (WIKI.md §6.1.3)."""
    return load().get("investidor_schema", {})


def programa_schema() -> dict:
    """Schema do nó `programa` e do artefato programas.json (WIKI.md §6.1.4)."""
    return load().get("programa_schema", {})


def tema_vocab() -> list[str]:
    """Vocabulário canônico de temas-macro (WIKI.md §5.9) — alvo único de
    editais e ICTs. Lista vazia se não declarado (consumidores degradam)."""
    return load().get("tema_vocab", [])


def ict_requirement_patterns() -> list[str]:
    """Regex (strings) que detectam exigência de parceria com ICT no texto do
    edital (WIKI.md §5.10). Heurística do campo `requires_ict_partner`."""
    return load().get("ict_requirement_patterns", [])


def verificacao_values() -> list[str]:
    """Valores válidos do campo `verificacao` do edital (WIKI.md §5.11).
    Default do produtor: 'verificado' (fontes do SCRAPER_REGISTRY)."""
    return load().get("verificacao_values", ["verificado", "provisorio"])


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
# FILTRO PME (wikis/_pme_filter.md)
# =============================================================================

@lru_cache(maxsize=1)
def pme_filter_rules() -> dict:
    """Regras determinísticas do filtro PME — programas-whitelist, públicos-
    whitelist, exclusores acadêmicos. Doc autoritativo: `wikis/_pme_filter.md`.

    Retorna o bloco `target_relevance_rules` com chaves:
      - programas_pme_canonicos: dict[alias, label]
      - publicos_pme_canonicos:  list[str]   (subset de §5.5)
      - exclusores_academicos:   list[str]
    """
    parsed = _parse_md(_PME_FILTER_MD)
    return parsed.get("target_relevance_rules", {})


# =============================================================================
# DESCOBERTA DE OPORTUNIDADES (wikis/_discovery.md, item 2.2)
# =============================================================================

@lru_cache(maxsize=1)
def discovery_config() -> dict:
    """Config do agente de descoberta — queries de busca + caps. Doc
    autoritativo: `wikis/_discovery.md`, bloco `discovery`."""
    return _parse_md(_DISCOVERY_MD).get("discovery", {})


# =============================================================================
# RELOAD (testes)
# =============================================================================

def clear_cache() -> None:
    """Limpa o cache do loader (útil em testes após editar os docs)."""
    load.cache_clear()
    pme_filter_rules.cache_clear()
