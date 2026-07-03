"""Leak-test de isolamento multi-tenant (pré-beta) — docs/specs/pre-beta-verification.md.

Modelo de ameaça: um usuário autenticado do workspace B tentando ler/escrever
estado do workspace A. Ataca as quatro superfícies, cada uma com sua defesa:

  S1  PostgREST direto (anon key + JWT de B)  → defesa = RLS
  S2  API FastAPI (handlers com ID de recurso) → defesa = scoping server-side
  S3  Camada agêntica (checkpointer + Store)   → defesa = namespacing por workspace
  S4  DEMO_MODE (service-role)                  → defesa = guard de ambiente

Integração REAL, gated em env (Supabase local): SUPABASE_URL, SUPABASE_ANON_KEY,
SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET, DATABASE_URL. Pula sem elas (CI sem DB).

Como rodar (local):
    supabase start && supabase migration up          # aplica migrations, inclui 034
    export $(supabase status -o env | sed 's/^/SUPABASE_/' ...)   # ver README abaixo
    # ou, mais simples, exporte manualmente a partir de `supabase status -o env`:
    #   SUPABASE_URL=http://127.0.0.1:54321
    #   SUPABASE_ANON_KEY=<ANON_KEY>
    #   SUPABASE_SERVICE_KEY=<SERVICE_ROLE_KEY>
    #   SUPABASE_JWT_SECRET=<JWT_SECRET>
    #   DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
    pytest tests/test_tenant_isolation.py -v

Contra staging: aponte as mesmas envs para o projeto remoto (JWT_SECRET só existe
em projetos com JWT legado HS256; projetos ES256 exigem outra estratégia de token
— ver nota em _make_user_jwt).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

_REQUIRED_ENV = (
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_JWT_SECRET",
    "DATABASE_URL",
)


def _target_is_local() -> bool:
    """A suíte CRIA e APAGA auth.users/workspaces — jamais deve tocar um banco
    remoto por acidente (um `.env` de prod carregado num `pytest -q` normal
    apontaria o DATABASE_URL para produção). Por padrão só roda contra localhost;
    para a run deliberada contra staging, exporte TENANT_ISOLATION_ALLOW_REMOTE=1."""
    if os.getenv("TENANT_ISOLATION_ALLOW_REMOTE", "").strip().lower() in ("1", "true", "yes"):
        return True
    dsn = os.getenv("DATABASE_URL", "")
    return "127.0.0.1" in dsn or "localhost" in dsn or "@db:" in dsn


def _skip_reason() -> str | None:
    missing = [v for v in _REQUIRED_ENV if not os.getenv(v)]
    if missing:
        return f"Leak-test multi-tenant — faltam envs: {', '.join(missing)} (gated)"
    if not _target_is_local():
        return (
            "Leak-test multi-tenant — DATABASE_URL não é local e a suíte cria/apaga "
            "auth.users. Rode contra Supabase local, ou force com "
            "TENANT_ISOLATION_ALLOW_REMOTE=1 (staging deliberado)."
        )
    return None


pytestmark = pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")

import jwt as pyjwt  # noqa: E402  (só importa quando o gate passa)
import psycopg  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

from core.db import get_supabase_service, get_supabase_user  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de identidade
# ─────────────────────────────────────────────────────────────────────────────
def _make_user_jwt(user_id: str) -> str:
    """Forja um JWT HS256 como o GoTrue emitiria para `user_id` (role
    authenticated). O PostgREST valida com o mesmo SUPABASE_JWT_SECRET e RLS lê
    `auth.uid()` = sub. Projetos com JWT ES256 (Supabase CLI ≥ 2) não expõem o
    secret — lá, gere o token via signInWithPassword no lugar deste helper."""
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": user_id,
            "role": "authenticated",
            "aud": "authenticated",
            "iat": now,
            "exp": now + 3600,
            "email": f"{user_id}@leak.test",
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def _pg():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def _create_user_and_workspace(email_tag: str) -> tuple[str, str]:
    """Cria auth.users + workspace via conexão direta (owner). Retorna
    (user_id, workspace_id)."""
    user_id = str(uuid.uuid4())
    with _pg() as c, c.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email, aud, role) values (%s, %s, "
            "'authenticated', 'authenticated')",
            (user_id, f"{email_tag}-{user_id}@leak.test"),
        )
    svc = get_supabase_service()
    row = svc.table("workspaces").insert({"user_id": user_id, "profile": {}}).execute()
    return user_id, row.data[0]["id"]


def _cleanup(user_id: str) -> None:
    # workspace cascata (FK on delete cascade) leva junto content_items,
    # writing_sessions, session_turns, application_log, matching_weights,
    # reflection_insights, research_findings, exploration_log, weight_change_log,
    # company_hypergraphs. Depois removemos o user (cascata pega o workspace).
    with _pg() as c, c.cursor() as cur:
        cur.execute("delete from auth.users where id = %s", (user_id,))


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: dois tenants, A semeado com uma linha em cada tabela workspace-scoped.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def two_tenants():
    user_a, ws_a = _create_user_and_workspace("a")
    user_b, ws_b = _create_user_and_workspace("b")

    svc = get_supabase_service()  # bypassa RLS — semeia dados de A

    # Semeia UMA linha por tabela workspace-scoped no workspace de A.
    sess = svc.table("writing_sessions").insert(
        {"workspace_id": ws_a, "kind": "writing", "edital_id": "finep:leak-a",
         "status": "active"}
    ).execute().data[0]
    session_a = sess["id"]
    svc.table("session_turns").insert(
        {"session_id": session_a, "turn_index": 0, "role": "user",
         "content": "SEGREDO-DE-A"}
    ).execute()
    content_a = svc.table("content_items").insert(
        {"workspace_id": ws_a, "title": "SEGREDO-DE-A", "type": "other",
         "content": "conteúdo confidencial de A"}
    ).execute().data[0]["id"]
    app_a = svc.table("application_log").insert(
        {"workspace_id": ws_a, "edital_id": "finep:leak-a", "status": "matched"}
    ).execute().data[0]["id"]
    svc.table("matching_weights").insert(
        {"workspace_id": ws_a, "dimension": "trl", "weight": 20}
    ).execute()
    svc.table("reflection_insights").insert(
        {"workspace_id": ws_a, "level": 1, "insight": "SEGREDO-DE-A"}
    ).execute()
    svc.table("research_findings").insert(
        {"workspace_id": ws_a, "question": "SEGREDO-DE-A"}
    ).execute()
    svc.table("exploration_log").insert(
        {"workspace_id": ws_a, "edital_id": "finep:leak-a", "decision": "recommended"}
    ).execute()
    svc.table("company_hypergraphs").insert({"workspace_id": ws_a}).execute()
    svc.table("user_feedback").insert(
        {"user_id": user_a, "message": "SEGREDO-DE-A"}
    ).execute()

    # Tabelas service-only (RLS on, sem policy): semeia p/ provar que authenticated
    # não lê nem o que existe.
    svc.table("kg_artifacts").insert(
        {"key": f"leak-test-{uuid.uuid4()}", "blob": {"x": 1}}
    ).execute()
    src_id = f"finep:leak-{uuid.uuid4()}"
    svc.table("edital_source_docs").insert(
        {"edital_id": src_id, "source": "finep", "canonical_doc": [{"doc_name": "d"}]}
    ).execute()

    # procrastinate_jobs (owner insert — a lib usaria RPC; aqui é seed direto).
    with _pg() as c, c.cursor() as cur:
        cur.execute(
            "insert into public.procrastinate_jobs (task_name, args, status, "
            "queue_name) values ('leak_probe', %s::jsonb, 'todo', 'default')",
            ('{"workspace_id": "SEGREDO-DE-A"}',),
        )

    ctx = {
        "user_a": user_a, "ws_a": ws_a, "session_a": session_a,
        "content_a": content_a, "app_a": app_a,
        "user_b": user_b, "ws_b": ws_b,
        "jwt_b": _make_user_jwt(user_b),
    }
    yield ctx

    _cleanup(user_a)
    _cleanup(user_b)
    with _pg() as c, c.cursor() as cur:
        cur.execute("delete from public.procrastinate_jobs where task_name = 'leak_probe'")
        cur.execute("delete from public.kg_artifacts where key like 'leak-test-%'")
        cur.execute("delete from public.edital_source_docs where edital_id like 'finep:leak-%'")


# Tabelas workspace/user-scoped (política "own"): B nunca deve ver linha de A.
_OWN_SCOPED_TABLES = [
    "workspaces",
    "content_items",
    "writing_sessions",
    "session_turns",
    "application_log",
    "matching_weights",
    "reflection_insights",
    "research_findings",
    "exploration_log",
    "company_hypergraphs",
    "user_feedback",
]

# Tabelas service-only: RLS ligada sem policy → authenticated lê 0 linhas.
_SERVICE_ONLY_TABLES = ["kg_artifacts", "edital_source_docs"]

# Tabelas de leitura compartilhada (por design): authenticated LÊ (não é leak).
_SHARED_READ_TABLES = ["edital_chunks", "discovered_opportunities", "web_sources",
                       "playbook_overlays"]


# ─────────────────────────────────────────────────────────────────────────────
# S1 — PostgREST direto (RLS)
# ─────────────────────────────────────────────────────────────────────────────
class TestS1PostgRESTRLS:
    @pytest.mark.parametrize("table", _OWN_SCOPED_TABLES)
    def test_b_nao_le_linha_de_a(self, two_tenants, table):
        """B (JWT próprio, anon key) não enxerga nenhuma linha de A."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        rows = db_b.table(table).select("*").execute().data
        # B pode ter linhas próprias (matching_weights globais são visíveis!),
        # então filtramos: nenhuma linha pode pertencer a A.
        for r in rows:
            assert r.get("workspace_id") != two_tenants["ws_a"], (
                f"LEAK: B leu linha de A em {table}: {r}"
            )
            assert r.get("user_id") != two_tenants["user_a"], (
                f"LEAK: B leu linha de A em {table}: {r}"
            )

    @pytest.mark.parametrize("table", _OWN_SCOPED_TABLES)
    def test_b_nao_escreve_no_workspace_de_a(self, two_tenants, table):
        """INSERT de B mirando o workspace de A é barrado pela policy (with check)."""
        if table in ("workspaces", "user_feedback"):
            pytest.skip("scoping por user_id, coberto pelo teste de leitura")
        db_b = get_supabase_user(two_tenants["jwt_b"])
        payload = {"workspace_id": two_tenants["ws_a"]}
        with pytest.raises(APIError):
            db_b.table(table).insert(payload).execute()

    def test_b_nao_le_service_only_via_rls(self, two_tenants):
        """kg_artifacts / edital_source_docs: RLS sem policy → 0 linhas p/ B."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        for table in _SERVICE_ONLY_TABLES:
            rows = db_b.table(table).select("*").execute().data
            assert rows == [], f"LEAK: authenticated leu {table} (deveria ser deny-all)"

    def test_procrastinate_surface_negada(self, two_tenants):
        """REGRESSÃO (migration 034): a fila procrastinate não é acessível por
        authenticated — nem SELECT na tabela nem RPC de controle de fila.

        Antes do fix: anon lia `args` (workspace_id/payloads cross-tenant) e
        deletava jobs. Ver docs/specs/pre-beta-verification.md Frente 1."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        with pytest.raises(APIError):
            db_b.table("procrastinate_jobs").select("*").execute()
        with pytest.raises(APIError):
            db_b.rpc("procrastinate_defer_jobs_v1", {"jobs": []}).execute()

    def test_shared_read_visivel_por_design(self, two_tenants):
        """Contra-prova: as tabelas de leitura compartilhada NÃO levantam para
        authenticated (são globais por design). Trava regressões que apertem
        demais e quebrem o catálogo, ou afrouxem e exponham dado tenant."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        for table in _SHARED_READ_TABLES:
            # Não deve levantar; conteúdo pode estar vazio.
            db_b.table(table).select("*").limit(1).execute()

    def test_agent_memory_fora_do_postgrest(self, two_tenants):
        """O schema agent_memory (checkpointer + Store) não é servido pelo
        PostgREST (migration 028) — a tabela `store` não existe pela REST API."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        with pytest.raises(APIError):
            db_b.table("store").select("*").limit(1).execute()


# ─────────────────────────────────────────────────────────────────────────────
# S2 — API FastAPI (scoping nos handlers)
# ─────────────────────────────────────────────────────────────────────────────
class TestS2HandlerScoping:
    def test_writing_session_load_rejeita_via_rls(self, two_tenants):
        """WritingSession com o cliente RLS de B + session_id de A → não encontra
        (RLS filtra a linha antes do handler)."""
        from core.services.writing_session import WritingSession
        from domain.user_profile import CompanyProfile

        db_b = get_supabase_user(two_tenants["jwt_b"])
        with pytest.raises(ValueError):
            WritingSession(
                db=db_b,
                workspace_id=two_tenants["ws_b"],
                profile=CompanyProfile(),
                session_id=two_tenants["session_a"],
            )

    def test_writing_session_load_rejeita_mismatch_explicito(self, two_tenants):
        """Defesa em profundidade: mesmo com um cliente que bypassa RLS
        (service-role), a checagem explícita de workspace em _load_from_db barra
        session de A quando o workspace ativo é o de B."""
        from core.services.writing_session import WritingSession
        from domain.user_profile import CompanyProfile

        svc = get_supabase_service()  # bypassa RLS de propósito
        with pytest.raises(ValueError):
            WritingSession(
                db=svc,
                workspace_id=two_tenants["ws_b"],  # workspace de B
                profile=CompanyProfile(),
                session_id=two_tenants["session_a"],  # sessão de A
            )


# ─────────────────────────────────────────────────────────────────────────────
# S3 — Camada agêntica (namespacing do Store; sem RLS por design)
# ─────────────────────────────────────────────────────────────────────────────
class TestS3AgenticNamespacing:
    def test_store_isola_memoria_por_workspace(self, two_tenants, monkeypatch):
        """memory_put no workspace de A não é recuperável por memory_search do
        workspace de B — o namespace (workspace_id, "insights") segura o
        multi-tenant depois do RLS-bypass do Store.

        Embed FAKE determinístico (dims do Store) → zero token/rede."""
        import core.llm.agent_graph as ag

        # O Store liga a função de embed no init — patch ANTES de _get_memory_store,
        # resetando o singleton (mesmo protocolo de test_memory_store_postgres).
        dims = _store_dims()
        monkeypatch.setattr(ag, "_aembed_for_store", _make_fake_aembed(dims))
        ag._memory_store = None
        ag._memory_store_ready = False
        store = ag._get_memory_store()
        if store is None:
            pytest.skip("Store indisponível (rode scripts/setup_checkpointer.py)")

        ws_a, ws_b = two_tenants["ws_a"], two_tenants["ws_b"]
        key = str(uuid.uuid4())
        marker = f"SEGREDO-DE-A-{key}"
        # `memory_search` retorna {"insight","level","score"} — casamos pelo texto.
        insight = f"trl contrapartida orcamento prazo {marker}"
        ag.memory_put(ws_a, key, insight)
        try:
            hits_b = ag.memory_search(ws_b, "trl contrapartida orcamento prazo", limit=6)
            assert all(marker not in h["insight"] for h in hits_b), (
                "LEAK: memória de A recuperável pelo workspace de B"
            )
            hits_a = ag.memory_search(ws_a, "trl contrapartida orcamento prazo", limit=6)
            assert any(marker in h["insight"] for h in hits_a), (
                "A não recupera a própria memória — teste inconclusivo"
            )
        finally:
            ag.memory_delete(ws_a, key)

    def test_checkpoints_fora_do_postgrest(self, two_tenants):
        """As tabelas do CHECKPOINTER (agent_memory.checkpoints*) também são
        invisíveis pela REST API — complementa o teste da tabela `store`
        (mesmo schema, mesma defesa da migration 028)."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            with pytest.raises(APIError):
                db_b.table(table).select("*").limit(1).execute()

    def test_checkpointer_isola_thread_por_workspace(self, two_tenants):
        """Leak-test DURÁVEL do checkpointer (pendência da migração LangGraph):
        o AsyncPostgresSaver bypassa RLS por design — o isolamento é 100% o
        namespace do thread_id ({workspace_id}:{session_id}:{turn}).

        Grava um checkpoint no thread de A e prova que B, conhecendo session_id
        e turn (tudo menos o prefixo), NÃO alcança o estado: o thread homólogo
        sob o workspace de B volta vazio. O prefixo vem sempre do servidor
        (JWT→workspace, ver teste de convenção abaixo), nunca de input do user.
        """
        from langgraph.checkpoint.base import empty_checkpoint

        import core.llm.agent_graph as ag

        saver = ag._get_writing_checkpointer()
        if type(saver).__name__ != "AsyncPostgresSaver":
            pytest.skip("checkpointer durável indisponível (DATABASE_URL/setup)")

        ws_a, ws_b = two_tenants["ws_a"], two_tenants["ws_b"]
        session, turn = str(uuid.uuid4()), 1
        thread_a = f"{ws_a}:{session}:{turn}"
        cfg_a = {"configurable": {"thread_id": thread_a, "checkpoint_ns": ""}}
        cp = empty_checkpoint()
        cp["channel_values"] = {"segredo": "SEGREDO-DE-A"}

        ag._run_on_bg_loop(saver.aput(cfg_a, cp, {"source": "leak-test", "step": 1}, {}))
        try:
            tup_a = ag._run_on_bg_loop(saver.aget_tuple(cfg_a))
            assert tup_a is not None, "A não lê o próprio checkpoint — inconclusivo"

            # B tenta o MESMO session/turn sob o seu workspace → nada.
            thread_b_guess = f"{ws_b}:{session}:{turn}"
            tup_b = ag._run_on_bg_loop(saver.aget_tuple(
                {"configurable": {"thread_id": thread_b_guess, "checkpoint_ns": ""}}
            ))
            assert tup_b is None, (
                "LEAK: checkpoint de A alcançável por thread homólogo de B"
            )
        finally:
            ag._run_on_bg_loop(saver.adelete_thread(thread_a))

    def test_thread_id_sempre_prefixado_pelo_workspace(self):
        """Trava de convenção: TODO thread_id do runtime de escrita nasce de
        f\"{self.workspace_id}:{self.session_id}:...\" — o prefixo vem do
        estado server-side da sessão (que o RLS/S2 protege), nunca do request.
        Se alguém trocar a construção, o teste durável acima perde a premissa."""
        import inspect

        from core.services import writing_session as ws

        src = inspect.getsource(ws.WritingSession)
        assert 'f"{self.workspace_id}:{self.session_id}:' in src, (
            "thread_id do turno deve ser prefixado pelo workspace da sessão"
        )

    def test_subagente_nao_herda_checkpointer_do_pai(self, monkeypatch):
        """S3: o caminho stateless (subagentes/kg_match/profile) compila o grafo
        com checkpointer=False, não None — None faria o LangGraph HERDAR o
        checkpointer do pai (cross-loop crash + persistência indevida). Trava a
        regressão que o comentário em agent_graph.py:421 descreve."""
        import core.llm.agent_graph as ag

        captured = {}
        real_build_graph = ag._build_graph

        def spy_build_graph(*args, **kwargs):
            captured["checkpointer"] = kwargs.get("checkpointer", "MISSING")
            return real_build_graph(*args, **kwargs)

        monkeypatch.setattr(ag, "_build_graph", spy_build_graph)
        monkeypatch.setattr(
            ag, "_build_chat_model", lambda *a, **k: object()
        )
        # run_agent_graph_async (caminho stateless) deve pedir checkpointer=False.
        import inspect
        src = inspect.getsource(ag.run_agent_graph_async)
        assert "checkpointer=False" in src, (
            "run_agent_graph_async deve compilar com checkpointer=False (herança "
            "do pai quebra isolamento cross-loop)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers do Store (S3)
# ─────────────────────────────────────────────────────────────────────────────
_SIGNAL = ["trl", "contrapartida", "orcamento", "prazo"]


def _store_dims() -> int:
    """Dims que o Store configurou (= coluna pgvector), lidos do próprio schema."""
    with _pg() as c, c.cursor() as cur:
        cur.execute(
            "select atttypmod from pg_attribute a "
            "join pg_class rel on rel.oid = a.attrelid "
            "join pg_namespace n on n.oid = rel.relnamespace "
            "where n.nspname = 'agent_memory' and rel.relname = 'store_vectors' "
            "and a.attname = 'embedding'"
        )
        row = cur.fetchone()
    # pgvector guarda a dim direto em atttypmod (sem o -4 dos varchar).
    return int(row[0]) if row and row[0] and row[0] > 0 else 1536


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


# ─────────────────────────────────────────────────────────────────────────────
# S4 — DEMO_MODE (guard de ambiente; service-role bypassa RLS)
# ─────────────────────────────────────────────────────────────────────────────
class TestS4DemoModeGuard:
    def test_guard_recusa_boot_demo_em_producao(self, monkeypatch):
        """DEMO_MODE=1 em produção colapsa todos os tenants num workspace único
        (service-role). O guard recusa o boot salvo override deliberado. Espelha
        test_hardening_pr1 — replicado aqui para a suíte de isolamento ser
        auto-contida nas 4 superfícies."""
        from backend.api import _guard_demo_mode

        for var in ("DEMO_MODE", "RAILWAY_ENVIRONMENT", "ENVIRONMENT",
                    "DEMO_MODE_ALLOW_PROD"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="DEMO_MODE"):
            _guard_demo_mode()

    def test_guard_permite_override_deliberado(self, monkeypatch):
        from backend.api import _guard_demo_mode

        for var in ("DEMO_MODE", "RAILWAY_ENVIRONMENT", "ENVIRONMENT",
                    "DEMO_MODE_ALLOW_PROD"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        monkeypatch.setenv("DEMO_MODE_ALLOW_PROD", "1")
        _guard_demo_mode()  # não deve levantar
