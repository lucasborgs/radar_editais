"""Testes de contrato do bridge SQL `core/kg/entity_catalog.py` (v3 Fase 3 PR-A,
docs/specs/v3-unified.md §8/§10).

Duas camadas:

1. `TestDefaultBackendDelegation` — roda sempre (sem DB/rede): confirma que com
   `CATALOG_BACKEND` no default (`hypergraph`, não setado) o dispatcher chama
   `hypergraph_catalog` diretamente — é o critério "byte-idêntico" do handoff.

2. `TestSqlBackendContract` — gated em env (Supabase LOCAL com `ingest_all()`
   já rodado, mesmo protocolo de tests/test_tenant_isolation.py): exercita o
   backend SQL de verdade contra a tabela `entities`.

   Como rodar:
       supabase start && supabase migration up
       DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
       OPENAI_API_KEY=... python -m core.kg.gold      # popula `entities`
       SUPABASE_URL=http://127.0.0.1:54321 \\
       SUPABASE_SERVICE_KEY=<service_role_key de `supabase status -o env`> \\
       DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
       pytest tests/test_entity_catalog.py -v

   O shape de referência (chaves/tipos) vem de `hypergraph_catalog.edital_card()`
   chamada DIRETO sobre um grafo sintético (função pura, sem I/O) — o diretório
   `data/knowledge_graph/hypergraphs/` é efêmero/gitignored e está vazio neste
   ambiente, então não há dado real do hipergrado local para comparar via
   `list_editais()`. O teste de shape não perde força por isso: o que importa é
   se o backend SQL preenche as MESMAS chaves com os MESMOS tipos que o código
   legado emite — o grafo sintético só serve de gabarito determinístico disso.
"""
from __future__ import annotations

import os

import pytest

from core.kg import entity_catalog, hypergraph_catalog

_REQUIRED_ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "DATABASE_URL")


def _target_is_local() -> bool:
    """Vários módulos (ex. backend/api.py) chamam `load_dotenv()` no import —
    isso pode popular SUPABASE_URL/DATABASE_URL a partir do `.env` (que aponta
    pro REMOTO, sem a migration 036) mesmo sem o dev ter exportado nada. Sem
    esta guarda, o gate por presença de env passaria e os testes tentariam
    consultar `entities` no Postgres de produção. Mesmo protocolo de
    tests/test_company_chunks_rls.py."""
    if os.getenv("ENTITY_CATALOG_TEST_ALLOW_REMOTE", "").strip().lower() in ("1", "true", "yes"):
        return True
    dsn = os.getenv("DATABASE_URL", "")
    return "127.0.0.1" in dsn or "localhost" in dsn or "@db:" in dsn


def _skip_reason() -> str | None:
    missing = [v for v in _REQUIRED_ENV if not os.getenv(v)]
    if missing:
        return f"entity_catalog SQL backend — faltam envs: {', '.join(missing)} (gated, ver docstring)"
    if not _target_is_local():
        return (
            "entity_catalog SQL backend — DATABASE_URL não é local (provavelmente veio do "
            ".env via load_dotenv() de outro módulo, que aponta pro remoto sem migration 036). "
            "Rode contra Supabase local, ou force com ENTITY_CATALOG_TEST_ALLOW_REMOTE=1."
        )
    return None


# ---------------------------------------------------------------------------
# Camada 1 — dispatcher/default (sem I/O)
# ---------------------------------------------------------------------------

class TestDefaultBackendDelegation:
    """CATALOG_BACKEND default = hypergraph: o dispatcher é transparente —
    comportamento byte-idêntico ao `hypergraph_catalog` de antes desta mudança."""

    def test_get_edital_delegates_by_default(self, monkeypatch):
        monkeypatch.delenv("CATALOG_BACKEND", raising=False)
        sentinel = {"title": "Sentinela"}
        calls = []
        monkeypatch.setattr(
            hypergraph_catalog, "get_edital",
            lambda eid: (calls.append(eid), sentinel)[1],
        )
        assert entity_catalog.get_edital("finep:589") is sentinel
        assert calls == ["finep:589"]

    def test_list_editais_delegates_by_default(self, monkeypatch):
        monkeypatch.delenv("CATALOG_BACKEND", raising=False)
        sentinel = [{"title": "Sentinela"}]
        calls = []
        monkeypatch.setattr(
            hypergraph_catalog, "list_editais",
            lambda **kw: (calls.append(kw), sentinel)[1],
        )
        assert entity_catalog.list_editais(status="ABERTA", tema="ia", limit=5) is sentinel
        assert calls == [{"status": "ABERTA", "tema": "ia", "limit": 5}]

    def test_explicit_hypergraph_value_also_delegates(self, monkeypatch):
        monkeypatch.setenv("CATALOG_BACKEND", "hypergraph")
        monkeypatch.setattr(hypergraph_catalog, "get_edital", lambda eid: "legacy-path")
        assert entity_catalog.get_edital("finep:1") == "legacy-path"

    def test_sql_backend_does_not_touch_hypergraph_catalog(self, monkeypatch):
        """Só a troca de flag muda o caminho — sem condicional espalhada no
        consumidor (o dispatcher vive inteiramente em entity_catalog)."""
        monkeypatch.setenv("CATALOG_BACKEND", "sql")

        def _boom(*a, **kw):
            raise AssertionError("não deveria cair no hipergrado com CATALOG_BACKEND=sql")

        monkeypatch.setattr(hypergraph_catalog, "get_edital", _boom)
        monkeypatch.setattr(entity_catalog, "_sql_get_edital", lambda eid: {"via": "sql"})
        assert entity_catalog.get_edital("finep:1") == {"via": "sql"}


# ---------------------------------------------------------------------------
# Gabarito de shape — hypergraph_catalog.edital_card() é função pura (§8)
# ---------------------------------------------------------------------------

def _reference_card() -> dict:
    graph = {
        "proveniencia": {
            "url": "https://example.org/edital",
            "urls_documentos": ["https://example.org/edital.pdf"],
            "coletado_em": "2026-01-01",
        },
        "nodes": [
            {
                "id": "op:1", "type": "Oportunidade", "kind": "edital",
                "name": "Edital Sintético", "description": "Objetivo de teste",
                "prazo": "31/12/2026", "status": "aberta", "valor": "R$ 1.000.000",
                "fonte": "finep", "mecanismo": ["subvencao"],
                "requisitos_texto": ["Requisito A"],
                "constraints": [{"tipo": "porte", "op": "in", "valor": ["me"]}],
                "exclusoes_texto": ["Exclusão A"], "aperture": "prazo",
                "macro_temas": ["TIC"],
            },
            {"id": "c:tema1", "type": "Conceito", "dim": "tema", "name": "Saúde Digital"},
            {"id": "c:tec1", "type": "Conceito", "dim": "tecnologia", "name": "IA"},
            {"id": "c:apl1", "type": "Conceito", "dim": "aplicacao", "name": "Diagnóstico"},
            {"id": "op:prog1", "type": "Oportunidade", "kind": "programa", "name": "Programa X"},
            {"id": "ator:ict1", "type": "Ator", "kind": "ict", "name": "ICT Y"},
            {"id": "ator:inv1", "type": "Ator", "kind": "investidor", "name": "Fundo Z"},
        ],
        "edges": [],
    }
    return hypergraph_catalog.edital_card("finep__1", graph, full=True)


def _assert_same_shape(sql_card: dict, ref_card: dict) -> None:
    missing = set(ref_card) - set(sql_card)
    extra = set(sql_card) - set(ref_card)
    assert not missing, f"chaves ausentes no card SQL: {missing}"
    assert not extra, f"chaves extras (sem equivalente legado) no card SQL: {extra}"
    mismatched = []
    for key in ref_card:
        rv, sv = ref_card[key], sql_card[key]
        if rv is None or sv is None:
            continue  # None = "sem dado" em qualquer um dos lados, não é forma errada
        if type(rv) is not type(sv):
            mismatched.append((key, type(rv).__name__, type(sv).__name__))
    assert not mismatched, f"tipos divergentes (ref vs sql): {mismatched}"


@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
class TestSqlBackendContract:
    @pytest.fixture(autouse=True)
    def _sql_backend(self, monkeypatch):
        monkeypatch.setenv("CATALOG_BACKEND", "sql")

    def test_list_editais_nonempty_and_shape_matches(self):
        ref = _reference_card()
        cards = entity_catalog.list_editais(limit=200)
        assert cards, "entities (kind=edital) vazia — rodou `python -m core.kg.gold`?"
        for card in cards[:3]:
            _assert_same_shape(card, ref)

    def test_get_edital_shape_matches_for_sample(self):
        ref = _reference_card()
        sample = entity_catalog.list_editais(limit=3)
        assert sample, "sem editais para amostrar — ver test_list_editais_nonempty_and_shape_matches"
        for card in sample:
            fetched = entity_catalog.get_edital(card["id"])
            assert fetched is not None, f"get_edital não achou {card['id']!r} logo após list_editais listá-lo"
            _assert_same_shape(fetched, ref)
            assert fetched["id"] == card["id"]

    def test_get_edital_unknown_id_returns_none(self):
        assert entity_catalog.get_edital("finep:__inexistente__") is None

    def test_coverage_parity_report(self, capsys):
        """Paridade de cobertura SQL vs hipergrado (spec §10: 'SQL=45 ≥ hypergraph').
        O hipergrado é file-based e o diretório local está vazio/gitignored —
        reporta os dois números em vez de travar numa igualdade que depende de
        estado de disco fora do controle deste teste."""
        sql_editais = entity_catalog.list_editais(limit=10_000)
        hyper_editais = hypergraph_catalog.list_editais(limit=10_000)
        sql_ids = {c["id"] for c in sql_editais}
        hyper_ids = {c["id"] for c in hyper_editais}
        only_sql = sql_ids - hyper_ids
        only_hyper = hyper_ids - sql_ids
        print(
            f"\n[paridade catálogo] sql={len(sql_ids)} hypergraph={len(hyper_ids)} "
            f"só-sql={len(only_sql)} só-hypergraph={len(only_hyper)}"
        )
        assert sql_ids, "backend SQL sem editais — ingest não rodou neste Postgres local"
        if hyper_ids:
            assert len(sql_ids) >= len(hyper_ids), (
                "backend SQL tem MENOS editais que o hipergrado local — regressão de cobertura"
            )
