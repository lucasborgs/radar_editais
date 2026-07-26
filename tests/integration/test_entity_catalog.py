"""Testes de `core/kg/entity_catalog.py` — catálogo + explore só-SQL (v3 Fase 3
PR-B, docs/specs/v3-unified.md §8/§10).

Duas camadas:

1. HERMÉTICA (sem DB/rede): funções PURAS — mapeamento de card (shape + campos
   antes-vazios agora POPULADOS), BFS estrutural (`_bfs_edges`: profundidade e
   ciclos) e ranking semântico (`_rank_by_similarity`).

2. GATED em env (Supabase LOCAL com `ingest_all()` já rodado, mesmo protocolo de
   tests/integration/test_company_chunks_rls.py): exercita o backend SQL contra `entities`.

   Como rodar a camada 2:
       supabase start && supabase migration up
       DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
       OPENAI_API_KEY=... python -m radar.core.kg.gold
       SUPABASE_URL=http://127.0.0.1:54321 \\
       SUPABASE_SERVICE_KEY=<service_role de `supabase status -o env`> \\
       DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
       ENTITY_CATALOG_TEST_GOLD_READY=1 \\
       pytest tests/integration/test_entity_catalog.py -v
"""
from __future__ import annotations

import os

import pytest

from radar.core.kg import entity_catalog

pytestmark = pytest.mark.integration

_REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "DATABASE_URL")

# Contrato de shape do card de edital cheio — as chaves que writing_session,
# planning_node, checklist e o frontend leem. Fonte da verdade agora que
# `hypergraph_catalog.edital_card` foi deletado.
CARD_KEYS = {
    "id", "source", "title", "status", "deadline", "themes", "technologies",
    "programs", "publico_alvo", "fonte_recurso", "opportunity_type", "official_url",
    "aperture", "macro_temas", "kind", "objective", "mecanismo", "mechanism",
    "eligible_entities", "key_requirements", "exclusoes", "value",
    "icts", "investidores", "constraints", "document_urls", "collected_at",
    "provenance",
}


def _target_is_local() -> bool:
    if os.getenv("ENTITY_CATALOG_TEST_ALLOW_REMOTE", "").strip().lower() in ("1", "true", "yes"):
        return True
    dsn = os.getenv("DATABASE_URL", "")
    return "127.0.0.1" in dsn or "localhost" in dsn or "@db:" in dsn


def _skip_reason() -> str | None:
    missing = [v for v in _REQUIRED_ENV if not os.getenv(v)]
    if missing:
        return f"entity_catalog SQL — faltam envs: {', '.join(missing)} (gated)"
    if not _target_is_local():
        return (
            "entity_catalog SQL — DATABASE_URL não é local (o .env aponta pro remoto "
            "sem a migration 036). Rode contra Supabase local, ou force com "
            "ENTITY_CATALOG_TEST_ALLOW_REMOTE=1."
        )
    if os.getenv("ENTITY_CATALOG_TEST_GOLD_READY", "").strip().lower() not in (
        "1", "true", "yes",
    ):
        return (
            "entity_catalog SQL — exige ingest gold explícito; rode `python -m "
            "radar.core.kg.gold` e defina ENTITY_CATALOG_TEST_GOLD_READY=1"
        )
    return None


# ===========================================================================
# Camada 1 — funções puras (sem DB)
# ===========================================================================

def _edital_row(**over) -> dict:
    row = {
        "id": "uuid-1", "native_id": "finep:589", "source": "finep", "kind": "edital",
        "name": "Edital Sintético", "description": "Objetivo de teste",
        "deadline": "2099-12-31", "status": "aberta",
        "setores": ["TIC"], "tecnologias_tags": ["ia embarcada", "nlp"],
        "mecanismo": "subvencao", "formato": "edital_periodico",
        "requisitos_texto": ["Requisito A"],
        "constraints": [{"tipo": "porte", "op": "in", "valor": ["me"]}],
        "ticket_min": 100000, "ticket_max": 1000000,
        "metadata": {
            "url": "https://ex.org/e", "exclusoes": ["Vedado a grandes"],
            "publico_alvo": ["startups", "microempresas"],
        },
        "updated_at": "2026-07-10",
    }
    row.update(over)
    return row


class TestCardMapping:
    def test_card_has_exact_key_set(self):
        card = entity_catalog._row_to_card(_edital_row(), programs={}, icts={}, agencies={})
        assert set(card) == CARD_KEYS

    def test_previously_empty_fields_now_populated(self):
        """exclusoes/publico_alvo/eligible_entities — antes sempre [] no
        SQL, agora vêm de metadata (call B) e das tags (§ PR-B)."""
        card = entity_catalog._row_to_card(
            _edital_row(), programs={}, icts={"uuid-1": ["ICT Y"]}, agencies={"uuid-1": ["FINEP"]},
        )
        assert card["exclusoes"] == ["Vedado a grandes"]
        assert card["publico_alvo"] == ["startups", "microempresas"]
        assert card["eligible_entities"] == ["startups", "microempresas"]  # == publico_alvo
        assert card["icts"] == ["ICT Y"]
        assert card["fonte_recurso"] == ["FINEP"]

    def test_status_from_deadline_and_value_display(self):
        assert entity_catalog._row_to_card(_edital_row(), programs={}, icts={}, agencies={})["status"] == "ABERTA"
        past = entity_catalog._row_to_card(_edital_row(deadline="2000-01-01"), programs={}, icts={}, agencies={})
        assert past["status"] == "ENCERRADA"
        assert entity_catalog._row_to_card(_edital_row(), programs={}, icts={}, agencies={})["value"] == "R$ 100,000 – R$ 1,000,000"

    def test_metadata_non_list_publico_alvo_coerced(self):
        """metadata.publico_alvo pode vir string (bronze) — o card nunca quebra."""
        card = entity_catalog._row_to_card(
            _edital_row(metadata={"publico_alvo": "Empresas brasileiras"}),
            programs={}, icts={}, agencies={},
        )
        assert card["publico_alvo"] == ["Empresas brasileiras"]

    def test_curated_card_shape_and_fields(self):
        row = {
            "id": "uuid-p", "native_id": "programa:centelha", "source": "curadoria",
            "kind": "programa", "name": "Centelha", "description": "Programa X",
            "status": "ativa", "deadline": None, "setores": ["TIC"],
            "tecnologias_tags": ["ideacao"], "mecanismo": "subvencao", "formato": None,
            "requisitos_texto": ["R1"], "constraints": [], "ticket_min": None, "ticket_max": None,
            "metadata": {"site": "https://c.org", "publico_alvo": ["startups"],
                         "exclusoes": ["pessoa física"], "estagio_alvo": ["ideação"]},
            "updated_at": "2026-07-10",
        }
        card = entity_catalog._curated_card(row, agencies={"uuid-p": ["FINEP"]})
        assert card["kind"] == "programa"
        assert card["status"] == "ABERTA"           # ativa → ABERTA
        assert card["publico_alvo"] == ["startups"]
        assert card["exclusoes"] == ["pessoa física"]
        assert card["official_url"] == "https://c.org"
        assert card["fonte_recurso"] == ["FINEP"]


class TestBfsEdges:
    """§8.2 — BFS estrutural (profundidade + ciclos), função pura."""

    EDGES = [
        ("edital", "agencia", "operado_por"),
        ("edital", "programa", "subordinado_a"),
        ("programa", "agencia2", "operado_por"),   # 2º salto a partir do edital
        ("agencia", "edital", "dup"),              # aresta redundante (ciclo trivial)
    ]

    def test_depth1_only_direct_edges(self):
        got = entity_catalog._bfs_edges(self.EDGES, "edital", 1)
        reached = {frozenset((s, t)) for s, t, _ in got}
        assert frozenset(("edital", "agencia")) in reached
        assert frozenset(("edital", "programa")) in reached
        assert frozenset(("programa", "agencia2")) not in reached  # é 2º salto

    def test_depth2_reaches_second_hop(self):
        got = entity_catalog._bfs_edges(self.EDGES, "edital", 2)
        reached = {frozenset((s, t)) for s, t, _ in got}
        assert frozenset(("programa", "agencia2")) in reached

    def test_cycle_is_finite(self):
        cyc = [("A", "B", "e"), ("B", "C", "e"), ("C", "A", "e")]
        got = entity_catalog._bfs_edges(cyc, "A", 10)
        assert len(got) == 3  # cada aresta uma vez, sem loop infinito

    def test_undirected_reaches_source_from_target(self):
        """Seed no TARGET de uma aresta alcança o source (grafo não-direcionado)."""
        got = entity_catalog._bfs_edges(self.EDGES, "agencia", 1)
        reached = {frozenset((s, t)) for s, t, _ in got}
        assert frozenset(("edital", "agencia")) in reached


class TestRankBySimilarity:
    """§8.1 — ranking semântico de search_entities (função pura)."""

    def _snapshot(self):
        import numpy as np
        rows = [
            {"native_id": "ict:a", "kind": "ict", "name": "A", "description": "", "setores": [], "tecnologias_tags": []},
            {"native_id": "ict:b", "kind": "ict", "name": "B", "description": "", "setores": [], "tecnologias_tags": []},
            {"native_id": "edital:c", "kind": "edital", "name": "C", "description": "", "setores": [], "tecnologias_tags": []},
        ]
        mat = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.9, 0.1, 0.0]], dtype=np.float32)
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        return {"rows": rows, "mat": mat, "probe": ()}

    def test_orders_by_cosine(self):
        res = entity_catalog._rank_by_similarity([1.0, 0.0, 0.0], self._snapshot(), None, 3)
        assert [r["id"] for r in res][:2] == ["ict:a", "edital:c"]  # a=1.0, c≈0.99, b=0
        assert res[0]["score"] >= res[1]["score"] >= res[2]["score"]

    def test_kind_filter_applied_before_topk(self):
        res = entity_catalog._rank_by_similarity([1.0, 0.0, 0.0], self._snapshot(), "ict", 5)
        assert {r["kind"] for r in res} == {"ict"}
        assert all(r["id"] != "edital:c" for r in res)

    def test_empty_snapshot_returns_empty(self):
        import numpy as np
        empty = {"rows": [], "mat": np.empty((0, 0), dtype=np.float32), "probe": ()}
        assert entity_catalog._rank_by_similarity([1.0], empty, None, 5) == []


# ===========================================================================
# Camada 2 — contrato SQL (gated em Supabase local com ingest rodado)
# ===========================================================================

@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
class TestSqlBackendContract:
    def test_list_editais_nonempty_and_shape(self):
        cards = entity_catalog.list_editais(limit=200)
        assert cards, "entities (kind=edital) vazia — rodou `python -m radar.core.kg.gold`?"
        for card in cards[:3]:
            assert set(card) == CARD_KEYS
            assert isinstance(card["exclusoes"], list)
            assert isinstance(card["publico_alvo"], list)

    def test_get_edital_roundtrip(self):
        sample = entity_catalog.list_editais(limit=3)
        assert sample
        for card in sample:
            fetched = entity_catalog.get_edital(card["id"])
            assert fetched is not None
            assert fetched["id"] == card["id"]
            assert set(fetched) == CARD_KEYS

    def test_get_edital_unknown_returns_none(self):
        assert entity_catalog.get_edital("finep:__inexistente__") is None

    def test_get_stats_shape(self):
        stats = entity_catalog.get_stats()
        for key in ("total_editais", "by_status", "n_programas", "n_investidores",
                    "n_icts", "total_oportunidades"):
            assert key in stats
        assert stats["total_editais"] >= 0

    def test_search_entities_returns_scored(self):
        res = entity_catalog.search_entities("inovação tecnológica", k=5)
        assert isinstance(res, list)
        for r in res:
            assert {"id", "name", "kind", "score"} <= set(r)
