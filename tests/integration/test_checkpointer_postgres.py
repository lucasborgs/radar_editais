"""Integração REAL do checkpointer Postgres (Etapa 3) — gated em DATABASE_URL.

Exercita o caminho durável de verdade (AsyncPostgresSaver sobre DATABASE_URL, não
InMemorySaver): interrupt() persiste no Postgres, Command(resume) retoma e fecha, e
— o gate de segurança crítico — o estado de um workspace NÃO é legível pelo
thread_id de outro (isolamento multi-tenant pós RLS-bypass do checkpointer).

Pula automaticamente sem DATABASE_URL (CI sem DB). Local: tem que rodar
`scripts/setup_checkpointer.py` antes (cria as tabelas). O modelo é scriptado →
zero token, zero rede; só o checkpointer toca o Postgres.
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
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.types import interrupt  # noqa: E402
from pydantic import PrivateAttr  # noqa: E402

import core.llm.agent_graph as ag  # noqa: E402


class ScriptedChatModel(BaseChatModel):
    """Instância ÚNICA por teste: `_idx` persiste entre invokes (o run fresco e o
    resume são chamadas LLM distintas → responses[0] e responses[1])."""
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
    return f"O usuário respondeu (campo 'cnpj'): {interrupt({'field': 'cnpj', 'prompt': 'Qual o CNPJ?'})}"


def _delete_threads(prefix: str) -> None:
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c, c.cursor() as cur:
        for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            # Etapa 5: tabelas do checkpointer migraram public → agent_memory.
            cur.execute(f"delete from agent_memory.{t} where thread_id like %s", (prefix + "%",))


@pytest.fixture(scope="module", autouse=True)
def _teardown_runtime():
    """Fecha pool + para o loop dedicado ao fim do módulo (exit limpo do pytest)."""
    yield
    ag.shutdown_writing_runtime()


@pytest.fixture
def real_checkpointer():
    """O AsyncPostgresSaver real (roda setup() na 1ª init). Falha explícita se o
    DSN está setado mas o checkpointer não subiu (DB inacessível / sem setup)."""
    ckpt = ag._get_writing_checkpointer()
    assert ckpt is not None, "checkpointer não inicializou — rodou setup_checkpointer.py?"
    assert type(ckpt).__name__ == "AsyncPostgresSaver", (
        f"esperava AsyncPostgresSaver, veio {type(ckpt).__name__} "
        "(DATABASE_URL setado mas caiu no fallback?)"
    )
    return ckpt


def test_interrupt_resume_durable(monkeypatch, real_checkpointer):
    """interrupt persiste no Postgres → resume retoma e FECHA; custo só do delta."""
    shared = ScriptedChatModel(responses=[
        _ai("vou pedir", [{"id": "t1", "name": "ask_cnpj", "args": {}}]),
        _ai("CNPJ registrado. Seção pronta."),
    ])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)

    prefix = f"wsITEST_{uuid.uuid4().hex[:8]}"
    tid = f"{prefix}:sess:1"
    try:
        first = ag.run_writing_turn(
            system="sys", initial_messages=[{"role": "user", "content": "escreva"}],
            tools=[ask_cnpj], model="m", provider="anthropic", max_steps=5, thread_id=tid,
        )
        assert first.interrupt == {"field": "cnpj", "prompt": "Qual o CNPJ?"}

        second = ag.run_writing_turn(
            system="sys", initial_messages=[], tools=[ask_cnpj], model="m",
            provider="anthropic", max_steps=5, thread_id=tid,
            resume="12.345.678/0001-90", prior_n_msgs=first.n_messages,
        )
        assert second.interrupt is None
        assert second.result.final_text == "CNPJ registrado. Seção pronta."
        # Resume conta só a chamada LLM pós-resume (1 AIMessage = 10/5), não dobra.
        assert second.result.usage == {"input_tokens": 10, "output_tokens": 5}
    finally:
        _delete_threads(prefix)


def test_cross_workspace_isolation_durable(monkeypatch, real_checkpointer):
    """GATE de segurança: um interrupt pendente do workspace A é INVISÍVEL pelo
    thread_id do workspace B contra o Postgres real (isolamento = namespacing)."""
    shared = ScriptedChatModel(responses=[
        _ai("vou pedir", [{"id": "t1", "name": "ask_cnpj", "args": {}}]),
    ])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: shared)

    prefix = f"wsITEST_{uuid.uuid4().hex[:8]}"
    tid_a = f"{prefix}_A:sess:1"
    tid_b = f"{prefix}_B:sess:1"  # mesmo "sess:1", workspace diferente
    try:
        first = ag.run_writing_turn(
            system="sys", initial_messages=[{"role": "user", "content": "x"}],
            tools=[ask_cnpj], model="m", provider="anthropic", max_steps=5, thread_id=tid_a,
        )
        assert first.interrupt is not None  # A pausou

        state_b = ag._run_on_bg_loop(
            real_checkpointer.aget_tuple({"configurable": {"thread_id": tid_b}})
        )
        state_a = ag._run_on_bg_loop(
            real_checkpointer.aget_tuple({"configurable": {"thread_id": tid_a}})
        )
        assert state_b is None, "VAZAMENTO: workspace B leu o checkpoint de A"
        assert state_a is not None
    finally:
        _delete_threads(prefix)
