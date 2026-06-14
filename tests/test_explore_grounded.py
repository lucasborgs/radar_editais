"""Testes da tool search_edital_trechos do explore (spec explore-grounded-comparison).

Estratégia: monkeypatch em `explore_tools.retrieve_chunks` — a tool NÃO depende
de DB/Supabase nestes testes; só verificamos a orquestração (loop por edital,
rótulos, degradação, caps). Instanciamos KGMatchService() real (lê o índice em
disco), mas a tool só usa o service para `service` ser válido; a recuperação é
mockada.

Cobre:
  (a) ids válidos → blocos rotulados por edital
  (b) loop chama retrieve_chunks 1× por id (recuperação balanceada)
  (c) lista vazia → mensagem de orientação
  (d) edital sem chunk (retrieve_chunks → []) → degradação por-edital, sem levantar
  (e) exceção de retrieve_chunks → bloco de erro, sem levantar
  (f) MAX_EDITAIS trunca
  (g) k_por_edital clamp
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.llm.agent_tools.explore_tools as explore_tools  # noqa: E402
from core.llm.agent_tools import build_explore_tools  # noqa: E402
from core.services.kg_match_service import KGMatchService  # noqa: E402


def _get_tool(svc=None):
    svc = svc or KGMatchService()
    tools = build_explore_tools(svc)
    return next(t for t in tools if t.name == "search_edital_trechos")


def _fake_chunk(eid: str, text: str = "texto literal do edital"):
    return {
        "id": f"{eid}-0",
        "edital_id": eid,
        "chunk_index": 0,
        "text": text,
        "section": "Art. 1",
        "source_file": "edital.pdf",
        "page_range": "1",
        "metadata": {},
        "score": 1.0,
    }


# ---------------------------------------------------------------------------
# Registro: a tool aparece na lista de build_explore_tools
# ---------------------------------------------------------------------------

def test_tool_is_registered():
    svc = KGMatchService()
    names = {t.name for t in build_explore_tools(svc)}
    assert "search_edital_trechos" in names


# ---------------------------------------------------------------------------
# (a) ids válidos → blocos rotulados por edital
# ---------------------------------------------------------------------------

def test_valid_ids_return_labeled_blocks(monkeypatch):
    monkeypatch.setattr(
        explore_tools, "retrieve_chunks",
        lambda db, eids, **kw: [_fake_chunk(eids[0])],
    )
    t = _get_tool()
    out = t.func(edital_ids=["finep:1", "fapesp:2"], query="contrapartida")
    assert isinstance(out, str)
    # cada edital aparece como bloco rotulado ### <id>
    assert "### finep:1" in out
    assert "### fapesp:2" in out


# ---------------------------------------------------------------------------
# (b) loop chama retrieve_chunks 1× por id
# ---------------------------------------------------------------------------

def test_loop_calls_retrieve_once_per_id(monkeypatch):
    calls: list[list[str]] = []

    def _spy(db, eids, **kw):
        calls.append(list(eids))
        return [_fake_chunk(eids[0])]

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _spy)
    t = _get_tool()
    t.func(edital_ids=["a", "b", "c"], query="trl")
    # 3 chamadas, cada uma com UM id (não a união)
    assert calls == [["a"], ["b"], ["c"]]


# ---------------------------------------------------------------------------
# (c) lista vazia → mensagem de orientação
# ---------------------------------------------------------------------------

def test_empty_ids_returns_guidance(monkeypatch):
    called = {"n": 0}

    def _spy(*a, **kw):
        called["n"] += 1
        return []

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _spy)
    t = _get_tool()
    out = t.func(edital_ids=[], query="x")
    assert "Nenhum edital_id válido" in out
    assert "list_editais" in out
    # não tentou recuperar nada
    assert called["n"] == 0


def test_only_falsy_ids_returns_guidance(monkeypatch):
    monkeypatch.setattr(explore_tools, "retrieve_chunks", lambda *a, **kw: [])
    t = _get_tool()
    out = t.func(edital_ids=["", None], query="x")
    assert "Nenhum edital_id válido" in out


# ---------------------------------------------------------------------------
# (d) edital sem chunk → degradação por-edital, sem levantar
# ---------------------------------------------------------------------------

def test_no_chunks_degrades_per_edital(monkeypatch):
    monkeypatch.setattr(explore_tools, "retrieve_chunks", lambda *a, **kw: [])
    t = _get_tool()
    out = t.func(edital_ids=["finep:1"], query="contrapartida")
    assert "### finep:1" in out
    assert "sem trecho relevante" in out


def test_mixed_chunks_and_empty(monkeypatch):
    def _spy(db, eids, **kw):
        return [_fake_chunk("a")] if eids == ["a"] else []

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _spy)
    t = _get_tool()
    out = t.func(edital_ids=["a", "b"], query="x")
    assert "### a" in out and "texto literal" in out
    assert "### b" in out and "sem trecho relevante" in out


# ---------------------------------------------------------------------------
# (e) exceção de retrieve_chunks → bloco de erro, sem levantar
# ---------------------------------------------------------------------------

def test_exception_degrades_to_error_block(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("DB indisponível")

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _boom)
    t = _get_tool()
    # chama .func direto (sem o wrapper Tool.call que também engole exceção) —
    # garante que a PRÓPRIA tool degrada, não o wrapper.
    out = t.func(edital_ids=["finep:1"], query="x")
    assert "### finep:1" in out
    assert "erro ao recuperar" in out
    assert "DB indisponível" in out


def test_exception_in_one_edital_does_not_kill_others(monkeypatch):
    def _spy(db, eids, **kw):
        if eids == ["bad"]:
            raise RuntimeError("falhou")
        return [_fake_chunk(eids[0])]

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _spy)
    t = _get_tool()
    out = t.func(edital_ids=["bad", "good"], query="x")
    assert "### bad" in out and "erro ao recuperar" in out
    assert "### good" in out and "texto literal" in out


# ---------------------------------------------------------------------------
# (f) MAX_EDITAIS trunca
# ---------------------------------------------------------------------------

def test_max_editais_truncates(monkeypatch):
    calls: list[list[str]] = []

    def _spy(db, eids, **kw):
        calls.append(list(eids))
        return [_fake_chunk(eids[0])]

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _spy)
    t = _get_tool()
    ids = [f"e{i}" for i in range(10)]  # 10 > MAX_EDITAIS (5)
    t.func(edital_ids=ids, query="x")
    assert len(calls) == explore_tools.MAX_EDITAIS == 5
    # truncou os primeiros, não os últimos
    assert calls[0] == ["e0"]
    assert calls[-1] == ["e4"]


# ---------------------------------------------------------------------------
# (g) k_por_edital clamp (1–5)
# ---------------------------------------------------------------------------

def test_k_por_edital_clamped_high(monkeypatch):
    seen_k: list[int] = []

    def _spy(db, eids, query, k, **kw):
        seen_k.append(k)
        return [_fake_chunk(eids[0])]

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _spy)
    t = _get_tool()
    t.func(edital_ids=["a"], query="x", k_por_edital=99)
    assert seen_k == [5]


def test_k_por_edital_clamped_low(monkeypatch):
    seen_k: list[int] = []

    def _spy(db, eids, query, k, **kw):
        seen_k.append(k)
        return [_fake_chunk(eids[0])]

    monkeypatch.setattr(explore_tools, "retrieve_chunks", _spy)
    t = _get_tool()
    t.func(edital_ids=["a"], query="x", k_por_edital=0)
    assert seen_k == [1]


# ---------------------------------------------------------------------------
# Cap por trecho: texto longo é truncado antes da concatenação
# ---------------------------------------------------------------------------

def test_chunk_char_cap_applied(monkeypatch):
    long_text = "x" * (explore_tools.EXPLORE_CHUNK_CHAR_CAP + 5000)
    monkeypatch.setattr(
        explore_tools, "retrieve_chunks",
        lambda db, eids, **kw: [_fake_chunk(eids[0], text=long_text)],
    )
    t = _get_tool()
    out = t.func(edital_ids=["a"], query="x")
    # o trecho cru completo não cabe; o marcador de truncamento aparece
    assert long_text not in out
    assert "truncado" in out
