"""Regressões do M0: contrato do perfil e fronteira do Explore."""

from __future__ import annotations

import pytest

from radar.api.auth_routes import ProfilePayload, update_profile
from radar.api.common import profile_from_workspace, to_py_profile
from radar.api.routers.explore import _history_without_current
from radar.core.llm.agent_graph import StreamDelta
from radar.core.llm.agent_runtime import AgentResult
from radar.core.services.explore_agent import ExploreAgent
from radar.domain.profile_schema import PROFILE_FIELD_NAMES

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, result):
        self.db = db
        self.result = result

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        return self

    def update(self, payload):
        self.db.profile = payload["profile"]
        self.result = [{"id": "ws-1", "profile": self.db.profile}]
        return self

    def execute(self):
        return _Result(self.result)


class _Db:
    def __init__(self, profile=None):
        self.profile = profile or {}

    def table(self, _name):
        row = {"id": "ws-1", "profile": self.profile}
        return _Query(self, row)


def test_profile_http_round_trip_preserves_every_supported_field():
    payload = {
        "nome": "Acme",
        "cnpj": "123",
        "url_site": "https://acme.example",
        "tipo_entidade": "startup",
        "one_liner": "Uma linha",
        "solution_summary": "Solução",
        "descricao_atividades": "Atividades",
        "portfolio_projetos": "Projetos",
        "estilo_escrita": "Direto",
        "tamanho_empresa": "EPP",
        "capital_social": 10.5,
        "uf": "SP",
        "faturamento_anual": 100.0,
        "ano_fundacao": 2020,
        "equipe_resumo": "Equipe",
        "trl": 7,
        "tipos_financiamento_interesse": ["capital_risco"],
        "estagio": "seed",
        "mrr_arr": 2000.0,
        "round_alvo_brl": 500000.0,
        "cap_table_resumo": "Fundadores",
        "tracao_resumo": "10 clientes",
    }
    db = _Db({"data_constituicao": "2020-01-01", "legacy": "keep"})
    saved = update_profile(ProfilePayload.model_validate(payload), "user-1", db)
    stored = saved["profile"]

    assert {key: stored[key] for key in PROFILE_FIELD_NAMES} == payload
    assert stored["data_constituicao"] == "2020-01-01"
    domain = profile_from_workspace(db, "ws-1")
    assert to_py_profile(ProfilePayload.model_validate(payload)) == domain


def test_profile_contract_rejects_unknown_fields_and_has_independent_lists():
    with pytest.raises(ValueError):
        ProfilePayload.model_validate({"nao_contratado": "x"})
    assert ProfilePayload().tipos_financiamento_interesse is not ProfilePayload().tipos_financiamento_interesse


def test_explore_history_contains_only_previous_messages_and_defends_legacy_clients():
    history = [
        {"role": "user", "content": "primeira"},
        {"role": "assistant", "content": "resposta"},
        {"role": "user", "content": "atual"},
    ]
    assert _history_without_current(history, "atual") == history[:-1]
    assert _history_without_current(history[:-1], "atual") == history[:-1]


def test_exploration_log_tools_are_auth_scoped(monkeypatch):
    captured = []

    def fake_run_agent(**kwargs):
        captured.append(kwargs["tools"])
        return AgentResult(final_text="ok", steps=[], stop_reason="end_turn", usage={})

    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run_agent)
    svc = ExploreAgent()
    svc._explore_agent("pergunta", [], None, None, None, workspace_id="ws-1", db=object())
    assert "log_exploration_decision" in {tool.name for tool in captured[-1]}

    svc._explore_agent("pergunta", [], None, None, None)
    assert "log_exploration_decision" not in {tool.name for tool in captured[-1]}


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_count, should_seed", [(0, True), (3, False)])
async def test_explore_thread_seeds_only_empty_checkpoint(monkeypatch, stored_count, should_seed):
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield StreamDelta(
            kind="done",
            result=AgentResult(final_text="ok", steps=[], stop_reason="end_turn", usage={}),
        )

    async def fake_saver():
        return object()
    async def fake_trim(*_args, **_kwargs):
        return None
    monkeypatch.setattr("radar.core.llm.agent_graph.get_explore_checkpointer", fake_saver)
    monkeypatch.setattr("radar.core.llm.agent_graph.atrim_thread_history", fake_trim)
    async def fake_count(*_args, **_kwargs):
        return stored_count
    monkeypatch.setattr("radar.core.llm.agent_graph.aget_thread_message_count", fake_count)
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent_streaming_async", fake_stream)
    monkeypatch.setattr(ExploreAgent, "_explore_tools", lambda self: [])

    async for _event in ExploreAgent().explore_stream(
        "segunda",
        [{"role": "user", "content": "primeira"}, {"role": "assistant", "content": "resposta"}],
        thread_id="ws-1:sess-1",
    ):
        pass

    messages = captured["initial_messages"]
    assert [m["content"] for m in messages].count("segunda") == 1
    if should_seed:
        assert [m["content"] for m in messages] == ["primeira", "resposta", "segunda"]
        assert captured["prior_n_msgs"] == 3
    else:
        assert [m["content"] for m in messages] == ["segunda"]
        assert captured["prior_n_msgs"] == 3
