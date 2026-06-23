"""Etapa 3 — checkpointer + interrupt/resume do grafo de escrita (zero rede).

Exercita `run_writing_turn` com um chat model scriptado e um InMemorySaver
injetado: interrupt() dispara → pausa com payload → Command(resume) retoma no
ponto exato → texto final. Cobre também o caveat de re-execução do batch (risco
#1 do plano) e o isolamento por thread_id (namespacing multi-tenant).
"""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt
from pydantic import PrivateAttr

import core.llm.agent_graph as ag


class ScriptedChatModel(BaseChatModel):
    """Devolve `responses[i]` na i-ésima chamada (persistente entre invokes)."""
    responses: list
    _idx: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.responses[self._idx]
        self._idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


def _ai(text: str, tool_calls: list[dict] | None = None) -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=[
            {"id": tc["id"], "name": tc["name"], "args": tc["args"], "type": "tool_call"}
            for tc in (tool_calls or [])
        ],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


def _wire(monkeypatch, responses, *, checkpointer):
    """Injeta o chat model scriptado + um checkpointer fixo no run_writing_turn."""
    scripted = ScriptedChatModel(responses=responses)
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)
    monkeypatch.setattr(ag, "_get_writing_checkpointer", lambda: checkpointer)
    return scripted


@tool
def ask_cnpj() -> str:
    """Pede o CNPJ ao usuário (pausa via interrupt)."""
    answer = interrupt({"field": "cnpj", "prompt": "Qual o CNPJ?"})
    return f"O usuário respondeu (campo 'cnpj'): {answer}"


# ---------------------------------------------------------------------------
# interrupt → resume
# ---------------------------------------------------------------------------

def test_interrupt_pauses_with_payload(monkeypatch):
    saver = InMemorySaver()
    _wire(monkeypatch, [_ai("vou pedir", [{"id": "t1", "name": "ask_cnpj", "args": {}}])],
          checkpointer=saver)

    out = ag.run_writing_turn(
        system="sys", initial_messages=[{"role": "user", "content": "escreva"}],
        tools=[ask_cnpj], model="m", provider="anthropic", max_steps=5,
        thread_id="wsA:sess1:1",
    )
    assert out.interrupt == {"field": "cnpj", "prompt": "Qual o CNPJ?"}
    # n_messages = fronteira do delta para o resume (System+Human+AIMessage = 3).
    assert out.n_messages == 3


def test_resume_continues_from_interrupt(monkeypatch):
    saver = InMemorySaver()
    # call 1: pede CNPJ (interrompe). call 2 (pós-resume): texto final, sem tools.
    _wire(
        monkeypatch,
        [
            _ai("vou pedir", [{"id": "t1", "name": "ask_cnpj", "args": {}}]),
            _ai("Pronto, CNPJ registrado na proposta."),
        ],
        checkpointer=saver,
    )
    tid = "wsA:sess1:1"
    first = ag.run_writing_turn(
        system="sys", initial_messages=[{"role": "user", "content": "escreva"}],
        tools=[ask_cnpj], model="m", provider="anthropic", max_steps=5, thread_id=tid,
    )
    assert first.interrupt is not None

    second = ag.run_writing_turn(
        system="sys", initial_messages=[], tools=[ask_cnpj], model="m",
        provider="anthropic", max_steps=5, thread_id=tid,
        resume="12.345.678/0001-90", prior_n_msgs=first.n_messages,
    )
    assert second.interrupt is None
    assert second.result.final_text == "Pronto, CNPJ registrado na proposta."
    # O delta do resume NÃO recont a o turno que perguntou: só a tool resolvida +
    # o AIMessage final entram no trace deste turno.
    tool_steps = [s for s in second.result.steps if s.kind == "tool"]
    assert len(tool_steps) == 1
    assert "12.345.678/0001-90" in tool_steps[0].output


def test_resume_token_usage_is_delta_only(monkeypatch):
    """Custo: o turno de resume conta só a chamada LLM pós-resume, não a do
    turno que perguntou (senão o custo/turno dobraria)."""
    saver = InMemorySaver()
    _wire(
        monkeypatch,
        [
            _ai("vou pedir", [{"id": "t1", "name": "ask_cnpj", "args": {}}]),
            _ai("ok final"),
        ],
        checkpointer=saver,
    )
    tid = "wsA:sess1:1"
    first = ag.run_writing_turn(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[ask_cnpj], model="m", provider="anthropic", max_steps=5, thread_id=tid,
    )
    second = ag.run_writing_turn(
        system="sys", initial_messages=[], tools=[ask_cnpj], model="m",
        provider="anthropic", max_steps=5, thread_id=tid,
        resume="123", prior_n_msgs=first.n_messages,
    )
    # Cada AIMessage scriptado = 10 in / 5 out. O resume vê só 1 (a final).
    assert second.result.usage == {"input_tokens": 10, "output_tokens": 5}


# ---------------------------------------------------------------------------
# Risco #1 — re-execução do batch no resume
# ---------------------------------------------------------------------------

def test_batched_tool_reexecutes_on_resume(monkeypatch):
    """Tool batcheada com a que interrompe RE-EXECUTA ao retomar (caveat do
    LangGraph). Guarda o comportamento → justifica a guia de prompt 'chame
    request_user_info sozinha'."""
    calls: list[str] = []

    @tool
    def search_thing(query: str) -> str:
        """busca algo"""
        calls.append(query)
        return f"resultado de {query}"

    saver = InMemorySaver()
    _wire(
        monkeypatch,
        [
            _ai("busco e pergunto", [
                {"id": "s1", "name": "search_thing", "args": {"query": "prazo"}},
                {"id": "t1", "name": "ask_cnpj", "args": {}},
            ]),
            _ai("fim"),
        ],
        checkpointer=saver,
    )
    tid = "wsA:sess1:1"
    first = ag.run_writing_turn(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[search_thing, ask_cnpj], model="m", provider="anthropic",
        max_steps=5, thread_id=tid,
    )
    assert first.interrupt is not None
    assert calls == ["prazo"]  # rodou 1x antes da pausa

    ag.run_writing_turn(
        system="sys", initial_messages=[], tools=[search_thing, ask_cnpj],
        model="m", provider="anthropic", max_steps=5, thread_id=tid,
        resume="123", prior_n_msgs=first.n_messages,
    )
    assert calls == ["prazo", "prazo"]  # re-executou no resume (caveat documentado)


# ---------------------------------------------------------------------------
# Isolamento por thread_id (namespacing multi-tenant)
# ---------------------------------------------------------------------------

def test_thread_id_isolates_state(monkeypatch):
    """Um interrupt pendente no thread do workspace A não vaza para o do B —
    threads distintos não compartilham checkpoint (base do isolamento)."""
    saver = InMemorySaver()
    _wire(monkeypatch, [_ai("pede", [{"id": "t1", "name": "ask_cnpj", "args": {}}])],
          checkpointer=saver)

    ag.run_writing_turn(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[ask_cnpj], model="m", provider="anthropic", max_steps=5,
        thread_id="wsA:sess1:1",
    )
    # O estado do thread de B é vazio (nada pendente lá).
    st_b = saver.get_tuple({"configurable": {"thread_id": "wsB:sess9:1"}})
    assert st_b is None
    # O de A existe (pausado).
    st_a = saver.get_tuple({"configurable": {"thread_id": "wsA:sess1:1"}})
    assert st_a is not None


# ---------------------------------------------------------------------------
# Etapa 4 × Etapa 3 — subagente (tool) DENTRO do grafo com checkpointer
# ---------------------------------------------------------------------------

def test_subagent_inside_checkpointed_writing_turn(monkeypatch):
    """O grafo de escrita roda no loop dedicado (checkpointer); uma tool chama
    run_subagent, que roda seu PRÓPRIO grafo efêmero (run_agent → asyncio.run num
    worker thread, sem checkpointer). Prova que não há conflito de event loop
    entre o bg-loop do checkpointer e o loop do subagente — o caminho real do
    critic dentro do save_draft. Ambos os modelos são scriptados (zero token)."""
    from core.llm.agent_runtime import run_subagent

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")

    @tool
    def _noop(x: str) -> str:
        """tool interna do subagente"""
        return x

    @tool
    def review_thing() -> str:
        """roda um subagente de revisão (espelha run_critic dentro de save_draft)"""
        res = run_subagent(
            name="critic", system="revise", user_message="revise isto",
            tools=[_noop], provider="anthropic", model="critic-model", max_steps=2,
        )
        return f"revisão: {res.final_text}"

    parent = ScriptedChatModel(responses=[
        _ai("vou revisar", [{"id": "r1", "name": "review_thing", "args": {}}]),
        _ai("feito"),
    ])
    sub = ScriptedChatModel(responses=[_ai("aprovado")])

    # Roteia por model: o subagente pede "critic-model"; o pai usa o default.
    def fake_build(provider, model, **k):  # noqa: ANN001
        return sub if model == "critic-model" else parent

    monkeypatch.setattr(ag, "_build_chat_model", fake_build)
    monkeypatch.setattr(ag, "_get_writing_checkpointer", lambda: InMemorySaver())

    out = ag.run_writing_turn(
        system="sys", initial_messages=[{"role": "user", "content": "escreva e revise"}],
        tools=[review_thing], model="parent-model", provider="anthropic",
        max_steps=5, thread_id="wsA:sess1:1",
    )
    assert out.interrupt is None
    assert out.result.final_text == "feito"
    # O resultado do subagente fluiu de volta pela tool, sem erro de loop.
    tool_steps = [s for s in out.result.steps if s.kind == "tool"]
    assert len(tool_steps) == 1
    assert tool_steps[0].output == "revisão: aprovado"


def test_subagent_graph_compiles_with_checkpointer_false(monkeypatch):
    """REGRESSÃO (bug latente da Et.3, pego no gate de eval da Et.6): o caminho
    stateless (run_agent_graph_async, usado pelos subagentes) DEVE compilar com
    `checkpointer=False`, não None. Com None, o LangGraph HERDA o checkpointer do
    pai via contextvar do config quando roda como subgrafo — e o critic tentaria
    usar o AsyncPostgresSaver do turno de escrita (lock preso ao bg-loop) a partir
    do loop do subagente → 'Lock is bound to a different event loop'."""
    import asyncio

    captured: dict = {}
    real_build = ag._build_graph

    def spy_build(*args, **kwargs):
        captured["checkpointer"] = kwargs.get("checkpointer", "MISSING")
        return real_build(*args, **kwargs)

    monkeypatch.setattr(ag, "_build_graph", spy_build)
    scripted = ScriptedChatModel(responses=[_ai("ok")])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)

    asyncio.run(ag.run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[], model="m", provider="anthropic",
    ))
    assert captured["checkpointer"] is False, (
        "run_agent_graph_async deve passar checkpointer=False (não None) — senão o "
        "subagente herda o checkpointer do pai e quebra cross-loop"
    )
