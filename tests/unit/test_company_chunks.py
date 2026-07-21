"""Lado empresa do match v3 (`core/services/company_chunks`) — puro, sem DB/rede.

Cobre: chunking do perfil (mesmo _pack_chunks do gold), regra de cold start
(HyDE só sem docs + perfil ralo), e o refresh on-demand (diff determinístico:
sem mudança = zero embeddings; mudança = rewrite atômico do workspace; HyDE
fora do diff — falha de geração não vira re-embed perpétuo). O leak-test RLS
da tabela é integração à parte (tests/integration/test_company_chunks_rls.py).
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from radar.core.services import company_chunks as cc

pytestmark = pytest.mark.unit

RICH_PROFILE = {
    "nome": "iFlorestal",
    "one_liner": "Plataforma de monitoramento florestal por sensoriamento remoto e IA.",
    "solution_summary": "Imagens de satélite e visão computacional para detectar "
    "desmatamento, estimar biomassa e certificar origem de produtos florestais. " * 3,
    "descricao_atividades": "Geoprocessamento e IA aplicada à bioeconomia. " * 5,
}
THIN_PROFILE = {"nome": "ACME", "one_liner": "IA para triagem médica."}


# ── chunking do perfil ────────────────────────────────────────────────────────

def test_profile_chunk_texts_vazio():
    # tipo_entidade tem default "empresa" no dataclass — zerado, o perfil é
    # de fato vazio e vira zero chunks (o placeholder do to_context não conta).
    assert cc.profile_chunk_texts({"tipo_entidade": ""}) == []


def test_profile_chunk_texts_empacota():
    texts = cc.profile_chunk_texts(RICH_PROFILE)
    assert texts
    assert all(t.strip() for t in texts)
    assert any("iFlorestal" in t for t in texts)


# ── cold start / HyDE ────────────────────────────────────────────────────────

def test_hyde_wanted_so_no_cold_start():
    assert cc.hyde_wanted(THIN_PROFILE, []) is True            # ralo, sem docs
    assert cc.hyde_wanted(RICH_PROFILE, []) is False           # rico, sem docs
    assert cc.hyde_wanted(THIN_PROFILE, [{"id": "d1", "content": "doc"}]) is False
    assert cc.hyde_wanted({}, []) is False                     # sem semente p/ HyDE


def test_desired_chunks_inclui_library_com_doc_id():
    items = [{"id": "d1", "content": "Relatório técnico da empresa.\nSegunda linha."}]
    out = cc.desired_chunks(RICH_PROFILE, items)
    origins = {c.origin for c in out}
    assert origins == {"profile", "library_doc"}
    libs = [c for c in out if c.origin == "library_doc"]
    assert all(c.doc_id == "d1" for c in libs)
    assert not any(c.origin == "hyde" for c in out)  # HyDE nunca entra no diff


# ── refresh on-demand (conn fake) ────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        verb = sql.strip().split()[0].lower()
        self._conn.calls.append((verb, params))
        if verb == "select":
            self._rows = list(self._conn.rows)
        elif verb == "delete":
            self._conn.rows = []
        elif verb == "insert":
            # (workspace_id, origin, doc_id, text, vec)
            self._conn.rows.append((params[1], params[2], params[3]))

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Simula company_chunks de UM workspace: rows = [(origin, doc_id, text)]."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls: list[tuple] = []

    def cursor(self):
        return _FakeCursor(self)

    def transaction(self):
        return nullcontext()

    @property
    def verbs(self):
        return [v for v, _ in self.calls]


@pytest.fixture
def no_network(monkeypatch):
    embeds: list[list[str]] = []

    def fake_embed(texts):
        embeds.append(list(texts))
        return [[0.1] * 4 for _ in texts]

    import radar.core.retrieval.embedder as embedder
    import radar.core.retrieval.hyde as hyde
    monkeypatch.setattr(embedder, "embed_texts", fake_embed)
    monkeypatch.setattr(hyde, "generate_hyde_doc", lambda q: f"pseudo-doc sobre {q[:30]}")
    return embeds


def test_ensure_sem_mudanca_zero_embeddings(no_network):
    atual = [("profile", None, t) for t in cc.profile_chunk_texts(RICH_PROFILE)]
    conn = _FakeConn(atual)
    n = cc.ensure_company_chunks("ws-1", RICH_PROFILE, conn=conn)
    assert n == len(atual)
    assert no_network == []                 # nenhum embed
    assert "delete" not in conn.verbs       # nenhuma escrita


def test_ensure_perfil_mudou_rewrite_atomico(no_network):
    conn = _FakeConn([("profile", None, "texto antigo do perfil")])
    n = cc.ensure_company_chunks("ws-1", RICH_PROFILE, conn=conn)
    assert n == len(cc.profile_chunk_texts(RICH_PROFILE))
    assert "delete" in conn.verbs and "insert" in conn.verbs
    assert len(no_network) == 1             # 1 batch de embed
    assert {r[0] for r in conn.rows} == {"profile"}


def test_ensure_cold_start_adiciona_hyde(no_network):
    conn = _FakeConn([])
    n = cc.ensure_company_chunks("ws-1", THIN_PROFILE, conn=conn)
    origins = [r[0] for r in conn.rows]
    assert "hyde" in origins and "profile" in origins
    assert n == len(conn.rows)
    # segunda chamada: nada mudou (hyde presente + det iguais) → no-op
    calls_before = len(no_network)
    cc.ensure_company_chunks("ws-1", THIN_PROFILE, conn=conn)
    assert len(no_network) == calls_before


def test_ensure_hyde_falhou_nao_reembeda_para_sempre(monkeypatch, no_network):
    import radar.core.retrieval.hyde as hyde
    monkeypatch.setattr(hyde, "generate_hyde_doc", lambda q: "")  # HyDE indisponível
    conn = _FakeConn([])
    cc.ensure_company_chunks("ws-1", THIN_PROFILE, conn=conn)
    assert not any(r[0] == "hyde" for r in conn.rows)  # degradou p/ só-perfil
    # a próxima chamada tenta o HyDE de novo, mas o rewrite é idempotente
    cc.ensure_company_chunks("ws-1", THIN_PROFILE, conn=conn)
    assert {r[0] for r in conn.rows} == {"profile"}


def test_ensure_saiu_do_cold_start_remove_hyde(no_network, monkeypatch):
    # estado atual: perfil ralo + hyde
    atual = [("profile", None, t) for t in cc.profile_chunk_texts(THIN_PROFILE)]
    atual.append(("hyde", None, "pseudo-doc velho"))
    conn = _FakeConn(atual)
    # library ganhou um doc → não é mais cold start → hyde some
    monkeypatch.setattr(
        cc, "_load_library_items",
        lambda db, ws: [{"id": "d1", "content": "Relatório técnico."}],
    )
    cc.ensure_company_chunks("ws-1", THIN_PROFILE, conn=conn, db=object())
    origins = {r[0] for r in conn.rows}
    assert origins == {"profile", "library_doc"}
