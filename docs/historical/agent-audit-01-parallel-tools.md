# 01 — Execução paralela de tools + suporte async (Findings B + C)

**Fase:** 1 (plumbing) · **Validação:** teste + eval · **Esforço:** médio-alto

## Problema

Dois limites acoplados no loop do agente:

- **B — sequencial:** quando o modelo pede várias tools no mesmo turno, elas
  rodam num `for` (`agent_runtime.py:652`). Tools de I/O de rede (fetch,
  web_search, CNPJ) bloqueiam uma após a outra. Latência = soma, não máximo.
- **C — async recusado:** o runtime é sync; tool async é rejeitada com string de
  erro (`agent_runtime.py:83-88`). O Extrator crawla até 10 páginas em sequência.

## Estado atual

- Loop sync em `run_agent` (`agent_runtime.py:599-694`); dispatch de tools no
  `for use in llm_step.tool_uses` (`:652`).
- `Tool.call` recusa coroutine (`:83-88`).
- Adapters `_call_openai`/`_call_anthropic` são sync (SDKs síncronos).
- Call sites: `writing_session.py:776`, `kg_match_service.py:729`,
  `profile_extractor.py:306` — todos chamados de dentro de handlers FastAPI
  (async) ou tasks procrastinate.

## Mudança proposta

1. **`Tool.call` async-aware:** se `inspect.iscoroutine(result)`, `await`-ar em vez
   de recusar. Manter suporte a tools sync (não-coroutine).
2. **Loop async:** criar `run_agent_async(...)` com a mesma assinatura; dentro de
   cada turno, executar os `tool_uses` concorrentes via
   `asyncio.gather(*[registry.get(u).call_async(u["input"]) for u in uses])`.
   Tools sync rodam em `asyncio.to_thread` para não bloquear o loop.
3. **Shim de compatibilidade:** manter `run_agent(...)` sync que faz
   `asyncio.run(run_agent_async(...))` — call sites existentes não quebram.
   Migrar call sites em handlers async para `await run_agent_async` num passo
   seguinte (opcional).
4. **Telemetria:** os spans `telemetry.tool_call` precisam funcionar dentro do
   gather — verificar reentrância dos context managers (`:653`).

## Validação

- **Teste de paralelismo:** 2 tools que `sleep(0.5)`; assert wallclock ≈ 0.5s, não
  1.0s.
- **Teste async:** tool definida `async def` executa e retorna string.
- **Teste shim:** `run_agent` sync ainda funciona (sem regressão de call sites).
- **Eval gate:** rodar `python -m radar.core.eval writing` e `extraction` — scores não
  regridem (mudança é de perf, não de comportamento).

## Risco

Médio-alto: mexe no coração do runtime. Atenção a (a) ordem dos tool_results vs
tool_uses (Anthropic exige correspondência por id — gather preserva ordem se
montado por índice), (b) reentrância dos spans de telemetria, (c) SDKs sync dentro
de `to_thread`.

## Perguntas em aberto

- Migrar **todos** os call sites para async agora, ou só introduzir
  `run_agent_async` + shim e migrar incrementalmente? (Recomendado: shim primeiro.)
