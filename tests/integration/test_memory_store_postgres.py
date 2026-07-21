"""Integração REAL do Store da memória Postgres (Etapa 5) — gated em DATABASE_URL.

Exercita o AsyncPostgresStore de verdade (pgvector no schema dedicado agent_memory,
não InMemory): put/search/delete persistem, o isolamento por namespace (workspace)
segura o multi-tenant pós RLS-bypass, e as tabelas vivem em agent_memory (invisível
ao PostgREST) — não em public.

Embed FAKE (determinístico, dims reais) injetado → zero token, zero rede; só o Store
toca o Postgres. Pula sem DATABASE_URL. Local: rode `scripts/setup_checkpointer.py`
antes (cria o schema + as tabelas do Store).
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("DATABASE_URL"),
        reason="Store Postgres real — requer DATABASE_URL (integração gated)",
    ),
]

import radar.core.llm.agent_graph as ag  # noqa: E402

_SIGNAL = ["trl", "contrapartida", "orcamento", "prazo"]
# Dim do embed fake — alinhado em runtime aos dims que o Store configurou (= coluna
# pgvector), via _make_fake_aembed(dims). Evita a fragilidade do EMBEDDING_DIMENSIONS
# import-time (768 do modelo OS vs 1536 default, conforme a ordem de carga do .env).


def _make_fake_aembed(dims: int):
    async def _fake_aembed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            tl = (t or "").lower()
            v = [0.0] * dims
            for i, w in enumerate(_SIGNAL):
                v[i] = float(tl.count(w))
            v[dims - 1] = 0.1  # evita norma zero
            out.append(v)
        return out
    return _fake_aembed


def _delete_ws(prefix: str) -> None:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c, c.cursor() as cur:
        for t in ("store_vectors", "store"):
            cur.execute(f"delete from agent_memory.{t} where prefix like %s", (prefix + "%",))


@pytest.fixture(scope="module", autouse=True)
def _teardown_runtime():
    yield
    ag.shutdown_writing_runtime()


@pytest.fixture
def real_store(monkeypatch):
    """O AsyncPostgresStore real (roda setup() na 1ª init), com embed fake. Reseta o
    singleton para re-inicializar com o fake patchado."""
    # Alinha o fake aos dims que a init configura (= coluna pgvector), lendo o ENV
    # do mesmo jeito que _init_memory_store. Garante casamento independente da ordem
    # de import do embedder na suíte.
    dims = int(os.environ.get("EMBEDDING_DIMENSIONS", "768"))
    monkeypatch.setattr(ag, "_aembed_for_store", _make_fake_aembed(dims))
    ag._memory_store = None
    ag._memory_store_ready = False
    store = ag._get_memory_store()
    assert store is not None, "Store não inicializou — rodou setup_checkpointer.py?"
    assert type(store).__name__ == "AsyncPostgresStore", (
        f"esperava AsyncPostgresStore, veio {type(store).__name__} (caiu no None/fallback?)"
    )
    assert store.index_config["dims"] == dims, (
        f"dims do Store ({store.index_config['dims']}) ≠ env ({dims}) — coluna pgvector "
        "pré-existente com outro dim? (gotcha de acoplamento de dims)"
    )
    return store


def test_put_search_delete_durable(real_store):
    ws = f"wsITEST_{uuid.uuid4().hex[:8]}"
    try:
        ag.memory_put(ws, "i1", "melhorar o TRL dos projetos", level=2)
        ag.memory_put(ws, "i2", "contrapartida foi o gargalo do orcamento", level=1)

        top = ag.memory_search(ws, "maturidade trl", limit=1)
        assert len(top) == 1
        assert "TRL" in top[0]["insight"]

        ag.memory_delete(ws, "i1")
        rest = ag.memory_search(ws, "trl", limit=6)
        assert all("TRL" not in r["insight"] for r in rest)
    finally:
        _delete_ws(ws)


def test_cross_workspace_isolation_durable(real_store):
    """GATE de segurança: insight do workspace A é INVISÍVEL pelo namespace de B
    contra o Postgres real (isolamento = namespace por workspace_id)."""
    a = f"wsITEST_{uuid.uuid4().hex[:8]}_A"
    b = f"wsITEST_{uuid.uuid4().hex[:8]}_B"
    try:
        ag.memory_put(a, "i1", "segredo do TRL do workspace A", level=2)
        res_b = ag.memory_search(b, "trl", limit=6)
        assert res_b == [], "VAZAMENTO: workspace B leu insight de A"
        res_a = ag.memory_search(a, "trl", limit=6)
        assert any("workspace A" in r["insight"] for r in res_a)
    finally:
        _delete_ws(a)
        _delete_ws(b)


def test_store_tables_live_in_agent_memory_not_public(real_store):
    """As tabelas do Store ficam no schema dedicado agent_memory (fora do PostgREST),
    não em public — defesa do RLS-bypass por construção."""
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "select table_schema from information_schema.tables "
            "where table_name = 'store_vectors'"
        )
        schemas = {r[0] for r in cur.fetchall()}
    assert "agent_memory" in schemas
    assert "public" not in schemas
