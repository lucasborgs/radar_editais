# TASK 1 — Findings (Streaming, item 1)

**Data:** 2026-07-18 · **Script:** `spikes/lever1_streaming/demo.py` · **Grafo:** real (`_build_graph`, tools/system do explore, sem perfil)
**Pergunta de teste:** "Quais editais estão abertos agora? Liste até 3 com um resumo breve de cada um." (força ≥1 tool call, geralmente 3: `list_editais` + `get_edital`×3)
**Runs:** 2 execuções limpas após o fix de filtro (ver Wrinkle 2), `temperature=0`.

## Gap conhecido (decidido com o usuário antes de rodar)

**Não há `ANTHROPIC_API_KEY` neste ambiente local** — mesmo gap já documentado no spike anterior (`spikes/agent_sdk_explore/results/report.md`). Rodei só o transporte **OpenAI** (via `OPENAI_API_KEY` canônico — mesmo branch de código de `_build_chat_model` que um OpenAI-compat real usaria, só sem `base_url` custom). O veredito de transporte e o achado do gap de `stream_usage` são mecânica do LangChain/LangGraph, não do backend — mas a paridade Anthropic em si **fica não verificada localmente** e deveria ser confirmada num smoke antes de declarar TASK 2/3/4 prontas em produção.

## Números (2 runs)

| | ainvoke (ref) | astream_events v2 | astream multimode |
|---|---|---|---|
| Run 1 total | 10.906s | 8.110s (TTFT 4.207s) | 13.908s (TTFT 4.341s) |
| Run 2 total | 10.009s | 7.597s (TTFT 3.999s) | 8.560s (TTFT 4.099s) |

TTFT consistentemente ~40% da latência total do `ainvoke` — tokens chegam bem antes do turno terminar. **Condição (a) do checkpoint: confirmada.**

O TTFT (~4s) não é o primeiro token absoluto do turno — é o primeiro token *com texto* (a resposta final). As rodadas de tool-calling anteriores geram `AIMessageChunk` com `content=""` (só `tool_calls`), então não produzem delta visível — comportamento correto: não há texto para narrar ao usuário enquanto o agente decide/chama tools.

## Paridade do `AgentResult` reconstruído (2 runs, ambos transportes)

`stop_reason`, `n_steps` (7) e `tools` (ordem e nomes) bateram **exatamente** com o `ainvoke` de referência nos dois runs e nos dois transportes — confirma que `_derive_stop_reason`/`_messages_to_agent_result` aplicados ao estado final do stream reconstroem o mesmo trace estrutural.

`final_text` e `usage` **divergiram levemente** (texto quase idêntico, ex. "Aqui estão três editais..." com wording ligeiramente diferente; `output_tokens` variou 414–465 entre as 3 chamadas). **Isto não é um bug de plumbing**: são 3 chamadas independentes ao mesmo modelo (mesmo com `temperature=0`, gpt-4o-mini não é perfeitamente determinístico — variância residual conhecida). O teste field-a-field do plano (linha 88) assume implicitamente que dá para comparar texto entre execuções separadas do LLM; na prática só os campos estruturais (`stop_reason`/`steps`/`tools`) são comparáveis 1:1 entre chamadas independentes. `usage` nunca veio **zerado** em nenhum run — sinal real é ausência de zeragem, não igualdade de valor.

## Gap de usage OpenAI (item 7 do plano) — **NÃO reproduzido nesta versão pinada**

```
stream_usage=False → usage_metadata={'input_tokens': 17, 'output_tokens': 5, ...}
stream_usage=True  → usage_metadata={'input_tokens': 17, 'output_tokens': 5, ...}
```

Idêntico com e sem a flag — e nos runs do grafo completo, `usage` do estado final via streaming **nunca veio zerado** mesmo sem `stream_usage=True` em `_build_chat_model`. **Isto contradiz a premissa do plano** (`langchain-openai==1.3.3`, pinada no projeto, já popula `usage_metadata` no streaming por padrão — provavelmente a lib passou a default `stream_options.include_usage=True` internamente numa versão recente). Achado: o fix da TASK 2 (`stream_usage=True` no `ChatOpenAI`) é **inofensivo mas não comprovadamente necessário** nesta versão pinada. Recomendo manter o fix mesmo assim (defensivo, custo zero, protege contra downgrade futuro da lib) mas sem tratá-lo como o achado central do item.

## Wrinkles descobertos (mudam a implementação da TASK 2)

1. **`astream_events(version="v3")` não é um async iterator direto nesta versão pinada** (`langgraph==1.2.6`). `Pregel.astream_events` com `version="v3"` retorna `self._apregel_stream_v3(...)`, um `AsyncGraphRunStream` experimental ("may change", cursors assinados) — `async for` direto nele falha com `TypeError: 'async for' requires an object with __aiter__ method, got coroutine`. **`version="v2"` funciona como o plano descreveu** (async generator de `StreamEvent`, `on_chat_model_stream`/`on_chain_end`). Se a TASK 2 for pelo caminho `astream_events`, usar **v2**, não v3.
2. **`stream_mode=["messages", "values"]` emite `ToolMessage` completo (não só `AIMessageChunk`) no modo `"messages"`**, com `metadata["langgraph_node"] == "tools"`. Sem filtrar por `isinstance(msg, AIMessageChunk)`, o texto bruto da tool (ex. `"Encontrados 3 editais:\n  ID:web:..."`) é lido como se fosse delta de token do assistente — bug real que apareceu na primeira versão do script. A implementação de produção **precisa** desse filtro.
3. Extração do estado final: em `astream_events` v2 não há um campo óbvio "isto é a raiz"; usei uma heurística (`output` é dict contendo as 4 chaves de `AgentState`: `messages`/`llm_calls`/`tool_rounds`/`documents` — nenhum nó individual retorna as 4 juntas, só o merge final do grafo). Funcionou nos 2 runs, mas é uma heurística, não uma garantia de API. Em **`astream(stream_mode=["values"])`**, o último item do modo `"values"` é **garantido** pela API ser o estado terminal — sem heurística.

## Recomendação de transporte para a TASK 2

**`graph.astream(init, config, stream_mode=["messages", "values"])`** (astream multimode), não `astream_events`. Motivos:
- Estado final vem garantido pela API (`values`), sem heurística de "isto é o root".
- Payload mais enxuto (só duas categorias de evento vs o event-log genérico do `astream_events`, que inclui `on_chain_start/end` de cada nó do grafo).
- O único wrinkle (filtrar `ToolMessage` do modo `"messages"`) é um `isinstance` de uma linha — mais barato que a heurística de reconstrução de estado do `astream_events`.
- `astream_events(version="v3")`, como o plano assumia, **não está disponível de forma direta** na versão pinada — descartado.

## ★ Veredito do checkpoint ★

- **(a) Tokens antes do fim do turno:** ✅ confirmado nos 2 transportes, 2 runs (TTFT ~4s vs total 8–14s).
- **(b) Estado final reconstrói `AgentResult` com paridade estrutural (`stop_reason`/`steps`/`tools`):** ✅ confirmado, 2 runs, 2 transportes. `usage` nunca zerou (sem necessidade comprovada do fix `stream_usage=True` nesta lib pinada, mas mantê-lo é seguro).
- **Anthropic:** não testado localmente (sem key) — decisão tomada explicitamente com o usuário antes de rodar. Recomendo um smoke rápido com Anthropic antes do cutover de produção da TASK 3/4 (não bloqueia TASK 2, que é aditiva e não muda comportamento existente).

**GO** — condição (a) e (b) satisfeitas para o provider disponível. Transporte escolhido: **`astream(stream_mode=["messages","values"])`**. Prosseguir para TASK 2 com esse transporte, aplicando os 2 wrinkles acima (filtro `AIMessageChunk`, estado final via `values`).
