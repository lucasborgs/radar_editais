"""Loader do schema autoritativo em docs/domain/schema.md e sources/<source>.md.

Extrai blocos ```yaml``` dos docs e expõe helpers.

Substitui `wiki_schema.py` (removido) — mesma lógica de parse, sem as funções
que só serviam ao ETL legacy (build_knowledge_graph, etl_process).

Contrato:
- Mudou docs/domain/schema.md → comportamento muda sem tocar em .py.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

from radar.core.config import ROOT as _ROOT

_WIKI_MD = _ROOT / "docs" / "domain" / "schema.md"
_WIKIS_DIR = _ROOT / "docs" / "domain" / "sources"
_PME_FILTER_MD = _WIKIS_DIR / "_pme_filter.md"
_DISCOVERY_MD = _WIKIS_DIR / "_discovery.md"
_COVERAGE_MD = _WIKIS_DIR / "_coverage.md"

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
    """Schema global + overrides em docs/domain/sources/<source>.md."""
    schema = _parse_md(_WIKI_MD)
    if source:
        schema.update(_parse_md(_WIKIS_DIR / f"{source}.md"))
    return schema


# ---------------------------------------------------------------------------
# Utilitários de data
# ---------------------------------------------------------------------------

def iso_to_br_date(value: str | None) -> str:
    """ISO yyyy-mm-dd → dd/mm/yyyy (docs/domain/schema.md §9.3). Já-BR passa direto."""
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


# ---------------------------------------------------------------------------
# Ingestão
# ---------------------------------------------------------------------------

def skip_keywords(source: str) -> list[str]:
    return load(source).get("skip_keywords", [])


def document_authority_overrides(source: str) -> dict:
    return load(source).get("document_authority_overrides", {})


# ---------------------------------------------------------------------------
# Documento estruturado (silver — docs/domain/schema.md §11)
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

def tema_vocab() -> list[str]:
    return load().get("tema_vocab", [])


# ---------------------------------------------------------------------------
# Constraints de elegibilidade dura (D6/PR5, docs/domain/schema.md §6.4) — vocab validado pelo
# produtor (core/kg/constraints_producer). Único resíduo vivo do antigo bloco de
# schema do hipergrado, que morreu com a linhagem hyper-extract (v3 PR-C).
# ---------------------------------------------------------------------------

def constraint_tipos() -> list[str]:
    """Tipos válidos de constraint de elegibilidade dura (D6/PR5), do WIKI §6.4
    (bloco `constraint_enums` — subconjunto que o match valida)."""
    return load().get("constraint_enums", {}).get("constraint_tipos", [])


def constraint_ops() -> list[str]:
    """Operadores válidos de constraint (in|not_in|lte|gte|exige), do WIKI §6.4
    (bloco `constraint_enums`)."""
    return load().get("constraint_enums", {}).get("constraint_ops", [])


def constraint_ops_by_type() -> dict[str, list[str]]:
    """Operadores permitidos por tipo, do schema autoritativo."""
    raw = load().get("constraint_vocab", {}).get("ops_by_type")
    if not isinstance(raw, dict) or not raw:
        raise ValueError("authoritative constraint ops_by_type is unavailable")
    valid_ops = set(constraint_ops())
    result: dict[str, list[str]] = {}
    for tipo, ops in raw.items():
        if (
            not isinstance(tipo, str) or not tipo.strip()
            or not isinstance(ops, list) or not ops
            or any(not isinstance(op, str) or op not in valid_ops for op in ops)
        ):
            raise ValueError("authoritative constraint ops_by_type is invalid")
        result[tipo] = list(dict.fromkeys(ops))
    return result


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


# ---------------------------------------------------------------------------
# Catálogo de fontes (adapters registry)
# ---------------------------------------------------------------------------

def source_adapters() -> dict:
    return load().get("source_adapters", {})


def pnipe_schema() -> dict:
    """Contrato do registro bronze PNIPE (`pnipe_schema` em
    docs/domain/sources/pnipe.md): campos, required_fields, kinds e notas.
    Autoritativo — regras vivem no doc, não no código."""
    return load("pnipe").get("pnipe_schema", {})


def setor_vocab() -> list[str]:
    return load().get("setor_vocab", [])


def estagio_vocab() -> list[str]:
    return load().get("estagio_vocab", [])


# ---------------------------------------------------------------------------
# Vocabulários gold v3 (docs/domain/schema.md §13) — lidos pelo ingest core/kg/gold.py
# ---------------------------------------------------------------------------

def setores_taxonomia() -> dict:
    """Bloco `setores_taxonomia` (§13.1): labels (16), fallback, tese_theme_map,
    alias_map. Aplicado por `gold.normalize_setores`."""
    return load().get("setores_taxonomia", {})


def tag_normalization() -> dict:
    """Bloco `tag_normalization` (§13.2): regras + mapa de sinônimos do passe
    determinístico de tags. Aplicado por `gold.normalize_tags`."""
    return load().get("tag_normalization", {})


def match_sections() -> dict:
    """Bloco `match_sections` (§13.3): drop_kinds + padrões de eligibility/
    boilerplate. Aplicado por `gold.classify_section`."""
    return load().get("match_sections", {})


def constraint_vocab_v3() -> dict:
    """Bloco `constraint_vocab` (§13.4): tipos + ops de elegibilidade dura do
    match v3. Usado por `constraints_producer.produce_from_text` (superconjunto do
    subconjunto `constraint_enums` do §6.4 que o produtor valida)."""
    return load().get("constraint_vocab", {})


def constraint_vocab_satisfies() -> dict[str, dict[str, list[str]]]:
    """Sub-bloco `constraint_vocab.satisfies` (§13.4): hierarquia de
    comparação — valores do PERFIL que satisfazem um valor EXIGIDO pelo
    constraint (ex. forma_juridica=empresa é satisfeito por startup/ltda/...).
    Usado por `core/services/eligibility.py` na COMPARAÇÃO; não afeta o que
    `produce_from_text` emite."""
    return constraint_vocab_v3().get("satisfies", {})


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
# Filtro PME (docs/domain/sources/_pme_filter.md)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def pme_filter_rules() -> dict:
    parsed = _parse_md(_PME_FILTER_MD)
    return parsed.get("target_relevance_rules", {})


# ---------------------------------------------------------------------------
# Descoberta (docs/domain/sources/_discovery.md)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _discovery_raw() -> dict:
    """YAML bruto sem projeção. Fonte única para funções que precisam
    da estrutura original (ex. discovery_queries, _valid_family_keys)."""
    return _parse_md(_DISCOVERY_MD).get("discovery", {})


@lru_cache(maxsize=1)
def discovery_config() -> dict:
    """Config da Descoberta com queries projetadas para lista plana —
    preserva compatibilidade com consumidor atual (opportunity_discovery.py)."""
    cfg = dict(_discovery_raw())
    raw_q = cfg.get("queries", [])
    if raw_q and isinstance(raw_q[0], dict):
        cfg["queries"] = [q["text"] for q in raw_q]
    return cfg


# ---------------------------------------------------------------------------
# Cobertura — canais de aquisição (docs/domain/sources/_coverage.md, RT03-T01)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def coverage_config() -> dict:
    return _parse_md(_COVERAGE_MD).get("coverage", {})


def coverage_modes() -> list[str]:
    """Modos canônicos de canal carregados do doc autoritativo."""
    raw = coverage_config().get("modes", [])
    modes = [m["key"] for m in raw if isinstance(m, dict) and m.get("key")]
    if not modes:
        raise ValueError("no channel modes found in coverage config")
    return modes


def coverage_channels() -> list[dict]:
    """Canais de aquisição registrados, validados contra modos canônicos."""
    valid_modes = frozenset(coverage_modes())
    channels = coverage_config().get("channels", [])
    seen: set[str] = set()
    validated: list[dict] = []
    for ch in channels:
        key = ch.get("source_key", "")
        if not key:
            raise ValueError("channel missing source_key")
        if not isinstance(key, str) or not key.islower():
            raise ValueError(f"source_key must be lowercase: {key!r}")
        if key in seen:
            raise ValueError(f"duplicate source_key: {key}")
        mode = ch.get("mode", "")
        if mode not in valid_modes:
            raise ValueError(f"invalid mode {mode!r} for channel {key!r}")
        display = ch.get("display_name")
        if not display:
            raise ValueError(f"channel {key!r} missing display_name")
        scope = ch.get("scope_note")
        if not scope:
            raise ValueError(f"channel {key!r} missing scope_note")
        interval = ch.get("expected_interval_hours")
        if interval is not None and (not isinstance(interval, (int, float)) or interval <= 0):
            raise ValueError(f"channel {key!r} expected_interval_hours must be positive, got {interval!r}")
        enabled = ch.get("enabled_by_default")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError(f"channel {key!r} enabled_by_default must be boolean, got {enabled!r}")
        has_flag = "flag_name" in ch
        if has_flag and enabled is not False:
            raise ValueError(f"channel {key!r} with flag_name must have enabled_by_default=false")
        if not has_flag and enabled is False:
            raise ValueError(f"channel {key!r} without flag_name must have enabled_by_default=true (or omit for true)")
        seen.add(key)
        validated.append(ch)
    return validated


def coverage_channel(source_key: str) -> dict | None:
    """Retorna um canal por source_key ou None."""
    for ch in coverage_channels():
        if ch.get("source_key") == source_key:
            return ch
    return None


# ---------------------------------------------------------------------------
# Famílias de busca (docs/domain/sources/_discovery.md, RT03-T01)
# ---------------------------------------------------------------------------

def _valid_family_keys() -> frozenset[str]:
    families = _discovery_raw().get("query_families", [])
    seen: set[str] = set()
    for fam in families:
        key = fam.get("key", "")
        if not key:
            raise ValueError("query_family entry missing key")
        if not isinstance(key, str) or not key.islower():
            raise ValueError(f"query_family key must be lowercase: {key!r}")
        if key in seen:
            raise ValueError(f"duplicate query_family key: {key}")
        if not fam.get("description"):
            raise ValueError(f"query_family {key!r} missing description")
        seen.add(key)
    return frozenset(seen)


def query_families() -> list[dict]:
    """Famílias de busca registradas."""
    _valid_family_keys()
    return list(discovery_config().get("query_families", []))


def discovery_queries_raw() -> list:
    """Queries do YAML sem projeção."""
    return _discovery_raw().get("queries", [])


def discovery_queries() -> list[dict]:
    """Queries estruturadas com ``text`` e ``family``, validadas contra famílias registradas.

    Cada entrada: ``{'text': str, 'family': str}``.
    Erro se ``family`` não estiver em ``query_families()``.
    """
    valid = _valid_family_keys()
    raw = discovery_queries_raw()
    if not raw:
        return []
    out: list[dict] = []
    for i, q in enumerate(raw):
        if not isinstance(q, dict):
            raise TypeError(f"query[{i}] must be a dict with 'text' and 'family'")
        text = q.get("text")
        family = q.get("family")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"query[{i}] missing or empty 'text'")
        if not isinstance(family, str) or not family.strip():
            raise ValueError(f"query[{i}] missing or empty 'family'")
        if family not in valid:
            raise ValueError(
                f"query[{i}] family {family!r} not in registered families: {sorted(valid)}"
            )
        out.append({"text": text, "family": family})
    return out


def discovery_queries_flat() -> list[str]:
    """Projeção compatível: lista plana de strings de query.

    Preserva o contrato do consumidor atual (``opportunity_discovery.py``).
    """
    structured = discovery_queries()
    return [q["text"] for q in structured]


# ---------------------------------------------------------------------------
# Canal Deep Research (docs/domain/sources/_discovery.md,
# spec discovery-deep-research.md)
# ---------------------------------------------------------------------------

def deep_research_config() -> dict:
    """Config do canal Deep Research (defaults quando ausente).

    Lê do doc autoritativo (não-cacheado): ``_discovery_raw`` relê o YAML.
    ``provider`` vazio significa resolver no default do engine.
    """
    raw = (_discovery_raw().get("deep_research") or {}) or {}
    if not isinstance(raw, dict):
        raise TypeError("discovery.deep_research must be a mapping")
    try:
        return {
            "enabled": bool(raw.get("enabled", False)),
            "provider": str(raw.get("provider") or ""),
            "max_findings": int(raw.get("max_findings") or 10),
        }
    except (TypeError, ValueError) as exc:  # noqa: PERF203
        raise ValueError("discovery.deep_research config inválida") from exc


def deep_research_targets() -> list[dict]:
    """Alvos de pesquisa do canal Deep Research.

    Cada alvo: ``{'key': str, 'brief': str, 'type_hint': str}``. Sem config ou
    sem alvos → []. Erro se a estrutura do YAML estiver quebrada (documento é
    autoritativo; quebrar aqui é melhor que pesquisar lixo).
    """
    raw = (_discovery_raw().get("deep_research") or {}) or {}
    targets = raw.get("targets") or []
    if not isinstance(targets, list):
        raise TypeError("discovery.deep_research.targets must be a list")
    out: list[dict] = []
    for i, t in enumerate(targets):
        if not isinstance(t, dict):
            raise TypeError(f"deep_research target[{i}] must be a mapping")
        key = t.get("key", "")
        brief = t.get("brief", "")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"deep_research target[{i}] missing or empty 'key'")
        if not isinstance(brief, str) or not brief.strip():
            raise ValueError(f"deep_research target[{i}] missing or empty 'brief'")
        out.append({
            "key": key,
            "brief": brief,
            "type_hint": str(t.get("type_hint") or "edital").strip().lower(),
        })
    return out


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

def clear_cache() -> None:
    load.cache_clear()
    pme_filter_rules.cache_clear()
    coverage_config.cache_clear()
