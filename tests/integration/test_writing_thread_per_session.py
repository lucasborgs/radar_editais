"""Item 3 — thread-por-sessão da escrita (TASK 4), gated em DATABASE_URL.

Valida, contra o AsyncPostgresSaver REAL, os mecanismos novos da promoção da
escrita a thread-por-sessão:
  • idempotência do prefixo/system (id determinístico → add_messages substitui
    em posição, 1 cópia sempre-fresca; nunca acumula);
  • delta por turno (prior_n_msgs vindo do checkpointer via
    get_thread_message_count) — usage/trace do turno N não dobra a conversa;
  • trim de paridade na fronteira (poda episódico mantendo os ids estáveis + a
    janela, com sufixo VÁLIDO começando em HumanMessage);
  • interrupt/resume converge no MESMO thread {ws}:{session} sem regredir.

Pula sem DATABASE_URL (CI sem DB). Local: rodar scripts/setup_checkpointer.py
antes. Modelo scriptado → zero token/rede; só o checkpointer toca o Postgres.
"""
from __future__ import annotations

import os
import uuid

import pytest

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
from langgraph.types import interrupt  # noqa: E402
from pydantic import PrivateAttr  # noqa: E402

import core.llm.agent_graph as ag  # noqa: E402

# O produtor usa WR_PREFIX_MSG_ID de writing_session; aqui replicamos a constante
# (o teste é do MECANISMO do grafo, não do produtor).
WR_PREFIX_ID = "wr:stable-prefix"


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
def ask_cnpj() -> str:
    """Pede o CNPJ ao usuário (pausa via interrupt)."""
    return f"O usuário respondeu: {interrupt({'field': 'cnpj', 'prompt': 'Qual o CNPJ?'})}"


def _delete_threads(prefix: str) -> None:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c, c.cursor() as cur:
        for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            cur.execute(f"delete from agent_memory.{t} where thread_id like %s", (prefix + "%",))


def _read_thread_messages(thread_id: str) -> list:
    ckpt = ag._get_writing_checkpointer()
    tup = ag._run_on_bg_loop(ckpt.aget_tuple({"configurable": {"thread_id": thread_id}}))
    if tup is None:
        return []
    return tup.checkpoint.get("channel_values", {}).get("messages", [])


@pytest.fixture(scope="module", autouse=True)
def _teardown_runtime():
    yield
    ag.shutdown_writing_runtime()


@pytest.fixture
def real_checkpointer():
    ckpt = ag._get_writing_checkpointer()
    assert ckpt is not None, "checkpointer não inicializou — rodou setup_checkpointer.py?"
    assert type(ckpt).__name__ == "AsyncPostgresSaver", (
        f"esperava AsyncPostgresSaver, veio {type(ckpt).__name__}"
    )
    return ckpt


def _fresh_turn(*, system: str, prefix: str, user: str, thread_id: str) -> object:
    """Espelha o que o produtor faz num turno FRESCO em thread-por-sessão:
    lê prior_n_msgs do checkpointer, manda system(id) + prefixo(id) + msg atual,
    sem re-seedar histórico."""
    prior = ag.get_thread_message_count(thread_id)
    return ag.run_writing_turn(
        system=system,
        initial_messages=[
            {"role": "user", "content": prefix, "id": WR_PREFIX_ID},
            {"role": "user", "content": user},
        ],
        tools=[], model="m", provider="anthropic", max_steps=5,
        thread_id=thread_id, prior_n_msgs=prior, mode="writing",
    )


def test_prefix_and_system_idempotent_single_copy(monkeypatch, real_checkpointer):
    """Após 3 turnos frescos na mesma thread: EXATAMENTE 1 system e 1 prefixo
    persistidos, ambos refletindo o ÚLTIMO turno (não acumulam cópias stale)."""
    shared = ScriptedChatModel(responses=[_ai("r1"), _ai("r2"), _ai("r3")])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)

    prefix_key = f"wsTPS_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix_key}:sess"
    try:
        _fresh_turn(system="SYS v1", prefix="PREFIX v1", user="msg1", thread_id=tid)
        _fresh_turn(system="SYS v2", prefix="PREFIX v2", user="msg2", thread_id=tid)
        _fresh_turn(system="SYS v3", prefix="PREFIX v3", user="msg3", thread_id=tid)

        msgs = _read_thread_messages(tid)
        systems = [m for m in msgs if isinstance(m, SystemMessage)]
        prefixes = [m for m in msgs if m.id == WR_PREFIX_ID]
        assert len(systems) == 1, f"esperava 1 system, veio {len(systems)}"
        assert len(prefixes) == 1, f"esperava 1 prefixo, veio {len(prefixes)}"
        # Sempre fresco: reflete o último turno.
        assert "v3" in _text(systems[0].content)
        assert "v3" in _text(prefixes[0].content)
        # As 3 mensagens do usuário acumularam (histórico vivo, sem re-seed).
        humans = [m for m in msgs if isinstance(m, HumanMessage) and m.id != WR_PREFIX_ID]
        user_texts = [_text(m.content) for m in humans]
        assert any("msg1" in t for t in user_texts)
        assert any("msg3" in t for t in user_texts)
    finally:
        _delete_threads(prefix_key)


def test_delta_usage_does_not_double(monkeypatch, real_checkpointer):
    """O usage do turno N cobre só o turno N (não a conversa toda) — prior_n_msgs
    vindo do checkpointer fatia o delta corretamente."""
    shared = ScriptedChatModel(responses=[_ai("r1"), _ai("r2")])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)

    prefix_key = f"wsTPS_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix_key}:sess"
    try:
        out1 = _fresh_turn(system="s", prefix="p", user="m1", thread_id=tid)
        out2 = _fresh_turn(system="s", prefix="p", user="m2", thread_id=tid)
        # Cada turno teve 1 chamada LLM (10/5). Se o delta dobrasse, o turno 2
        # somaria também o AI do turno 1.
        assert out1.result.usage == {"input_tokens": 10, "output_tokens": 5}
        assert out2.result.usage == {"input_tokens": 10, "output_tokens": 5}
        # E o turno 2 traduziu só o próprio delta (1 passo de resposta, não 2).
        assert sum(1 for s in out2.result.steps if s.kind == "assistant") <= 1
    finally:
        _delete_threads(prefix_key)


def test_trim_keeps_stable_ids_and_valid_window(monkeypatch, real_checkpointer):
    """trim_thread_history poda o episódico antigo mantendo system+prefixo e uma
    janela que começa em HumanMessage (sufixo válido)."""
    shared = ScriptedChatModel(responses=[_ai(f"r{i}") for i in range(6)])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)

    prefix_key = f"wsTPS_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix_key}:sess"
    try:
        for i in range(6):
            _fresh_turn(system="s", prefix="p", user=f"m{i}", thread_id=tid)

        before = _read_thread_messages(tid)
        removed = ag.trim_thread_history(
            tid, keep_human_turns=2, keep_ids=("wr:system", WR_PREFIX_ID),
        )
        assert removed > 0, "esperava poda com 6 turnos e janela=2"
        after = _read_thread_messages(tid)
        assert len(after) < len(before)

        # system + prefixo preservados (1 cada).
        assert sum(1 for m in after if isinstance(m, SystemMessage)) == 1
        assert sum(1 for m in after if m.id == WR_PREFIX_ID) == 1
        # O primeiro episódico (após os ids estáveis) é uma HumanMessage — janela
        # válida (nenhum ToolMessage órfão no início).
        episodic = [m for m in after if m.id not in ("wr:system", WR_PREFIX_ID)
                    and not isinstance(m, SystemMessage)]
        assert episodic, "episódico não pode ficar vazio"
        assert isinstance(episodic[0], HumanMessage)
        # Janela = 2 turnos humanos mantidos.
        assert sum(1 for m in episodic if isinstance(m, HumanMessage)) == 2
    finally:
        _delete_threads(prefix_key)


def test_interrupt_resume_same_session_thread(monkeypatch, real_checkpointer):
    """interrupt/resume convergem no MESMO thread {ws}:{session}; o resume fecha
    e conta só o delta pós-resume (não regride)."""
    shared = ScriptedChatModel(responses=[
        _ai("vou pedir", [{"id": "t1", "name": "ask_cnpj", "args": {}}]),
        _ai("CNPJ ok. Pronto."),
    ])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)

    prefix_key = f"wsTPS_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix_key}:sess"  # thread da SESSÃO (sem :turn)
    try:
        prior = ag.get_thread_message_count(tid)
        first = ag.run_writing_turn(
            system="s", initial_messages=[
                {"role": "user", "content": "p", "id": WR_PREFIX_ID},
                {"role": "user", "content": "escreva"},
            ],
            tools=[ask_cnpj], model="m", provider="anthropic", max_steps=5,
            thread_id=tid, prior_n_msgs=prior, mode="writing",
        )
        assert first.interrupt == {"field": "cnpj", "prompt": "Qual o CNPJ?"}

        prior2 = ag.get_thread_message_count(tid)  # do checkpointer, não de n_msgs
        second = ag.run_writing_turn(
            system="s", initial_messages=[], tools=[ask_cnpj], model="m",
            provider="anthropic", max_steps=5, thread_id=tid,
            resume="12.345.678/0001-90", prior_n_msgs=prior2, mode="writing",
        )
        assert second.interrupt is None
        assert second.result.final_text == "CNPJ ok. Pronto."
        assert second.result.usage == {"input_tokens": 10, "output_tokens": 5}
    finally:
        _delete_threads(prefix_key)


def test_resume_skips_trim_window_exceeded_still_closes(monkeypatch, real_checkpointer):
    """Cruzamento trim×interrupt (regressão da revisão T4): janela excedida +
    interrupt + resume → FECHA. O produtor NÃO poda no resume — podar uma thread
    PAUSADA num interrupt via update_state descarta o estado pendente e quebra o
    Command(resume) (provado: o resume volta com final_text vazio). A poda espera
    o próximo turno fresco. Este teste espelha a regra do `_turn_agent`."""
    shared = ScriptedChatModel(responses=[
        _ai("r0"), _ai("r1"), _ai("r2"),
        _ai("vou pedir", [{"id": "t1", "name": "ask_cnpj", "args": {}}]),
        _ai("CNPJ ok. Pronto."),
    ])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)
    keep_ids = ("wr:system", WR_PREFIX_ID)
    win = 2

    def producer_turn(tid, user, tools, resume=None):
        # Regra do _turn_agent: trim SÓ em turno fresco (resume=None).
        if resume is None:
            ag.trim_thread_history(tid, keep_human_turns=win, keep_ids=keep_ids)
        prior = ag.get_thread_message_count(tid)
        im = [] if resume is not None else [
            {"role": "user", "content": "p", "id": WR_PREFIX_ID},
            {"role": "user", "content": user},
        ]
        return ag.run_writing_turn(
            system="s", initial_messages=im, tools=tools, model="m",
            provider="anthropic", max_steps=5, thread_id=tid,
            resume=resume, prior_n_msgs=prior, mode="writing",
        )

    prefix_key = f"wsTPS_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix_key}:sess"
    try:
        for i in range(3):
            producer_turn(tid, f"m{i}", tools=[])
        first = producer_turn(tid, "escreva", tools=[ask_cnpj])
        assert first.interrupt == {"field": "cnpj", "prompt": "Qual o CNPJ?"}
        # Thread pausada EXCEDE a janela (>2 humanos) — se o resume podasse (bug),
        # o Command(resume) quebraria; o fix garante que não poda.
        humans = [
            m for m in _read_thread_messages(tid)
            if isinstance(m, HumanMessage) and m.id != WR_PREFIX_ID
        ]
        assert len(humans) > win, f"esperava thread > janela, veio {len(humans)}"
        # Resume NÃO poda → fecha limpo.
        second = producer_turn(tid, "resp", tools=[ask_cnpj], resume="12.345.678/0001-90")
        assert second.interrupt is None
        assert second.result.final_text == "CNPJ ok. Pronto."
    finally:
        _delete_threads(prefix_key)


def test_cross_workspace_isolation_session_thread(monkeypatch, real_checkpointer):
    """Leak-test ESTENDIDO (B2′) — a convenção NOVA `{ws}:{session}` (sem :turn)
    preserva o namespacing por workspace: o estado de A é invisível pelo thread_id
    de B contra o Postgres real (mesmo `sess`, workspace diferente)."""
    shared = ScriptedChatModel(responses=[_ai("r")])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)

    prefix_key = f"wsTPS_{uuid.uuid4().hex[:8]}"
    tid_a = f"{prefix_key}_A:sess"   # convenção nova: {ws}:{session}
    tid_b = f"{prefix_key}_B:sess"   # mesmo session, workspace diferente
    try:
        _fresh_turn(system="s", prefix="p", user="segredo de A", thread_id=tid_a)

        state_b = ag._run_on_bg_loop(
            real_checkpointer.aget_tuple({"configurable": {"thread_id": tid_b}})
        )
        state_a = ag._run_on_bg_loop(
            real_checkpointer.aget_tuple({"configurable": {"thread_id": tid_a}})
        )
        assert state_b is None, "VAZAMENTO: workspace B leu o checkpoint de A"
        assert state_a is not None
    finally:
        _delete_threads(prefix_key)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)
