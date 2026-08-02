"""core/kg/phase1/resolve.py — resolução de entidades na geração corrente.

Ordem rígida (nunca adivinhar):

1. id EXATO (nós e nós de qualidade);
2. `native_id` exato e ÚNICO;
3. nome normalizado exato e ÚNICO;
4. nó de qualidade por id ou valor exato e ÚNICO.

Ambiguidade → status categórico `ambiguous` + lista CURTA de candidatos seguros
(apenas ids). Sem match → `not_found`. NUNCA escolhe silenciosamente um sufixo
(`endswith`) — comportamento inseguro da spike `kg-structure-aware/_resolve`
fica fora desta integração.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

MAX_CANDIDATES = 5


@dataclass(frozen=True)
class Resolution:
    """Resultado categórico da resolução de uma referência no snapshot."""
    status: Literal["hit", "not_found", "ambiguous"]
    node_id: str | None = None
    candidates: list[str] = field(default_factory=list)


def _norm(value: Any) -> str:
    """Normalização determinística p/ comparação: NFKD sem acentos + lowercase."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(value or "")).lower()
        if not unicodedata.combining(c)
    ).strip()


def _ambiguous(node_ids: list[str]) -> Resolution:
    return Resolution(
        "ambiguous",
        candidates=sorted(set(node_ids))[:MAX_CANDIDATES],
    )


def resolve_entity(ref: str, snapshot: Any) -> Resolution:
    """Resolve uma referência no snapshot (ordem acima; nunca sufixo solto)."""
    ref = (ref or "").strip()
    if not ref:
        return Resolution("not_found")

    nodes = snapshot.nodes
    quality = snapshot.quality_nodes

    # 1. id exato — substância OU nó de qualidade.
    for n in nodes:
        if n["id"] == ref:
            return Resolution("hit", n["id"])
    for q in quality:
        if q["id"] == ref:
            return Resolution("hit", q["id"])

    # 2. native_id exato e ÚNICO.
    by_native = [n for n in nodes if n["native_id"] == ref]
    if len(by_native) == 1:
        return Resolution("hit", by_native[0]["id"])
    if len(by_native) > 1:
        return _ambiguous([n["id"] for n in by_native])

    norm = _norm(ref)
    if not norm:
        return Resolution("not_found")

    # 3. nome normalizado exato e ÚNICO.
    by_name = [n for n in nodes if _norm(n["name"]) == norm]
    if len(by_name) == 1:
        return Resolution("hit", by_name[0]["id"])
    if len(by_name) > 1:
        return _ambiguous([n["id"] for n in by_name])

    # 4. nó de qualidade por valor exato e ÚNICO (id já coberto no passo 1).
    by_value = [q for q in quality if _norm(q["value"]) == norm]
    if len(by_value) == 1:
        return Resolution("hit", by_value[0]["id"])
    if len(by_value) > 1:
        return _ambiguous([q["id"] for q in by_value])

    return Resolution("not_found")
