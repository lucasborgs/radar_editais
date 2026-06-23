# Auditoria de Tokens — Radar de Editais

> Branch: `main` · Auditado em: 2026-06-23  
> Runtime real: LangGraph StateGraph (Etapas 1-3 mergeadas). O loop `run_agent_async` legado não é mais o caminho de produção.

---

## 1. Mapeamento das Assinaturas de Ferramentas (Tool Signatures)

### 1.1 Writing Agent — `build_writing_tools()` (10 tools)

Injetado em cada turno da `WritingSession` e na geração em lote. Todas as 10 estão presentes desde o primeiro token da sessão.

| # | Tool | Descrição no Schema | Params |
|---|------|---------------------|--------|
| 1 | `search_edital` | ~200 chars | `query`, `k` |
| 2 | `load_skill` | ~160 chars | nenhum |
| 3 | `search_library` | ~130 chars | `query`, `k` |
| 4 | `read_section` | ~100 chars | `title` |
| 5 | `read_full_proposal` | ~190 chars | nenhum |
| 6 | `save_draft` | **~550 chars** + instrução inline do critic | `section_title`, `content`, `force` |
| 7 | `request_user_info` | **~600 chars** (descrição de interrupt) | `field`, `prompt` |
| 8 | `recall_company_learnings` | ~240 chars | `topic` |
| 9 | `deep_research` | ~260 chars | `question` |
| 10 | `write_todos` | **~420 chars** + exemplo de JSON embutido | `todos: list[dict]` |

**Tamanho estimado do bloco de tools no system (JSON Schema serializado):**  
~3.500–4.500 tokens. Presente em **todo call LLM** do agente de escrita.

### 1.2 Explore Agent — `build_explore_tools()` (9 tools)

| # | Tool | Observação |
|---|------|------------|
| 1–4 | `list_editais`, `get_edital`, `find_analogues`, `get_graph_neighbors` | Payloads limpos |
| 5 | `find_ict_partners` | Docstring verbosa sobre "sugestão vs. exigência" |
| 6–7 | `list_icts`, `list_investidores` | Ok |
| 8 | `oportunidades_por_tema` | Mais verboso — explica toda a lógica cross-dimensional |
| 9 | `search_edital_trechos` | **~400 chars** + instrução de "use SÓ quando…" longa |

**Tamanho estimado:** ~2.800–3.500 tokens.

### 1.3 Critic Sub-agent — `build_critic_tools()` (3 tools)

Payloads enxutos (`read_target_context`, `read_company_profile`, `read_proposal_sections`). ~800 tokens total. Bem dimensionado.

### Diagnóstico — Sobrecarregamento do System Prompt

```
PROBLEMA CRÍTICO: request_user_info e write_todos somam ~1.020 chars de descrição
para uma funcionalidade que só é relevante em ~20% dos turnos. O LangChain serializa
TODOS os schemas para o modelo a CADA chamada LLM, inclusive nas intermediárias
(tool-result → agent). Num turno de 6 steps, o bloco de tools aparece 6 vezes.

Estimativa de custo do bloco de tools por turno (8 steps):
  8 chamadas × ~3.800 tokens de tools = ~30.400 tokens de input só em tool schemas.
```

**Oportunidades de redução:**

- `request_user_info`: 600 chars de doc. Pode ser reduzida a ~100 chars — a instrução de "PAUSA o turno" já está no system prompt do writer.
- `write_todos`: O exemplo JSON embutido no docstring (~180 chars) gera tokens extras em toda chamada. Remover o exemplo da docstring (manter no system prompt do agente).
- `save_draft`: Instrução de critic no docstring pode ser resumida — o writer não precisa saber os detalhes internos do critic.

---

## 2. Fluxo do StateGraph e o Lixo Cognitivo

### 2.1 Arquitetura do Grafo

```
START → agent → tools → [reflect?] → agent → ... → END
         ↑_______________|
```

O estado usa `add_messages` com reducer de **append puro**:

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # NUNCA trunca
    llm_calls: int
    ...
```

### 2.2 Como as ToolMessages são apensadas

No nó `tools` ([agent_graph.py:155-184](core/llm/agent_graph.py)):

```python
async def tools(state: AgentState) -> dict:
    out = await tool_node.ainvoke(state)
    tmsgs = out["messages"]
    for m in tmsgs:
        m.content = _cap(str(m.content), TOOL_RESULT_CHAR_CAP, ...)  # cap por resultado
    ...
    return {"messages": tmsgs, ...}  # appended ao estado cumulativo
```

O `TOOL_RESULT_CHAR_CAP = 8.000 chars` (env) é um **cap por resultado individual**, não do histórico total. Após N rodadas de tools:

| Rodadas | Chars acumulados (estimativa, sem reflexão) |
|---------|---------------------------------------------|
| 1       | ≤ 8.000                                     |
| 3       | ≤ 24.000                                    |
| 6       | ≤ 48.000                                    |
| 8 (max) | ≤ 64.000                                    |

Na prática, cada chamada ao LLM intermediária recebe **todo o histórico acumulado de ToolMessages** + o bloco de tool schemas + system prompt.

### 2.3 Mecanismo de "Truncamento"

```python
def reflect(state: AgentState) -> dict:
    return {
        "messages": [HumanMessage(content=_REFLECT_PROMPT)],  # ADICIONA ~25 tokens
        "rounds_since_reflect": 0,
        ...
    }
```

O nó `reflect` **adiciona** uma mensagem ao histórico — não comprime nem remove nada. A reflexão é um mecanismo anti-drift cognitivo, não uma mitigação de tokens.

### 2.4 Histórico Cross-Turno da WritingSession

O `thread_id` da escrita usa `user_turn_index` ([writing_session.py](core/services/writing_session.py)):

```python
thread_id = f"{self.workspace_id}:{self.session_id}:{user_turn_index}"
```

**Cada turno é um thread novo no checkpointer.** O histórico cross-turno não é acumulado pelo LangGraph — ele vem de `_build_agent_initial_messages`, que injeta os turnos anteriores do banco:

```
initial_messages por turno =
  [PERFIL DA EMPRESA]           ← sempre presente
  [PITCH TARGET CONTEXT]        ← se modo pitch
  [turno 1 user]
  [turno 1 assistant]
  [turno 2 user]
  [turno 2 assistant]
  ...
  [turno N user_message]        ← mensagem atual
```

A `COMPRESS_THRESHOLD = 10` ([writing_session.py:75](core/services/writing_session.py)) comprime apenas APÓS 10 turnos. Turnos 1–9: histórico bruto cresce sem teto na `initial_messages`.

### Diagnóstico — Crescimento Exponencial do Contexto

```
Um turno típico (turno 5, escrevendo seção 3):
  System prompt:      ~900 tokens
  Tool schemas:       ~3.800 tokens (repete em cada step LLM)
  Profile context:    ~400-800 tokens
  Histórico (4 turns anteriores, incluindo rascunhos): ~3.000-6.000 tokens
  Intra-turn tools:   ~4-12 tool rounds × up to 8.000 chars = até 40.000 tokens

  INPUT TOKEN ESTIMATIVA (step intermediário turno 5): 15.000–30.000 tokens
```

---

## 3. O Peso do RAG e do Grafo no Contexto

### 3.1 Payload de `search_edital` (writing)

```python
SEARCH_EDITAL_CHUNK_CHAR_CAP = 1.500  # por chunk (env)
SEARCH_EDITAL_CHAR_CAP       = 8.000  # total da tool-result (env)
k = 5 chunks default
```

**Output formatado de `format_chunks_for_prompt`:**

```
TRECHOS RELEVANTES DO EDITAL (top-5 mais relevantes para a sua pergunta):

[Trecho 1 — Elegibilidade] regulamento-chamada-602.pdf, p. 12-14
<até 1.500 chars de texto>

[Trecho 2 — Análogo finep:601 — Valor] chamada-anterior.pdf, p. 8
<até 1.500 chars de texto>
...
```

| Item | Chars | Tokens (~4c/token) |
|------|-------|--------------------|
| Header | ~80 | ~20 |
| 5 chunks × 1.500 chars | ~7.500 | ~1.875 |
| 5 metas `[Trecho N — seção] arquivo, p. X` | ~250 | ~63 |
| **Total** | **~7.830** | **~1.960** |

Capped em 8.000 chars → **~2.000 tokens por chamada a `search_edital`**.

Com 2-3 chamadas por turno de escrita: **4.000–6.000 tokens de RAG** acumulados no estado intra-turno.

### 3.2 Payload de `search_edital_trechos` (explore)

```python
EXPLORE_CHUNK_CHAR_CAP   = 800   # por chunk (menor que o writing)
EXPLORE_TRECHOS_CHAR_CAP = 6.000 # total
MAX_EDITAIS = 5
k_por_edital = 3 (default)
```

Mais enxuto que o writing path — adequado para o explore público.

### 3.3 `read_full_proposal` — O Maior Risco Pontual

```python
READ_FULL_PROPOSAL_CHAR_CAP = 8.000  # cap total
```

Numa proposta com 6 seções já redigidas (600–1.500 words cada):

- Sem truncamento: ~18.000–36.000 chars
- Com cap: trunca em 8.000 chars e avisa

**Se chamada 1–2 vezes por turno num turno de conclusão, adiciona ~2.000–4.500 tokens ao estado.**

### 3.4 Metadados Verbosos?

`format_chunks_for_prompt` retorna apenas campos estruturados úteis (`section`, `source_file`, `page_range`). Sem HTML residual. O chunker limpa o conteúdo na ingestão. Os únicos campos de custo marginal são o `source_file` (nome do PDF, ~30 chars) e `page_range` em chunks textuais onde o número de página não tem ação pelo modelo.

### 3.5 `recall_company_learnings` — Sem Cap Aplicado

```python
@tool
def recall_company_learnings(topic: str = "") -> str:
    from core.reflection_service import search_insights_for_tool
    return search_insights_for_tool(session._db, session.workspace_id, query=topic)
```

Sem `_cap()` no retorno. Workspaces com muitos insights acumulados podem gerar outputs não controlados que escapam ao `TOOL_RESULT_CHAR_CAP` do nó `tools`.

> **Nota:** o cap central no nó `tools` ([agent_graph.py:162](core/llm/agent_graph.py)) aplica `_cap` sobre `m.content` depois que o ToolNode executa, então o backstop existe — mas o log de disparo não está disponível sem dados de prod.

---

## 4. Inventário de LLM Touchpoints (Stateful vs. Stateless)

| Componente | Arquivo | Modelo | Tipo | Input Estimado | Risco de Loop |
|---|---|---|---|---|---|
| Writing Agent (por step) | [writing_session.py](core/services/writing_session.py) | `claude-sonnet-4-6` | **Stateful** (checkpointer/turn) | 8K–30K tokens/step | Médio (max_steps=8) |
| Critic Sub-agent | [critic_agent.py](core/llm/agent_tools/critic_agent.py) | `gpt-4o` | Stateless (max_steps=3) | 3K–10K tokens | **ALTO** — dispara em todo `save_draft` |
| WritingSession Compress | [writing_session.py](core/services/writing_session.py) | `gpt-4o-mini` | Stateless 1-shot | 2K–4K tokens | Baixo (só no turno 10+) |
| WritingSession Signal | [writing_session.py](core/services/writing_session.py) | `gpt-4o-mini` | Stateless 1-shot | 2K–4K tokens | Baixo |
| ChecklistService (3 passes paralelos) | [checklist_service.py](core/services/checklist_service.py) | `gemini-2.5-flash` / `gpt-4o-mini` | Stateless paralelo | 4K–12K por pass = **12K–36K total** | Médio (chamada manual) |
| Explore/KGMatch Agent | [kg_match_service.py](core/services/kg_match_service.py) | `claude-sonnet-4-6` | Stateless por turno | 3K–8K/turn | Baixo |
| Profile Extractor Agent | [profile_extractor.py](core/profile_extractor.py) | `claude-sonnet-4-6` | Stateless | 3K–12K | Baixo (1× por submit) |
| Edital Extractor | [edital_extractor.py](core/edital_extractor.py) | `gpt-4o` / `gemini-2.5-flash` | Stateless | 5K–20K por edital | **ALTO** (batch em pipeline) |
| ContextualRetrieval | [contextual_retrieval.py](core/contextual_retrieval.py) | `gpt-4o-mini` | Stateless por chunk | 400–800 tokens/chunk | **ALTO** (1 call/chunk no reindex) |
| Deep Research (sub-agent) | [research_tools.py](core/llm/agent_tools/research_tools.py) | `anthropic`/`openai` | Stateless (max_steps=5) | 2K–8K | Médio (por chamada no turno) |
| HybridMatch Stage 2 | [hybrid_match_service.py](core/services/hybrid_match_service.py) | `gemini-2.5-flash` / `gpt-4o-mini` | Stateless | 3K–6K por empresa | Baixo (1× por match) |
| ETL Enrichment | [etl_process.py](pipeline/etl_process.py) | `gemini-2.5-flash` | Stateless batch | 2K–5K por edital | **ALTO** (todos editais no pipeline) |

### 4.1 Pontos de Multiplicação Silenciosa de Custo

**Critic em geração em lote:**

```
Cenário: usuário pede "gera a proposta completa" (8 seções)
  → run_generation_turn despacha 8 runs de agente
  → cada seção faz 1+ save_draft calls
  → cada save_draft dispara run_critic (gpt-4o, max_steps=3)

  Custo mínimo : 8 seções × 1 save_draft × ~5K tokens critic = 40K tokens (gpt-4o)
  Com rejeição : 8 × 2 saves × 5K = 80K tokens só no critic
```

**ContextualRetrieval no reindex:**

```python
# contextual_retrieval.py:81
client.chat.completions.create(model=_MODEL, max_tokens=80, ...)
```

Chamado por chunk durante `build_knowledge_graph` ou `reindex_edital`. Um edital FINEP pode ter 200–500 chunks:

```
500 chunks × ~600 tokens de input = 300K tokens por edital (gpt-4o-mini)
```

**ETL Enrichment em batch:**

O `pipeline/etl_process.py` usa `--backend gemini` por default. Sem o cache `.enrichment_cache.json`:

```
50 editais × ~3K tokens = 150K tokens (Gemini Flash)
```

---

## Resumo Executivo dos Ralos

| Ralo | Severidade | Remediação |
|------|-----------|------------|
| Tool schemas repetidos em todos os steps (~3.800 tokens × N steps) | 🔴 Alto | Reduzir docstrings de `request_user_info`, `write_todos`, `save_draft` |
| Histórico cross-turno sem teto (turnos 1–9) | 🔴 Alto | Baixar `COMPRESS_THRESHOLD` de 10 → 4–5 |
| Critic dispara em todo `save_draft` na geração em lote | 🔴 Alto | Passar `force=True` na geração batch (usuário revisa depois) |
| `add_messages` sem pruning intra-turno | 🟠 Médio | Implementar `trim_messages` no nó `agent` (manter só últimas N ToolMessages) |
| `recall_company_learnings` sem `_cap()` explícito | 🟠 Médio | Adicionar `_cap(result, 3000, tool_name="recall_company_learnings")` na tool |
| ContextualRetrieval por chunk no reindex | 🔴 Alto (batch) | Verificar se `CONTEXTUAL_RETRIEVAL_ENABLED` está desligado em prod |
| `read_full_proposal` chamado desnecessariamente | 🟡 Baixo | Já tem cap de 8K; monitorar log de disparo antes de apertar |
