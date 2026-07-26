# Plano de Implementação — Design Conversacional

4 sprints, ordenadas por impacto e dependências.

---

## Sprint 1 — Fundação de agenciamento visível

**Objetivo**: O usuário vê o agente trabalhando em tempo real e pode interrompê-lo.

### C1 — Stream do writing workspace

| | |
|---|---|
| **Endpoint** | `POST /writing/turn/stream` (SSE) |
| **Eventos** | `token` (text delta), `tool_start`/`tool_end` (tool calls), `compliance_flags`, `sections_done`, `done` (payload final), `error` |
| **Backend** | `WritingSession._turn_agent_streaming()` — async generator que roda o grafo com checkpointer via bg-loop e produz eventos. Pós-processamento (persistência, tool_trace) executa após o `done` do grafo |
| **Frontend** | `WorkspaceChat.tsx` consome SSE — estado `streamingText`, callbacks `onToken`/`onTool`/`onDone`/`onError`. Mesmo padrão de `page.tsx` |
| **Fallback** | `gotAnyFrame` → fallback silencioso para batch `POST /writing/turn` |

**Arquivos**: `routers/writing.py`, `writing_session.py`, `agent_graph.py`, `WorkspaceChat.tsx`, `api.ts`

### C2 — Tool trace visível

| | |
|---|---|
| **Backend** | Eventos `tool_start`/`tool_end` no SSE com `{name, input_summary, output_summary, duration_ms}` |
| **Frontend** | Componente `AgentTrace` — painel colapsável na bolha do agente. Reutilizável entre front door e workspace |
| **Dados** | `tool_trace` já existe no backend; falta serializar no SSE |

**Arquivos**: `agent_graph.py` (eventos no streaming), `AgentTrace.tsx` (novo), `WorkspaceChat.tsx`, `page.tsx`

### C3 — Stop generation

| | |
|---|---|
| **Backend** | Endpoint `POST /writing/{id}/cancel` + registry de tasks in-flight. `asyncio.Event` como cancel token |
| **Frontend** | Botão "■ Parar" durante streaming/working state. Desabilita input, aborta a stream |
| **Thread safety** | Cancel token no escopo da request |

**Arquivos**: `routers/writing.py`, `writing_session.py`, `WorkspaceChat.tsx`

---

## Sprint 2 — Confiança e robustez

**Objetivo**: Usuário confia no que o agente escreve e não perde trabalho em erros.

### H1 — Citations no writing

| | |
|---|---|
| **Backend** | `assistant_message` com markers `[1]` + `citations[]` no payload |
| **Frontend** | Marker clicável → expande snippet do chunk. Componentes `InlineCitation` + `CitationPanel` |

**Arquivos**: `writing_session.py`, `WorkspaceChat.tsx`, `InlineCitation.tsx` (novo)

### H2 — Retry mechanism

| | |
|---|---|
| **Backend** | Idempotency key `X-Turn-Idempotency-Key` |
| **Frontend** | Botão "↻ Tentar novamente" no estado de erro. Debounce 2s no SSE reconnect |

**Arquivos**: `routers/writing.py`, `writing_session.py`, `WorkspaceChat.tsx`

---

## Sprint M — Memória e Continuidade

**Objetivo**: Agente anota decisões, usuário vê o que o sistema lembra, perfil não se perde.

### M1 — Working Memory

| | |
|---|---|
| **Backend** | Expor `write_note`/`read_note` do `Scratchpad` ao WritingSession e ExploreAgent. Instrução no system prompt |
| **Frontend** | Painel colapsável "📋 Notas" (reusa `AgentTrace` do Sprint 1) |

**Arquivos**: `writing_tools.py`, `explore_tools.py`, `WRITER_SYSTEM`, `EXPLORE_SYSTEM`, `WorkspaceChat.tsx`

### M2 — Visibilidade da memória

| | |
|---|---|
| **Backend** | Endpoint `GET /me/memory` |
| **Frontend** | Painel "🧠 O Radar lembra" com insights ativos, exploration_log, última alteração de perfil |

**Arquivos**: `routers/profile.py` (ou novo), `reflection_service.py` (leitura já existe), componente frontend novo

### M4 — Micro-compressão de tool results

| | |
|---|---|
| **Backend** | Tool results >5000 chars → LLM summarizer. Fallback para truncamento. Só para `search_edital` e `read_full_proposal` |

**Arquivos**: `agent_runtime.py` (pós-processamento)

### M3 — Histórico de perfil

| | |
|---|---|
| **Backend** | Tabela `profile_version_history`. Trigger before-update em `workspaces.profile`. Máx 10 versões |
| **Frontend** | Badge "perfil atualizado · diff" |

**Arquivos**: migration 04x, `common.py`, página de perfil

---

## Sprint 3 — Refinamento

**Objetivo**: Polimento da experiência.

| Item | Descrição |
|---|---|
| Budget warning | Evento `budget_warning` no SSE. Indicador "Passo 6/10" na tool trace |
| Modo UX | Substituir mode badge por seletor de intenção "Perguntar" vs "Escrever" |
| Plan recallable | Endpoint `GET /writing/{id}/plan`. Botão "📋 Ver plano" na sidebar |

---

## Sprint 1 — Plano Detalhado

### C1 — Stream do writing workspace

#### Backend: `agent_graph.py`

Criar `run_writing_turn_streaming` — sync generator que roda no bg loop e produz `StreamDelta`:

```python
def run_writing_turn_streaming(
    *,
    system: str,
    initial_messages: list[dict],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    thread_id: str,
    resume: Any | None = None,
    prior_n_msgs: int = 0,
    mode: str | None = None,
) -> Iterator[StreamDelta]:
    """Sync wrapper que itera run_agent_graph_streaming no bg-loop."""
    checkpointer = _get_writing_checkpointer()
    gen = _writing_turn_streaming_async(
        system=system, initial_messages=initial_messages,
        tools=tools, model=model, provider=provider,
        max_steps=max_steps, thread_id=thread_id,
        checkpointer=checkpointer, resume=resume,
        prior_n_msgs=prior_n_msgs, mode=mode,
    )
    # Iterate async generator on bg loop, yield each event
    it = gen.__aiter__()
    while True:
        try:
            event = asyncio.run_coroutine_threadsafe(
                it.__anext__(), _get_bg_loop()
            ).result()
            yield event
        except StopAsyncIteration:
            break
```

Criar `_writing_turn_streaming_async` — async generator que espelha `_writing_turn_async` mas usa `run_agent_graph_streaming` em vez de `ainvoke`:

```python
async def _writing_turn_streaming_async(
    *,
    system: str,
    initial_messages: list[dict],
    tools: list[BaseTool],
    model: str,
    provider: Provider,
    max_steps: int,
    thread_id: str,
    checkpointer,
    resume: Any | None = None,
    prior_n_msgs: int = 0,
    mode: str | None = None,
) -> AsyncIterator[StreamDelta]:
    """Async generator: events token/tool_end do streaming + done com WritingTurnOutcome."""
    graph = _build_graph(chat, tools, max_steps=max_steps, checkpointer=checkpointer)
    
    if resume is not None:
        # Resume path: Command(resume=resume)
        init = Command(resume=resume)
    else:
        init = AgentState(...)
    
    final_state = None
    async for event in run_agent_graph_streaming(...):
        yield event  # relay token/tool_end/done
        if event.kind == "done" and event.result:
            final_state = event.result
    
    # Build WritingTurnOutcome from final_state (same as _writing_turn_async)
    outcome = _build_outcome_from_state(...)
    yield StreamDelta(kind="done", outcome=outcome)
```

#### Backend: `writing_session.py`

Criar `_turn_agent_streaming` — async generator version:

```python
async def _turn_agent_streaming(
    self,
    user_message: str,
    section_hint: str | None,
    user_turn_index: int,
    resume_ctx: dict | None = None,
    max_steps: int | None = None,
) -> AsyncIterator[dict]:
    """Like _turn_agent, but yields SSE events during agent run + final dict."""
    # Setup: tools, provider, model, thread_id, messages (same as _turn_agent)
    # ...
    # Run streaming
    for event in run_writing_turn_streaming(...):
        if event.kind in ("token", "tool_end"):
            yield {"kind": event.kind, "text": event.text, "name": event.name}
        elif event.kind == "done":
            # Post-process: extract tool_trace, persist turn
            result = self._post_process_turn(outcome, user_message, ...)
            yield {"kind": "final", **result}
```

#### Backend: `routers/writing.py`

Criar endpoint:

```python
@router.post("/writing/turn/stream")
async def writing_turn_stream(
    req: WritingTurnRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    session = _build_session(req, db, user_id)
    
    async def gen():
        try:
            got_agent_events = False
            async for event in session._turn_agent_streaming(req.user_message, req.section_hint):
                got_agent_events = True
                if event["kind"] == "token":
                    yield _sse("token", {"text": event["text"]})
                elif event["kind"] == "tool_end":
                    yield _sse("tool", {"name": event["name"], "phase": "end"})
                elif event["kind"] == "final":
                    yield _sse("done", event)
        except Exception as e:
            if not got_agent_events:
                # Fallback to batch
                result = await asyncio.to_thread(session.turn, req.user_message, req.section_hint)
                yield _sse("done", result)
            else:
                yield _sse("error", {"message": str(e)})
    
    return StreamingResponse(gen(), media_type="text/event-stream", ...)
```

#### Frontend: `WorkspaceChat.tsx`

Adicionar consumo de SSE:

```typescript
// Props adicionais
streamingText: string | null;
agentTrace: ToolTraceEntry[];

// Durante streaming, mostrar texto incremental + tool trace
{streamingText !== null && (
  <ChatBubble role="assistant">
    <pre className="...">{streamingText}</pre>
    {agentTrace.length > 0 && <AgentTrace steps={agentTrace} />}
  </ChatBubble>
)}
```

#### Frontend: `api.ts`

Criar `writingTurnStream` function (mesmo padrão de `exploreStream`):

```typescript
export async function writingTurnStream(
  sessionId: string,
  message: string,
  callbacks: {
    onToken: (delta: string) => void;
    onTool: (name: string) => void;
    onDone: (payload: WritingTurnResponse) => void;
    onError: (msg: string) => void;
  },
  signal?: AbortSignal,
) { ... }
```

### C2 — Tool trace visível

#### Frontend: `AgentTrace.tsx` (novo componente)

```typescript
interface TraceStep {
  name: string;
  input_summary: string;
  output_summary: string;
  duration_ms: number;
}

export function AgentTrace({ steps }: { steps: TraceStep[] }) {
  // Collapsible panel showing tool calls with expandable details
  // Icon per tool type (search, save, read, etc.)
  // Duration bar
}
```

### C3 — Stop generation

#### Backend: registry de tasks

```python
# In writing_session.py ou um registry module
_in_flight_tasks: dict[str, asyncio.Event] = {}

def cancel_turn(session_id: str) -> bool:
    event = _in_flight_tasks.get(session_id)
    if event:
        event.set()
        return True
    return False
```

#### Frontend: botão de parar

```typescript
// No WorkspaceChat, durante working/streaming:
{working && (
  <div className="flex items-center gap-2">
    <TypingIndicator />
    <span>agente trabalhando…</span>
    <button onClick={onStop} className="...">■ Parar</button>
  </div>
)}
```
