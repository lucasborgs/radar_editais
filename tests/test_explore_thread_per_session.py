"""Item 3 — thread-por-sessão do EXPLORE (TASK 3), gated em DATABASE_URL.

Valida, contra o AsyncPostgresSaver LOOP-LOCAL real (pool aberto no loop do
teste, não no bg-loop da escrita), os mecanismos da promoção do explore:
  • saver SINGLETON POR LOOP (emenda de governança) — N chamadas no mesmo loop
    reusam o MESMO saver; nenhum pool novo por request;
  • system idempotente (descoberta A) — id determinístico → add_messages
    substitui em posição; após turnos de rotas diferentes, 1 system refletindo
    o ÚLTIMO turno, sem acumular stale;
  • delta-slicing (descoberta B) — usage/steps e os called_* derivados de steps
    cobrem só o turno atual; um turno SEM match não herda o match de um anterior;
  • isolamento por workspace (leak-test ESTENDIDO) — `{wsA}:sess` invisível pelo
    `{wsB}:sess` contra o Postgres real;
  • subagente stateless dentro de um turno de explore com thread (sem "Lock bound
    to a different event loop").

Pula sem DATABASE_URL (CI sem DB). Modelo scriptado → zero token/rede.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("DATABASE_URL"),
        reason="checkpointer Postgres real — requer DATABASE_URL (integração gated)",
    ),
]

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from pydantic import PrivateAttr  # noqa: E402

import core.llm.agent_graph as ag  # noqa: E402

EXPLORE_SYS_ID = "explore:system"


class ScriptedChatModel(BaseChatModel):
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


@tool
def find_matching_editais() -> str:
    """stub da tool de match do explore."""
    return "matched: edital X"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def _delete_threads(prefix: str) -> None:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c, c.cursor() as cur:
        for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            cur.execute(f"delete from agent_memory.{t} where thread_id like %s", (prefix + "%",))


@pytest_asyncio.fixture
async def explore_saver():
    """Saver loop-local do explore, criado NO loop do teste. Teardown fecha o pool
    (bound a este loop) e limpa o registry por-loop."""
    saver = await ag.get_explore_checkpointer()
    assert saver is not None, "explore checkpointer não inicializou"
    assert type(saver).__name__ == "AsyncPostgresSaver", (
        f"esperava AsyncPostgresSaver loop-local, veio {type(saver).__name__}"
    )
    yield saver
    loop = asyncio.get_running_loop()
    pool = getattr(saver, "conn", None)
    if pool is not None and hasattr(pool, "close"):
        try:
            await pool.close()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass
    ag._explore_checkpointers.pop(loop, None)
    ag._explore_maint_graphs.pop(loop, None)
    ag._explore_ckpt_locks.pop(loop, None)


async def _read_thread(saver, thread_id: str) -> list:
    tup = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    if tup is None:
        return []
    return tup.checkpoint.get("channel_values", {}).get("messages", [])


async def _turn(saver, thread_id, *, system, user, tools, chat) -> object:
    """Espelha o produtor do explore em modo-thread: prior_n_msgs do checkpointer,
    system com id determinístico, só a msg nova no payload."""
    import core.llm.agent_graph as agmod

    def _fake_build(*a, **k):
        return chat

    orig = agmod._build_chat_model
    agmod._build_chat_model = _fake_build
    try:
        prior = await ag.aget_thread_message_count(saver, thread_id)
        result = None
        async for d in ag.run_agent_graph_streaming(
            system=system,
            initial_messages=[{"role": "user", "content": user}],
            tools=tools, model="m", provider="anthropic", max_steps=5,
            thread_id=thread_id, checkpointer=saver, prior_n_msgs=prior,
            system_msg_id=EXPLORE_SYS_ID, mode="explore",
        ):
            if d.kind == "done":
                result = d.result
        return result
    finally:
        agmod._build_chat_model = orig


@pytest.mark.asyncio
async def test_explore_checkpointer_singleton_per_loop(explore_saver):
    """N chamadas no MESMO loop reusam o MESMO saver (nenhum pool novo por request)."""
    s2 = await ag.get_explore_checkpointer()
    s3 = await ag.get_explore_checkpointer()
    assert s2 is explore_saver
    assert s3 is explore_saver


@pytest.mark.asyncio
async def test_explore_system_idempotent_single_copy(explore_saver):
    """3 turnos de rotas diferentes na mesma thread → 1 system, refletindo o
    ÚLTIMO turno (descoberta A); as msgs do usuário acumulam sem re-seed."""
    prefix = f"wsEXP_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix}:sess"
    chat = ScriptedChatModel(responses=[_ai("r1"), _ai("r2"), _ai("r3")])
    try:
        await _turn(explore_saver, tid, system="SYS rota-A", user="m1", tools=[], chat=chat)
        await _turn(explore_saver, tid, system="SYS rota-B", user="m2", tools=[], chat=chat)
        await _turn(explore_saver, tid, system="SYS rota-C", user="m3", tools=[], chat=chat)

        msgs = await _read_thread(explore_saver, tid)
        systems = [m for m in msgs if isinstance(m, SystemMessage)]
        assert len(systems) == 1, f"esperava 1 system, veio {len(systems)}"
        assert "rota-C" in _text(systems[0].content), "system deve refletir o último turno"
        humans = [m for m in msgs if isinstance(m, HumanMessage)]
        texts = [_text(m.content) for m in humans]
        assert any("m1" in t for t in texts) and any("m3" in t for t in texts)
    finally:
        _delete_threads(prefix)


@pytest.mark.asyncio
async def test_explore_delta_slicing_and_called_match(explore_saver):
    """Descoberta B: o turno 2 (sem match) NÃO herda usage/steps do turno 1 (que
    deu match). usage do turno 2 = só o turno 2; called_match do turno 2 = False."""
    prefix = f"wsEXP_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix}:sess"
    # Turno 1: AI chama find_matching_editais → tool → AI final. Turno 2: AI plano.
    chat = ScriptedChatModel(responses=[
        _ai("busco match", [{"id": "m1", "name": "find_matching_editais", "args": {}}]),
        _ai("achei um edital"),
        _ai("resposta conceitual sem match"),
    ])

    def _called_match(result) -> bool:
        return any(
            s.kind == "tool" and s.name in ("find_matching_editais", "find_matching_entities")
            for s in result.steps
        )
    try:
        r1 = await _turn(explore_saver, tid, system="s", user="que editais casam?",
                         tools=[find_matching_editais], chat=chat)
        r2 = await _turn(explore_saver, tid, system="s", user="me explique o TRL",
                         tools=[find_matching_editais], chat=chat)

        assert _called_match(r1) is True, "turno 1 deu match"
        assert _called_match(r2) is False, "turno 2 NÃO pode herdar o match do turno 1"
        # usage do turno 2 = só a 1 chamada LLM dele (10/5), não a conversa toda.
        assert r2.usage == {"input_tokens": 10, "output_tokens": 5}
        # e o delta do turno 2 tem 1 passo llm, nenhum tool.
        assert sum(1 for s in r2.steps if s.kind == "tool") == 0
    finally:
        _delete_threads(prefix)


@pytest.mark.asyncio
async def test_explore_cross_workspace_isolation(explore_saver):
    """Leak-test ESTENDIDO à convenção do explore: `{wsA}:sess` invisível por
    `{wsB}:sess` (mesmo session, workspace diferente)."""
    prefix = f"wsEXP_{uuid.uuid4().hex[:8]}"
    tid_a = f"{prefix}_A:sess"
    tid_b = f"{prefix}_B:sess"
    chat = ScriptedChatModel(responses=[_ai("r")])
    try:
        await _turn(explore_saver, tid_a, system="s", user="segredo de A", tools=[], chat=chat)
        state_a = await explore_saver.aget_tuple({"configurable": {"thread_id": tid_a}})
        state_b = await explore_saver.aget_tuple({"configurable": {"thread_id": tid_b}})
        assert state_a is not None
        assert state_b is None, "VAZAMENTO: workspace B leu o checkpoint de A"
    finally:
        _delete_threads(prefix)


@pytest.mark.asyncio
async def test_explore_subagent_stateless_in_thread(explore_saver, monkeypatch):
    """Um subagente (run_subagent → grafo efêmero checkpointer=False) disparado por
    uma tool DENTRO de um turno de explore com thread NÃO quebra por loop-binding e
    não vaza para a thread (só o resultado da tool volta)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    from core.llm.agent_runtime import run_subagent

    @tool
    def _noop(x: str) -> str:
        """tool interna do subagente"""
        return x

    @tool
    def deep_dive() -> str:
        """roda um subagente (espelha o deep_research do explore)."""
        res = run_subagent(
            name="research", system="pesquise", user_message="pesquise isto",
            tools=[_noop], provider="anthropic", model="sub-model", max_steps=2,
        )
        return f"pesquisa: {res.final_text}"

    parent = ScriptedChatModel(responses=[
        _ai("vou pesquisar", [{"id": "d1", "name": "deep_dive", "args": {}}]),
        _ai("pronto"),
    ])
    sub = ScriptedChatModel(responses=[_ai("evidência X")])

    def fake_build(provider, model, **k):  # noqa: ANN001
        return sub if model == "sub-model" else parent

    monkeypatch.setattr(ag, "_build_chat_model", fake_build)

    prefix = f"wsEXP_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix}:sess"
    try:
        prior = await ag.aget_thread_message_count(saver=explore_saver, thread_id=tid)
        result = None
        async for d in ag.run_agent_graph_streaming(
            system="s", initial_messages=[{"role": "user", "content": "pesquise e responda"}],
            tools=[deep_dive], model="parent-model", provider="anthropic", max_steps=5,
            thread_id=tid, checkpointer=explore_saver, prior_n_msgs=prior,
            system_msg_id=EXPLORE_SYS_ID, mode="explore",
        ):
            if d.kind == "done":
                result = d.result
        assert result is not None
        assert result.final_text == "pronto"
        tool_steps = [s for s in result.steps if s.kind == "tool"]
        assert len(tool_steps) == 1
        assert tool_steps[0].output == "pesquisa: evidência X"
        # A thread persistiu só a conversa do explore — nenhuma msg do subagente.
        msgs = await _read_thread(explore_saver, tid)
        assert not any("evidência X" in _text(getattr(m, "content", "")) for m in msgs
                       if isinstance(m, HumanMessage))
    finally:
        _delete_threads(prefix)
