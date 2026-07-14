"""
Filtro determinístico PME — decide se uma chamada serve PME/startup
(accept), é puramente acadêmica (reject) ou ambígua (unclear).

Utilitário puro preservado para classificação explícita em tooling e testes;
não é um gate implícito do ingest gold. Regras autoritativas em
`wikis/_pme_filter.md`; leitor em `core.kg.schema.pme_filter_rules()`.

Função pura: recebe `metadata` (dict agnóstico à fonte), retorna decisão.
Sem I/O, sem LLM, sem efeito colateral.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from core.kg import schema

Decision = Literal["accept", "reject", "unclear"]

_SEARCH_FIELDS = ("titulo", "modalidade", "programa", "categoria", "descricao_resumo")


def _normalize(s: str) -> str:
    """Lowercase + strip de acentos (NFD)."""
    nfd = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower().strip()


def _search_text(metadata: dict) -> str:
    """Concatena os campos de busca do metadata em uma string normalizada.
    Campos ausentes/vazios são ignorados silenciosamente."""
    parts = [str(metadata.get(k) or "") for k in _SEARCH_FIELDS]
    return _normalize(" || ".join(p for p in parts if p))


def _programa_hits(search_text: str, aliases: dict) -> list[str]:
    """Aliases de programa-whitelist que casam como palavra no search_text.
    Sem early-break: 'PIPE Centelha' casa os dois."""
    hits: list[str] = []
    for alias in aliases:
        alias_norm = _normalize(alias)
        if not alias_norm:
            continue
        if re.search(rf"\b{re.escape(alias_norm)}\b", search_text):
            hits.append(alias)
    return hits


def _publico_hits(publico_alvo: list[str] | None, whitelist: list[str]) -> list[str]:
    """Interseção entre `publico_alvo` (já canonicalizado §5.5 WIKI.md) e a
    whitelist de públicos PME. Comparação case-insensitive."""
    if not publico_alvo:
        return []
    wl_lc = {p.lower() for p in whitelist}
    return [p for p in publico_alvo if isinstance(p, str) and p.lower() in wl_lc]


def _exclusor_hits(search_text: str, exclusores: list[str]) -> list[str]:
    """Termos de exclusor que aparecem como substring no search_text."""
    return [term for term in exclusores if _normalize(term) in search_text]


def is_target_relevant(metadata: dict) -> Decision:
    """Decide se uma chamada serve PME/startup.

    Regras (ver `wikis/_pme_filter.md` para racional):
      1. Hit em programa-whitelist  → accept
      2. Hit em público-whitelist   → accept
      3. Hit em exclusor acadêmico  → reject  (se nenhum accept disparou)
      4. Nenhum sinal               → unclear (tratado como reject por enquanto)

    Precedência: accept vence reject. Uma chamada PIPE que menciona "bolsa"
    no texto é accept porque o sinal de programa dispara, mesmo com o
    exclusor presente.
    """
    decision, _ = relevance_with_reason(metadata)
    return decision


def relevance_with_reason(metadata: dict) -> tuple[Decision, str]:
    """Versão com motivo da decisão (para logging / dashboard de rejeitados).

    Retorna `(decision, reason)` onde reason é uma string curta listando
    o sinal que disparou. Útil pro épico F (dashboard de rejeitados).
    """
    rules = schema.pme_filter_rules()
    programas = rules.get("programas_pme_canonicos") or {}
    publicos = rules.get("publicos_pme_canonicos") or []
    exclusores = rules.get("exclusores_academicos") or []

    text = _search_text(metadata)
    publico_alvo = metadata.get("publico_alvo")

    prog_hits = _programa_hits(text, programas)
    if prog_hits:
        return "accept", f"programa:{','.join(prog_hits)}"

    pub_hits = _publico_hits(publico_alvo, publicos)
    if pub_hits:
        return "accept", f"publico:{','.join(pub_hits)}"

    excl_hits = _exclusor_hits(text, exclusores)
    if excl_hits:
        return "reject", f"exclusor:{excl_hits[0]}"

    return "unclear", "sem-sinal"
