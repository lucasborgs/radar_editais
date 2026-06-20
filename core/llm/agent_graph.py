"""Spike Etapa 1 — runtime ReAct sobre LangGraph (StateGraph), atrás de flag.

Prova de fundação da migração (docs/specs/langgraph-migration.md):
substitui o loop hand-rolled de `run_agent_async` por um `StateGraph` de 3 nós
(agent / tools / reflect), traduzindo o estado final de volta para o contrato
`AgentResult` — sem tocar em nenhum call site.

Dispatch: `run_agent_async` delega aqui quando `AGENT_RUNTIME=langgraph`. O
default (`legacy`) mantém 100% o loop atual.

Decisões fechadas (Etapa 1):
  • Cap de iterações = contador `llm_calls` em state (paridade exata com
    `for ... range(max_steps)`), NÃO `recursion_limit`. Este vira só backstop.
  • Reflexão = nó dedicado condicional entre `tools` e `agent` (visível no trace),
    não `pre_model_hook` (que só existe no prebuilt do qual saímos).

Fora de escopo do spike (etapas seguintes): telemetria via callback (Etapa 6),
checkpointer (Etapa 3), troca de `@tool` p/ LangChain (Etapa 2 — aqui usamos o
bridge `Tool → StructuredTool`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any
from typing_extensions import TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from core.llm.agent_runtime import (
    _MAX_RETRIES,
    _PLAN_TOOL_NAMES,
    _REFLECT_CHAR_THRESHOLD,
    _REFLECT_PROMPT,
    _TIMEOUT,
    TOOL_RESULT_CHAR_CAP,
    AgentResult,
    Provider,
    StopReason,
    TraceStep,
    _cap,
)

logger = logging.getLogger(__name__)


# =============================================================================
# State
# =============================================================================

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    llm_calls: int
    tool_rounds: int
    rounds_since_reflect: int
    chars_since_reflect: int
    reflect_pending: bool


# =============================================================================
# Model factory (seam de teste: monkeypatch _build_chat_model)
# =============================================================================

def _build_chat_model(
    provider: Provider,
    model: str,
    *,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
):
    """Constrói o ChatModel LangChain. Espelha a resolução de endpoint de
    `_openai_agent_client` (ZDR/custom OpenAI-compat)."""
    # Paridade de resiliência com o tier legado (make_client / _call_anthropic):
    # timeout + retries explícitos. Sem isto, um 429 transitório (TPM) derruba o
    # turno inteiro em vez de re-tentar com backoff.
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kw: dict[str, Any] = {
            "model": model, "max_tokens": 4096,
            "timeout": _TIMEOUT, "max_retries": _MAX_RETRIES,
        }
        if temperature is not None:
            kw["temperature"] = temperature
        return ChatAnthropic(**kw)

    from langchain_openai import ChatOpenAI

    base = openai_base_url or os.environ.get("AGENT_OPENAI_BASE_URL") or None
    key = (
        openai_api_key
        or os.environ.get("AGENT_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    kw = {"model": model, "timeout": _TIMEOUT, "max_retries": _MAX_RETRIES}
    if temperature is not None:
        kw["temperature"] = temperature
    if base:
        kw["base_url"] = base
        key = key or "not-needed"
    if key:
        kw["api_key"] = key
    return ChatOpenAI(**kw)


# =============================================================================
# Graph builder
# =============================================================================

def _tool_error_to_str(e: Exception) -> str:
    """Degradação graciosa (Etapa 2): tool que levanta vira ToolMessage-string em
    vez de quebrar o grafo. Mantém o prefixo "Erro ao executar" — o sinal que o
    nó `tools` usa para antecipar a reflexão (espelha o loop legado)."""
    return f"Erro ao executar a tool: {e}"


def _build_graph(
    model,
    lc_tools: list[BaseTool],
    *,
    max_steps: int,
    reflect_every: int | None,
    checkpointer=None,
):
    bound = model.bind_tools(lc_tools) if lc_tools else model
    tool_node = ToolNode(lc_tools, handle_tool_errors=_tool_error_to_str)

    async def agent(state: AgentState) -> dict:
        resp = await bound.ainvoke(state["messages"])
        return {"messages": [resp], "llm_calls": state["llm_calls"] + 1}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END  # fim natural do turno
        return "tools"

    async def tools(state: AgentState) -> dict:
        out = await tool_node.ainvoke(state)
        tmsgs = out["messages"]
        # Cap central (movido do bridge na Etapa 2): trunca cada tool-result acima
        # do orçamento antes de ir ao histórico. Caps por-tool (writing_tools) já
        # podem ter agido antes; este é o teto de segurança final.
        for m in tmsgs:
            m.content = _cap(
                str(m.content), TOOL_RESULT_CHAR_CAP, tool_name=getattr(m, "name", None),
            )
        chars = sum(len(str(m.content)) for m in tmsgs)
        rsr = state["rounds_since_reflect"] + 1
        csr = state["chars_since_reflect"] + chars
        # Sinais leves de reflexão dinâmica (spec 08, espelha o loop legado).
        had_error = any(
            str(m.content).startswith(("Erro:", "Erro ao executar")) for m in tmsgs
        )
        plan_changed = any(getattr(m, "name", None) in _PLAN_TOOL_NAMES for m in tmsgs)
        big_output = csr >= _REFLECT_CHAR_THRESHOLD
        hit_ceiling = bool(reflect_every) and rsr >= reflect_every
        do_reflect = bool(reflect_every) and (
            had_error or plan_changed or big_output or hit_ceiling
        )
        return {
            "messages": tmsgs,
            "tool_rounds": state["tool_rounds"] + 1,
            "rounds_since_reflect": rsr,
            "chars_since_reflect": csr,
            "reflect_pending": do_reflect,
        }

    def after_tools(state: AgentState) -> str:
        # Cap pós-tools (paridade com o legado: as tools da última rodada SÃO
        # executadas — já rodaram no nó `tools` — e só então o teto corta).
        if state["llm_calls"] >= max_steps:
            return END
        return "reflect" if state.get("reflect_pending") else "agent"

    def reflect(state: AgentState) -> dict:
        return {
            "messages": [HumanMessage(content=_REFLECT_PROMPT)],
            "rounds_since_reflect": 0,
            "chars_since_reflect": 0,
            "reflect_pending": False,
        }

    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("reflect", reflect)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", should_continue, {END: END, "tools": "tools"})
    g.add_conditional_edges("tools", after_tools, {END: END, "reflect": "reflect", "agent": "agent"})
    g.add_edge("reflect", "agent")
    return g.compile(checkpointer=checkpointer)


# =============================================================================
# Tradutor de contrato: estado final → AgentResult
# =============================================================================

def _msg_text(m: AIMessage) -> str:
    t = getattr(m, "text", None)
    if isinstance(t, str):  # langchain-core >=1: `.text` é property (str)
        return t
    if callable(t):  # compat <1: `.text()` método
        return t() or ""
    return m.content if isinstance(m.content, str) else str(m.content or "")


def _messages_to_agent_result(messages: list[AnyMessage], stop_reason: StopReason) -> AgentResult:
    steps: list[TraceStep] = []
    call_inputs: dict[str, dict] = {}
    total_in = total_out = 0
    last_text = ""

    for m in messages:
        if isinstance(m, AIMessage):
            tool_uses = [
                {"id": tc["id"], "name": tc["name"], "input": tc["args"]}
                for tc in (m.tool_calls or [])
            ]
            for tu in tool_uses:
                call_inputs[tu["id"]] = tu["input"]
            um = m.usage_metadata or {}
            usage = {
                "input_tokens": um.get("input_tokens", 0),
                "output_tokens": um.get("output_tokens", 0),
            }
            total_in += usage["input_tokens"]
            total_out += usage["output_tokens"]
            text = _msg_text(m)
            steps.append(TraceStep(kind="llm", text=text, tool_uses=tool_uses, usage=usage))
            last_text = text
        elif isinstance(m, ToolMessage):
            steps.append(
                TraceStep(
                    kind="tool",
                    name=m.name or "",
                    input=call_inputs.get(m.tool_call_id, {}),
                    output=str(m.content),
                )
            )

    return AgentResult(
        final_text=last_text,
        steps=steps,
        stop_reason=stop_reason,
        usage={"input_tokens": total_in, "output_tokens": total_out},
    )


# =============================================================================
# Entry point (delegado pela facade quando AGENT_RUNTIME=langgraph)
# =============================================================================

def _to_lc_messages(initial: list[dict[str, Any]]) -> list[AnyMessage]:
    out: list[AnyMessage] = []
    for m in initial:
        role, content = m.get("role"), m.get("content", "")
        if role == "assistant":
            out.append(AIMessage(content=content))
        elif role == "system":
            out.append(SystemMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


async def run_agent_graph_async(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    on_step: Callable[[TraceStep], None] | None = None,
    reflect_every: int | None = None,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
    trace_context: dict | None = None,
) -> AgentResult:
    """Equivalente LangGraph de `run_agent_async` (mesma assinatura/contrato).

    `tools` são tools nativas do LangChain (Etapa 2): consumidas direto pelo
    ToolNode, sem bridge.

    `trace_context` (Etapa 6): parent remoto p/ aninhar quando este run é um subagente
    rodando noutra thread (ver run_subagent → agent_run(trace_context=...))."""
    chat = _build_chat_model(
        provider, model,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
    )
    # checkpointer=False (NÃO None): este é o caminho stateless (kg_match/profile/
    # subagentes). `None` faria o LangGraph HERDAR o checkpointer do pai quando o
    # grafo roda como subgrafo — e o critic (subagente dentro de save_draft) herdaria
    # o AsyncPostgresSaver do turno de escrita via contextvar do config, tentando
    # usar o lock dele (preso ao bg-loop) a partir do loop do subagente → "Lock is
    # bound to a different event loop". `False` corta a herança: subagente nunca
    # persiste nem toca o checkpointer do pai.
    graph = _build_graph(
        chat, tools, max_steps=max_steps, reflect_every=reflect_every, checkpointer=False,
    )

    init: AgentState = {
        "messages": [SystemMessage(content=system), *_to_lc_messages(initial_messages)],
        "llm_calls": 0,
        "tool_rounds": 0,
        "rounds_since_reflect": 0,
        "chars_since_reflect": 0,
        "reflect_pending": False,
    }

    # Import lazy (evita custo de telemetria em testes que não a exercem).
    from core import telemetry

    result: AgentResult
    with telemetry.agent_run(
        name=span_name or f"agent.{provider}.{model}",
        input={"system": system, "initial_messages": initial_messages},
        metadata={
            "provider": provider, "model": model,
            "max_steps": max_steps, "runtime": "langgraph",
            "tools": [t.name for t in tools],
        },
        trace_context=trace_context,
    ) as agent_span:
        # Spans nativos (chain/llm/tool) com timing real + usage automático, aninhados
        # sob o agent_span corrente (Etapa 6). None se Langfuse off → grafo sem overhead.
        config: dict[str, Any] = {"recursion_limit": 3 * max_steps + 5}
        handler = telemetry.make_callback_handler()
        if handler is not None:
            config["callbacks"] = [handler]
        try:
            final = await graph.ainvoke(init, config=config)
        except GraphRecursionError:
            # Backstop — não deveria disparar (teto real é llm_calls). Trata como max_steps.
            logger.warning("agent_graph: recursion_limit atingido (backstop) — max_steps")
            return AgentResult(final_text="", steps=[], stop_reason="max_steps", usage={})
        except Exception as e:
            logger.error("agent_graph: grafo falhou: %s", e)
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message=str(e))
            return AgentResult(final_text="", steps=[], stop_reason="error", usage={})

        msgs = final["messages"]
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        stop: StopReason = "max_steps" if (last_ai and last_ai.tool_calls) else "end_turn"
        result = _messages_to_agent_result(msgs, stop)

        if agent_span is not None:
            agent_span.update(
                output={"final_text": result.final_text, "stop_reason": result.stop_reason},
                metadata={
                    "stop_reason": result.stop_reason,
                    "n_steps": len(result.steps),
                    "usage": result.usage,
                },
            )

    if on_step:
        for s in result.steps:
            on_step(s)
    return result


# =============================================================================
# Etapa 3 — WritingSession com checkpointer Postgres (interrupt/resume)
# =============================================================================
# A WritingSession invoca o grafo com um checkpointer durável, keyed por
# `thread_id` (= "{workspace_id}:{session_id}:{turn_index}"). Isso dá:
#   • durabilidade do estado em-voo entre requests HTTP (multi-instância);
#   • `request_user_info` como `interrupt()` nativo → human-in-the-loop com
#     retomada (`Command(resume=...)`) preservando o raciocínio do agente.
# As tabelas de domínio (writing_sessions/session_turns) seguem AUTORITATIVAS;
# o checkpoint é efêmero (escopo de turno-run). Ver docs/specs/langgraph-migration.md.

# ---------------------------------------------------------------------------
# Event loop dedicado (singleton) — isola o checkpointer async dos callers sync
# ---------------------------------------------------------------------------
# O AsyncPostgresSaver mantém um AsyncConnectionPool BOUND ao event loop onde foi
# criado. Os callers da escrita são sync (router via asyncio.to_thread, eval, CLI)
# e o shim run_agent sobe um asyncio.run por chamada — loops efêmeros que matariam
# o pool. Solução: um loop de longa duração numa thread daemon; o pool é criado
# uma vez nele e toda corrotina do checkpointer roda lá via run_coroutine_threadsafe.

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_loop_lock = threading.Lock()


def _get_bg_loop() -> asyncio.AbstractEventLoop:
    global _bg_loop
    if _bg_loop is not None:
        return _bg_loop
    with _bg_loop_lock:
        if _bg_loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever,
                name="lg-checkpointer-loop",
                daemon=True,
            ).start()
            _bg_loop = loop
    return _bg_loop


def _run_on_bg_loop(coro):
    """Roda `coro` no loop dedicado a partir de qualquer thread sync (bloqueante)."""
    return asyncio.run_coroutine_threadsafe(coro, _get_bg_loop()).result()


# ---------------------------------------------------------------------------
# Schema dedicado (Etapa 5): a maquinaria que bypassa RLS (checkpointer + Store)
# vive fora de `public` → invisível ao PostgREST do Supabase (config.toml expõe só
# public/storage/graphql_public). search_path inclui public+extensions para resolver
# o tipo `vector` (pgvector vive em `extensions` no Supabase, em `public` no PG local).
# Substitui o band-aid da migration 027 (RLS+revoke tabela-a-tabela). Ver
# supabase/migrations/028_agent_memory_schema.sql.
# ---------------------------------------------------------------------------
AGENT_MEMORY_SCHEMA = "agent_memory"
_AGENT_MEMORY_SEARCH_PATH = f"{AGENT_MEMORY_SCHEMA},public,extensions"


async def _make_agent_memory_pool(dsn: str, *, max_size: int):
    """AsyncConnectionPool com search_path no schema dedicado + garante o schema
    (CREATE SCHEMA IF NOT EXISTS — defensivo, independe da migration 028 ter rodado).
    Compartilhado pelo checkpointer (Et.3) e pelo Store da memória (Et.5)."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=1,  # < max_size do Store (2); default 4 > max_size daria erro
        max_size=max_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={_AGENT_MEMORY_SEARCH_PATH}",
        },
    )
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {AGENT_MEMORY_SCHEMA}")
    return pool


# ---------------------------------------------------------------------------
# Checkpointer singleton
# ---------------------------------------------------------------------------
_checkpointer = None
_checkpointer_ready = False
_checkpointer_lock = threading.Lock()


async def _init_checkpointer():
    """Cria o AsyncPostgresSaver (sobre DATABASE_URL) + roda setup() idempotente.

    Sem DATABASE_URL, cai para um InMemorySaver de processo (singleton): interrupt/
    resume funcionam dentro de uma instância (dev/teste), só sem durabilidade
    cross-instância. Em prod DATABASE_URL está sempre setado."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        from langgraph.checkpoint.memory import InMemorySaver
        logger.warning(
            "checkpointer: DATABASE_URL ausente — InMemorySaver (sem durabilidade "
            "cross-instância). OK em dev/teste; em prod configure DATABASE_URL.",
        )
        return InMemorySaver()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pool = await _make_agent_memory_pool(
        dsn, max_size=int(os.getenv("CHECKPOINTER_POOL_MAX", "4")),
    )
    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # idempotente — checa a própria tabela de migrations
    logger.info(
        "checkpointer: AsyncPostgresSaver pronto (schema %s)", AGENT_MEMORY_SCHEMA,
    )
    return saver


def shutdown_writing_runtime(timeout: float = 10.0) -> None:
    """Fecha o pool do checkpointer e para o loop dedicado — uso em scripts/CLI no
    exit (evita 'Task was destroyed but it is pending' do AsyncConnectionPool morto
    junto com o processo). No-op se nada foi inicializado. Idempotente."""
    # Flush de traces pendentes do Langfuse antes do exit (Etapa 6): em scripts/CLI
    # curtos o batch exporter pode não esvaziar sozinho. No-op se Langfuse off.
    try:
        from core import telemetry
        telemetry.flush()
    except Exception:  # noqa: BLE001 — teardown best-effort
        pass

    global _bg_loop, _checkpointer, _checkpointer_ready
    global _memory_store, _memory_store_ready
    loop, ckpt, store = _bg_loop, _checkpointer, _memory_store
    if loop is None:
        return

    async def _close():
        # AsyncPostgresStore roda uma task de batch em background (_task) — cancela
        # antes de parar o loop (senão cospe GeneratorExit/Event loop closed no exit).
        task = getattr(store, "_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # AsyncPostgresSaver guarda o pool em .conn; AsyncPostgresStore também.
        for obj in (ckpt, store):
            pool = getattr(obj, "conn", None)
            if pool is not None and hasattr(pool, "close"):
                try:
                    await pool.close()
                except Exception:  # noqa: BLE001 — teardown best-effort
                    pass

    try:
        asyncio.run_coroutine_threadsafe(_close(), loop).result(timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
    loop.call_soon_threadsafe(loop.stop)
    _checkpointer = None
    _checkpointer_ready = False
    _memory_store = None
    _memory_store_ready = False
    _bg_loop = None


def _get_writing_checkpointer():
    """Singleton do checkpointer da escrita, criado no loop dedicado. Nunca lança:
    falha de init degrada para None (escrita segue sem durabilidade/interrupt)."""
    global _checkpointer, _checkpointer_ready
    if _checkpointer_ready:
        return _checkpointer
    with _checkpointer_lock:
        if _checkpointer_ready:
            return _checkpointer
        try:
            _checkpointer = _run_on_bg_loop(_init_checkpointer())
        except Exception as e:
            logger.error(
                "checkpointer: init falhou (%s) — escrita segue sem checkpointer", e,
            )
            _checkpointer = None
        _checkpointer_ready = True
    return _checkpointer


# ---------------------------------------------------------------------------
# Memory Store singleton (Etapa 5) — projeção read-optimized dos reflection_insights
# ---------------------------------------------------------------------------
# PostgresStore do LangGraph sobre namespace (workspace_id, "insights"): o
# reflection_service ESPELHA put/delete (a escrita autoritativa segue em
# reflection_insights — tabela rica com supersede/audit/weight_suggestions); a
# WritingSession recupera via search semântico query-conditioned. Embeddings OS
# (core.retrieval.embedder, env-parametrizável) → zero token OpenAI quando
# EMBEDDING_BACKEND=sentence_transformers. Mesmo bg-loop do checkpointer (o pool
# async fica bound a ele). Schema dedicado agent_memory (bypassa RLS, ver acima).

MEMORY_NS_INSIGHTS = "insights"  # 2º elemento do namespace: (workspace_id, "insights")

_memory_store = None
_memory_store_ready = False
_memory_store_lock = threading.Lock()


async def _aembed_for_store(texts: list[str]) -> list[list[float]]:
    """AEmbeddingsFunc do Store. `embed_texts` é BLOQUEANTE (ST/OpenAI sync) — roda
    em thread para não travar o bg-loop (onde o pool async vive). Seam de teste:
    monkeypatch este símbolo para um embed fake (zero token)."""
    from core.retrieval.embedder import embed_texts
    return await asyncio.to_thread(embed_texts, texts)


async def _init_memory_store():
    """Cria o AsyncPostgresStore (schema dedicado) + setup() idempotente, com index
    semântico sobre o campo `insight` (embeddings OS).

    Sem DATABASE_URL retorna None — NÃO há fallback InMemoryStore. Diferente do
    checkpointer (cujo interrupt/resume precisa funcionar em dev sem DB), a memória
    semântica é uma camada de ENRIQUECIMENTO com fallback gracioso para o bloco
    estático (load_active_insights). Um InMemoryStore aqui embedaria a query a cada
    turno via OpenAI (premissa MVP: não queimar tokens) e seria efêmero/inútil. Os
    testes injetam um InMemoryStore + embed fake explicitamente (como o checkpointer
    injeta InMemorySaver)."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.info(
            "memory_store: DATABASE_URL ausente — memória semântica OFF "
            "(WritingSession usa o bloco estático de insights).",
        )
        return None

    from core.retrieval.embedder import EMBEDDING_DIMENSIONS
    from langgraph.store.postgres.aio import AsyncPostgresStore

    # Lê os dims do ENV em call-time (não a constante import-time do embedder): a
    # coluna pgvector fixa os dims no 1º setup() e precisa casar com o que `embed_texts`
    # produz. Se o módulo embedder foi importado antes do .env carregar, a constante
    # ficaria defasada (ex.: 1536 default vs 768 do modelo OS configurado).
    dims = int(os.environ.get("EMBEDDING_DIMENSIONS", EMBEDDING_DIMENSIONS))
    index = {"dims": dims, "embed": _aembed_for_store, "fields": ["insight"]}
    pool = await _make_agent_memory_pool(
        dsn, max_size=int(os.getenv("MEMORY_STORE_POOL_MAX", "2")),
    )
    store = AsyncPostgresStore(pool, index=index)
    await store.setup()  # idempotente — tabelas store/store_vectors/store_migrations
    logger.info(
        "memory_store: AsyncPostgresStore pronto (schema %s, dims=%d)",
        AGENT_MEMORY_SCHEMA, dims,
    )
    return store


def _get_memory_store():
    """Singleton do Store da memória (bg-loop). Nunca lança: falha de init degrada
    para None → a WritingSession cai no bloco estático (load_active_insights)."""
    global _memory_store, _memory_store_ready
    if _memory_store_ready:
        return _memory_store
    with _memory_store_lock:
        if _memory_store_ready:
            return _memory_store
        try:
            _memory_store = _run_on_bg_loop(_init_memory_store())
        except Exception as e:
            logger.error("memory_store: init falhou (%s) — memória semântica off", e)
            _memory_store = None
        _memory_store_ready = True
    return _memory_store


# ---------------------------------------------------------------------------
# API pública da memória (sync, bg-loop) — consumida por reflection_service /
# writing_session / tool. Todas degradam graciosamente (nunca propagam exceção):
# Store off/falho → no-op (put/delete) ou lista vazia (search).
# ---------------------------------------------------------------------------

def memory_put(workspace_id: str, key: str, insight: str, *, level: int | None = None) -> None:
    """Espelha um insight no Store (namespace por workspace). `key` = id da row em
    reflection_insights → delete idempotente no supersede/deactivate."""
    store = _get_memory_store()
    if store is None or not insight:
        return
    value = {"insight": insight}
    if level is not None:
        value["level"] = level
    try:
        _run_on_bg_loop(store.aput((workspace_id, MEMORY_NS_INSIGHTS), key, value))
    except Exception as e:  # noqa: BLE001 — projeção best-effort, autoritativo é a tabela
        logger.warning("memory_put falhou (ws=%s key=%s): %s", workspace_id, key, e)


def memory_delete(workspace_id: str, key: str) -> None:
    """Remove um insight do Store (supersede/deactivate). Idempotente."""
    store = _get_memory_store()
    if store is None:
        return
    try:
        _run_on_bg_loop(store.adelete((workspace_id, MEMORY_NS_INSIGHTS), key))
    except Exception as e:  # noqa: BLE001
        logger.warning("memory_delete falhou (ws=%s key=%s): %s", workspace_id, key, e)


def memory_search(workspace_id: str, query: str, *, limit: int = 6) -> list[dict]:
    """Busca semântica por insights do workspace. Retorna lista de dicts
    `{"insight","level","score"}` (vazia se Store off/sem query/falha)."""
    store = _get_memory_store()
    if store is None or not (query or "").strip():
        return []
    try:
        items = _run_on_bg_loop(
            store.asearch((workspace_id, MEMORY_NS_INSIGHTS), query=query, limit=limit)
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("memory_search falhou (ws=%s): %s", workspace_id, e)
        return []
    out: list[dict] = []
    for it in items or []:
        val = it.value or {}
        out.append({
            "insight": val.get("insight", ""),
            "level": val.get("level"),
            "score": getattr(it, "score", None),
        })
    return out


# ---------------------------------------------------------------------------
# Entry point da escrita: interrupt/resume sobre o checkpointer
# ---------------------------------------------------------------------------

@dataclass
class WritingTurnOutcome:
    """Resultado de um turno-run da escrita pelo grafo com checkpointer.

    result:    AgentResult do DELTA deste turno (trace/usage), não do thread todo.
    interrupt: payload de request_user_info ({"field","prompt"}) se o grafo pausou
               num interrupt(); None se o turno completou.
    n_messages: len(messages) do estado final — fronteira para fatiar o delta do
               PRÓXIMO resume (o thread acumula pré+pós-interrupt no mesmo state)."""
    result: AgentResult
    interrupt: dict | None
    n_messages: int


async def _writing_turn_async(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider,
    max_steps: int,
    reflect_every: int | None,
    thread_id: str,
    checkpointer,
    resume: Any | None,
    prior_n_msgs: int,
    span_name: str | None,
    temperature: float | None,
    openai_base_url: str | None,
    openai_api_key: str | None,
) -> WritingTurnOutcome:
    from langgraph.types import Command

    chat = _build_chat_model(
        provider, model, temperature=temperature,
        openai_base_url=openai_base_url, openai_api_key=openai_api_key,
    )
    graph = _build_graph(
        chat, tools, max_steps=max_steps, reflect_every=reflect_every,
        checkpointer=checkpointer,
    )
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 3 * max_steps + 5,
    }

    if resume is not None:
        payload: Any = Command(resume=resume)
    else:
        payload = {
            "messages": [SystemMessage(content=system), *_to_lc_messages(initial_messages)],
            "llm_calls": 0,
            "tool_rounds": 0,
            "rounds_since_reflect": 0,
            "chars_since_reflect": 0,
            "reflect_pending": False,
        }

    from core import telemetry

    with telemetry.agent_run(
        name=span_name or f"agent.{provider}.{model}",
        input={"system": system, "resume": resume is not None},
        metadata={
            "provider": provider, "model": model, "max_steps": max_steps,
            "runtime": "langgraph", "thread_id": thread_id,
            "tools": [t.name for t in tools],
        },
    ) as agent_span:
        # Spans nativos do grafo aninhados sob o turno (Etapa 6). O critic (subagente
        # dentro de save_draft) propaga este trace via current_trace_context.
        handler = telemetry.make_callback_handler()
        if handler is not None:
            config["callbacks"] = [handler]
        try:
            final = await graph.ainvoke(payload, config=config)
        except GraphRecursionError:
            logger.warning("writing_turn: recursion_limit (backstop) — max_steps")
            return WritingTurnOutcome(
                AgentResult(final_text="", steps=[], stop_reason="max_steps", usage={}),
                None, prior_n_msgs,
            )
        except Exception as e:
            logger.error("writing_turn: grafo falhou: %s", e)
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message=str(e))
            return WritingTurnOutcome(
                AgentResult(final_text="", steps=[], stop_reason="error", usage={}),
                None, prior_n_msgs,
            )

        msgs = final["messages"]
        # Delta deste turno-run: no resume o thread acumula as mensagens do turno
        # anterior; só traduzimos as NOVAS (a partir de prior_n_msgs) para não
        # dobrar trace/usage (custo). Em turno fresco prior_n_msgs=0 (delta=tudo).
        delta = msgs[prior_n_msgs:]

        interrupts = final.get("__interrupt__")
        intr_payload: dict | None = None
        if interrupts:
            v = interrupts[0].value
            intr_payload = v if isinstance(v, dict) else {"prompt": str(v)}
            stop: StopReason = "end_turn"
        else:
            last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
            stop = "max_steps" if (last_ai and last_ai.tool_calls) else "end_turn"

        result = _messages_to_agent_result(delta, stop)

        if agent_span is not None:
            agent_span.update(
                output={"final_text": result.final_text, "interrupted": bool(intr_payload)},
                metadata={"stop_reason": result.stop_reason, "n_steps": len(result.steps),
                          "usage": result.usage},
            )

    return WritingTurnOutcome(result, intr_payload, len(msgs))


def run_writing_turn(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    reflect_every: int | None = None,
    thread_id: str,
    resume: Any | None = None,
    prior_n_msgs: int = 0,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
) -> WritingTurnOutcome:
    """Roda UM turno-run da escrita pelo grafo com checkpointer durável (sync).

    Diferente de `run_agent_graph_async` (stateless, usado pelos outros call sites),
    este persiste o estado por `thread_id` e suporta interrupt/resume:
      • `resume=None`  → turno fresco (semeia o state de `initial_messages`);
      • `resume=valor` → retoma o thread no ponto do interrupt() com `Command(resume)`.

    Roda no loop dedicado (o checkpointer async fica bound a ele). Retorna um
    `WritingTurnOutcome` (result do delta + payload de interrupt + n_messages)."""
    checkpointer = _get_writing_checkpointer()
    return _run_on_bg_loop(_writing_turn_async(
        system=system,
        initial_messages=initial_messages,
        tools=tools,
        model=model,
        provider=provider,
        max_steps=max_steps,
        reflect_every=reflect_every,
        thread_id=thread_id,
        checkpointer=checkpointer,
        resume=resume,
        prior_n_msgs=prior_n_msgs,
        span_name=span_name,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
    ))
