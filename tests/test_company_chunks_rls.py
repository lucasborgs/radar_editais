"""Leak-test de isolamento multi-tenant de `company_chunks` (migration 036, Fase 0).

`company_chunks` carrega dado do tenant (perfil/documentos da empresa) e é critério
de aceite da Fase 0: um usuário autenticado do workspace B jamais lê nem escreve
linhas do workspace A. Segue o protocolo de tests/test_tenant_isolation.py (S1 —
PostgREST direto, defesa = RLS).

Integração REAL, gated em env (Supabase local): SUPABASE_URL, SUPABASE_ANON_KEY,
SUPABASE_SERVICE_KEY, SUPABASE_JWT_SECRET, DATABASE_URL. Pula sem elas (CI sem DB).
Como rodar: ver cabeçalho de tests/test_tenant_isolation.py.
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
    """A suíte CRIA e APAGA auth.users/workspaces — nunca deve tocar um banco
    remoto por acidente. Só roda contra localhost salvo override deliberado."""
    if os.getenv("TENANT_ISOLATION_ALLOW_REMOTE", "").strip().lower() in ("1", "true", "yes"):
        return True
    dsn = os.getenv("DATABASE_URL", "")
    return "127.0.0.1" in dsn or "localhost" in dsn or "@db:" in dsn


def _skip_reason() -> str | None:
    missing = [v for v in _REQUIRED_ENV if not os.getenv(v)]
    if missing:
        return f"Leak-test company_chunks — faltam envs: {', '.join(missing)} (gated)"
    if not _target_is_local():
        return (
            "Leak-test company_chunks — DATABASE_URL não é local e a suíte cria/apaga "
            "auth.users. Rode contra Supabase local, ou force com TENANT_ISOLATION_ALLOW_REMOTE=1."
        )
    return None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or ""),
]

import jwt as pyjwt  # noqa: E402  (só importa quando o gate passa)
import psycopg  # noqa: E402
from postgrest.exceptions import APIError  # noqa: E402

from core.infra.db import get_supabase_service, get_supabase_user  # noqa: E402

_DUMMY_EMB = [0.1] + [0.0] * 1535  # 1536d — evita norma zero


def _make_user_jwt(user_id: str) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": user_id, "role": "authenticated", "aud": "authenticated",
            "iat": now, "exp": now + 3600, "email": f"{user_id}@leak.test",
        },
        os.environ["SUPABASE_JWT_SECRET"],
        algorithm="HS256",
    )


def _pg():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def _create_user_and_workspace(tag: str) -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    with _pg() as c, c.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email, aud, role) values (%s, %s, "
            "'authenticated', 'authenticated')",
            (user_id, f"{tag}-{user_id}@leak.test"),
        )
    svc = get_supabase_service()
    row = svc.table("workspaces").insert({"user_id": user_id, "profile": {}}).execute()
    return user_id, row.data[0]["id"]


@pytest.fixture(scope="module")
def two_tenants():
    user_a, ws_a = _create_user_and_workspace("cc-a")
    user_b, ws_b = _create_user_and_workspace("cc-b")

    svc = get_supabase_service()  # bypassa RLS — semeia dado de A
    chunk_a = svc.table("company_chunks").insert(
        {"workspace_id": ws_a, "origin": "profile", "text": "SEGREDO-DE-A", "embedding": _DUMMY_EMB}
    ).execute().data[0]["id"]

    ctx = {
        "user_a": user_a, "ws_a": ws_a, "chunk_a": chunk_a,
        "user_b": user_b, "ws_b": ws_b, "jwt_a": _make_user_jwt(user_a),
        "jwt_b": _make_user_jwt(user_b),
    }
    yield ctx

    for uid in (user_a, user_b):
        with _pg() as c, c.cursor() as cur:
            cur.execute("delete from auth.users where id = %s", (uid,))


class TestCompanyChunksRLS:
    def test_b_nao_le_linha_de_a(self, two_tenants):
        """B (JWT próprio, anon key) não enxerga nenhuma linha de A."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        rows = db_b.table("company_chunks").select("*").execute().data
        for r in rows:
            assert r.get("workspace_id") != two_tenants["ws_a"], f"LEAK: B leu linha de A: {r}"
        assert all(r.get("text") != "SEGREDO-DE-A" for r in rows)

    def test_b_nao_escreve_no_workspace_de_a(self, two_tenants):
        """INSERT de B mirando o workspace de A é barrado pela policy (with check)."""
        db_b = get_supabase_user(two_tenants["jwt_b"])
        with pytest.raises(APIError):
            db_b.table("company_chunks").insert(
                {"workspace_id": two_tenants["ws_a"], "origin": "profile",
                 "text": "invasão-de-B", "embedding": _DUMMY_EMB}
            ).execute()

    def test_a_le_a_propria_linha(self, two_tenants):
        """Contra-prova: A recupera a própria linha (isolamento não é deny-all)."""
        db_a = get_supabase_user(two_tenants["jwt_a"])
        rows = db_a.table("company_chunks").select("*").eq("workspace_id", two_tenants["ws_a"]).execute().data
        assert any(r.get("text") == "SEGREDO-DE-A" for r in rows), "A não lê a própria linha — inconclusivo"


class TestCaminhoNovoV3:
    """RLS no CAMINHO NOVO (Fase 2): o refresh on-demand do match
    (`ensure_company_chunks`, psycopg service-side) escreve SÓ o workspace
    pedido, e `load_company_chunks` (o insumo do Stage 2) devolve SÓ os chunks
    daquele workspace — mesmo com outro tenant populado ao lado."""

    def test_refresh_e_load_escopados_por_workspace(self, two_tenants, monkeypatch):
        import core.retrieval.embedder as embedder
        from core.services.company_chunks import ensure_company_chunks, load_company_chunks

        # sem rede: embedding fake determinístico (o alvo do teste é o escopo)
        monkeypatch.setattr(
            embedder, "embed_texts", lambda texts: [[0.1] * 1536 for _ in texts],
        )
        profile_b = {
            "nome": "Empresa-B",
            "one_liner": "Perfil exclusivo de B para o leak-test do caminho novo.",
            "descricao_atividades": "Atividades de B. " * 30,  # rico → sem HyDE/LLM
        }
        n = ensure_company_chunks(two_tenants["ws_b"], profile_b)
        assert n > 0

        texts_b, embs_b = load_company_chunks(two_tenants["ws_b"])
        assert len(texts_b) == n
        assert all("SEGREDO-DE-A" not in t for t in texts_b), "LEAK: chunk de A no lado B"
        assert embs_b.shape[0] == n

        # o refresh de B não tocou as linhas de A
        db_a = get_supabase_user(two_tenants["jwt_a"])
        rows_a = (
            db_a.table("company_chunks").select("text")
            .eq("workspace_id", two_tenants["ws_a"]).execute().data
        )
        assert any(r["text"] == "SEGREDO-DE-A" for r in rows_a)

        # e B, via PostgREST (RLS), vê exatamente o que o motor leu — nada de A
        db_b = get_supabase_user(two_tenants["jwt_b"])
        rows_b = db_b.table("company_chunks").select("workspace_id, text").execute().data
        assert {r["workspace_id"] for r in rows_b} <= {two_tenants["ws_b"]}
