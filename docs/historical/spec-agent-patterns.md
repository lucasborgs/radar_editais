# Spec — Agent patterns (write_todos, run_subagent, scratchpad)

> **Objetivo:** roubar 3 padrões do deepagents/LangGraph que agregam valor hoje, SEM migrar de runtime. Mantemos o harness próprio (`core/llm/agent_runtime.py`) e adicionamos primitivas que combatem drift em loops longos.
> **Base:** branch `main`. **Data:** 2026-06-12.
> **Enquadramento:** decisão LangGraph-tripwires — adotar framework custaria reescrita do loop, dos adapters por provider e da telemetria já consolidada. Os 3 padrões são tools/helpers isolados, encaixam no padrão de factory-com-closure existente e não tocam o shape de `AgentResult`/`TraceStep`.

## Decisões travadas

| # | Tópico | Decisão |
|---|--------|---------|
| 1 | Não migrar runtime | Padrões como tools/helpers locais. `run_agent`/`AgentResult`/`TraceStep` inalterados (só kwarg-only aditivo `span_name`) |
| 2 | `write_todos` | UMA tool, **substitui a lista inteira** (sem merge incremental, estilo deepagents). `list[dict]` solto no schema; validação no corpo → erro-string |
| 3 | `run_subagent` | Helper de runtime (fica em `agent_runtime`, não em `agent_tools/`). Formaliza o subagente-como-tool hoje hand-rolled em `deep_research.py`. Degrada para `AgentResult(stop_reason="error")` |
| 4 | Scratchpad | Em memória, por execução. Consumidor real = ProfileExtractor (crawl de até 10 páginas). DeepResearch fica fora por ora (5 steps, não precisa) |
| 5 | Wiring de `write_todos` | WritingSession (`PlanState` por turno) + ProfileExtractor (`PlanState` por extração). Persistência cross-turn dos todos é evolução futura |
| 6 | Invariante de tool | Tools NUNCA lançam pro loop — todo erro vira string. Docstring de tool é prompt |

## Arquitetura

```
agent_runtime.run_subagent(*, name, system, user_message, tools, ...)  ← helper
   └─ resolve_agent_provider + run_agent(span_name=f"subagent.{name}")  em try/except
        ⇒ exceção → AgentResult(final_text="", steps=[], stop_reason="error")

agent_tools/planning_tools.py    PlanState(todos) + build_planning_tools(state)
   └─ write_todos(todos: list[dict]) -> str  (replace integral, render ✓/▶/☐ + contagem)

agent_tools/scratchpad_tools.py  Scratchpad(notes) + build_scratchpad_tools(pad, max_notes, max_chars)
   └─ write_note(name, content) / read_note(name)  (limites → erro-string)
```

Três blocos, sem faseamento:
- **run_subagent** — refatora `deep_research.py` para usá-lo (comportamento observável idêntico: dedup de sources, `DeepResearchResult`, degradação). `span_name` distingue subagente de agente top-level no trace.
- **write_todos** — `build_writing_tools` appenda planning tools (PlanState interno por turno); `_extract_agent` soma planning + scratchpad. Prompts ganham instrução curta de uso.
- **scratchpad** — só no ProfileExtractor; notas retêm fatos entre páginas antes do `submit_profile`.

## Riscos

- **Prompt bloat no writing agent.** O Redator já tem ~10 tools; `write_todos` é a 11ª. Mitigação: instrução curta e condicional ("tarefas com múltiplas etapas"), UMA tool de planning (sem `read_todos` — a render volta a cada write e fica no histórico). Gate de eval antes de concluir.
- **Regressão de eval do writing** (mudamos prompt + tools). Gate: `python -m radar.core.eval writing` — regressão em `eval_save` ou explosão de `eval_tool_calls` → tornar a instrução mais condicional.
- **$defs aninhados no schema Pydantic** para `list[dict]` tipado. Mitigação: annotation solta `list[dict]`; a validação real é no corpo da tool.

## Critérios de aceitação

- `run_subagent` roda o subagente e degrada para `stop_reason="error"` sem propagar exceção; `span_name=f"subagent.{name}"` chega na telemetria.
- `deep_research.py` usa `run_subagent`; testes do dedup/degradação verdes (novo seam `dr.run_subagent`).
- `write_todos` renderiza ✓/▶/☐ + contagem, substitui a lista inteira, status ausente → `pending`, shape inválido → erro-string (nunca exceção).
- `write_note`/`read_note` sobrescrevem/leem; limites (`max_notes`, `max_chars`) e nota inexistente → erro-string.
- `build_writing_tools` inclui `write_todos`; tools do extractor incluem `write_todos` + `write_note`.
- `pytest tests/` verde. Gate de eval do writing sem regressão.
