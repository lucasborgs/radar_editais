"""core/kg/spike/match_boost.py — boost estrutural do match v3 (célula A/B).

Lê as arestas `similar_a` do schema `kg_spike` (memo por processo com sonda
barata — mesmo padrão do `_get_snapshot` de match_v3) e expõe o fator de boost
por vizinhança: editais vizinhos de editais que a empresa já casou (affinity
acima do piso do Stage 2) recebem um multiplicador `1 + alpha * weight`.

Sem LLM, determinístico, fail-open: DB indisponível → fator identidade
(retorna {} e o match roda exatamente como hoje).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import psycopg

logger = logging.getLogger(__name__)

SCHEMA = "kg_spike"
STRUCTURAL_ALPHA = float(os.getenv("MATCH_STRUCTURAL_ALPHA", "0.05"))


@dataclass
class _SimilarGraph:
    probe: tuple
    neighbors: dict[str, list[tuple[str, float]]] = field(default_factory=dict)


_SIMILAR: _SimilarGraph | None = None


def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL não configurada — o boost estrutural precisa do Postgres.")
    return dsn


def _probe(cur) -> tuple:
    cur.execute(
        f"select count(*), coalesce(max(created_at)::text, '') "
        f"from {SCHEMA}.edges where type='similar_a'"
    )
    return cur.fetchone()


def _load_similar(cur) -> _SimilarGraph:
    cur.execute(
        f"select source_id, target_id, weight from {SCHEMA}.edges "
        "where type='similar_a'"
    )
    neighbors: dict[str, list[tuple[str, float]]] = {}
    for s, t, w in cur.fetchall():
        neighbors.setdefault(s, []).append((t, float(w)))
        neighbors.setdefault(t, []).append((s, float(w)))
    return _SimilarGraph(probe=_probe(cur), neighbors=neighbors)


def _get_similar() -> _SimilarGraph | None:
    """Sonda barata a cada chamada; recarrega só quando o ingest mudou."""
    global _SIMILAR
    try:
        with psycopg.connect(get_dsn(), autocommit=True) as conn:
            with conn.cursor() as cur:
                probe = _probe(cur)
                if _SIMILAR is not None and _SIMILAR.probe == probe:
                    return _SIMILAR
                _SIMILAR = _load_similar(cur)
    except Exception as e:  # noqa: BLE001 — fail-open: sem boost, nunca derruba o match
        logger.warning("match_boost: similar_a indisponível (%s) — boost desativado", e)
        return None
    return _SIMILAR


def structural_factors(
    seeds: set[str], *, alpha: float | None = None,
) -> dict[str, float]:
    """Multiplicadores `1 + alpha*weight` para vizinhos `similar_a` dos seeds.

    Seeds são excluídos do boost (já são matches fortes — o objetivo é LIFTAR
    candidatos que o texto sozinho perdeu, não inflar os que já passaram).

    Fail-open: DB indisponível ou seeds vazios → {} (nenhum boost).
    """
    g = _get_similar()
    if g is None:
        return {}
    a = STRUCTURAL_ALPHA if alpha is None else alpha
    factors: dict[str, float] = {}
    for seed in seeds:
        for other, weight in g.neighbors.get(seed, []):
            if other in seeds:
                continue
            factors[other] = max(factors.get(other, 1.0), 1.0 + a * weight)
    return factors
