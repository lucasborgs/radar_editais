# Spec — Evolução Arquitetural Agêntica

> **Registro histórico:** proposta parcialmente absorvida e parcialmente
> superada pelo runtime atual. Consulte [`docs/architecture.md`](../architecture.md).

Status original: **proposta** · 2026-06-22 · escopo: migração de "1 agente + pipelines funcionais" para "4 agentes com papéis distintos + sub-agentes compartilhados"

---

## Decisões pinadas (não revisitar)

| # | Decisão |
|---|---|
| D1 | Memória do cliente ≠ wiki_page do KG: 3 tipos, 3 stores (CompanyProfile / exploration_log / PostgresStore) |
| D2 | KG nunca armazena dado de cliente — isolamento por design |
| D3 | "AI propõe, humano decide" — gate humano permanece no DiscoveryAgent |
| D4 | LangGraph + AsyncPostgresSaver + PostgresStore + procrastinate são a infra base — sem redesenho |
| D5 | Free-tier como padrão; pago só onde eval comprova ROI |
| D6 | Contexto estável (perfil, edital, playbook) sempre antes do dinâmico (histórico, query) |

---

## Sequência de implementação

Dependências determinam a ordem. Fases na mesma linha podem rodar em paralelo.

```
Fase 0  Config propagado para sub-agentes (wiring bug — habilitador de visibilidade)
         ↓
Fase 1  [paralelas] 1A: Critic sem truncamento + playbook no compliance
                    1B: Grounding baseline (pré-mudança de métrica)
         ↓
Fase 2  ProfileAgent como gate obrigatório do WritingSession
         ↓
Fase 3  [paralelas] 3A: ExploreAgent + exploration_log
                    3B: ResearchAgent compartilhado com ExploreAgent
         ↓
Fase 4  Grounding gate v2: claim-level entailment (requer baseline da Fase 1B)
         ↓
Fase 5  DiscoveryAgent → grafo ReAct (deferível; requer shadow-run)
```

---

## Fase 0 — Config propagado para sub-agentes

### Problema
O Critic (dentro de `save_draft`) e o ResearchAgent (dentro de `deep_research`) chamam `run_agent_graph_async` sem `trace_context`. Resultado: sub-agentes aparecem como traces raiz no Langfuse, não aninhados sob o turno do WritingAgent. Impossível medir latência e custo por sub-agente em contexto.

### Design
Em cada call site que invoca `run_agent_graph_async` para um sub-agente, adicionar:

```python
trace_ctx = telemetry.current_trace_context()
result = await run_agent_graph_async(..., trace_context=trace_ctx)
```

`current_trace_context()` já existe em `core/infra/telemetry.py` — é o seam projetado para isso (ver comentário em [`agent_graph.py:306`](../../core/llm/agent_graph.py#L306)). Sem Langfuse configurado, retorna `None` e o grafo roda sem overhead.

### Arquivos
- `core/llm/agent_tools/writing_tools.py` — todos os call sites para o critic dentro de `save_draft`
- `core/llm/agent_tools/research_tools.py` — call site do ResearchAgent dentro de `deep_research`

### Gate de eval
Nenhum eval gateado — é fix observacional. Verificar via Langfuse: sub-agentes aparecem aninhados sob o span do turno pai.

### Dependências
Nenhuma. Implementar primeiro.

---

## Fase 1A — Critic sem truncamento + playbook no compliance

### Problema
(a) O Critic trunca o draft em 3000 chars antes de avaliar — avalia com metade das páginas faltando.
(b) `playbook.for_monitor()` existe e retorna as regras de monitoramento do mecanismo, mas nenhum pass do ChecklistService o consome — o compliance pass não usa as regras do edital.

### Design

**1. Remover truncamento no Critic:**
No payload que monta o prompt do Critic dentro de `save_draft`, remover o cap de 3000 chars. Manter um backstop alto (ex: 30 000 chars) como proteção contra casos patológicos.

**2. Playbook no compliance pass:**
O ChecklistService recebe o edital como contexto, mas não recebe `playbook.for_monitor()`. Adicionar o bloco de regras do playbook como contexto adicional no prompt do pass de compliance. O playbook é contexto estável → vai no prefixo, antes do draft (D6).

Critic e ChecklistService permanecem como processos distintos por ora — a unificação futura exige avaliação própria.

### Arquivos
- `core/llm/agent_tools/writing_tools.py` — remover cap de 3000 chars no payload do critic; adicionar backstop de 30 000
- `core/services/checklist_service.py` — injetar `playbook.for_monitor()` no prompt do compliance pass
- `core/services/writing_session.py` — passar o playbook correto ao `ChecklistService`

### Gate de eval
`python -m radar.core.eval writing` — nenhuma regressão. O compliance pass deve detectar ≥ as mesmas violações que antes.

### Riscos
- **Latência do Critic:** draft maior = mais tokens = mais tempo. O Critic roda no bg-loop (thread dedicada do checkpointer); se demorar muito, atrasa o retorno do turno. Monitorar P95 de latência de turno após o deploy.
- **Compatibilidade do playbook:** confirmar que `playbook.for_monitor()` retorna string não-vazia para todos os mecanismos presentes nos goldens de writing.

---

## Fase 1B — Grounding baseline (pré-mudança de métrica)

### Problema
A métrica atual de grounding tem variância 0.05–0.625 — inutilizável como gate. Antes de substituir por claim-level entailment (Fase 4), é necessário estabelecer o baseline da nova métrica sobre os mesmos casos — sem isso não há comparação válida.

### Design
Adicionar o scorer `grounding_faithfulness` à suíte `writing` no harness:

1. Decomposição de claims atômicos do draft (LLM barato: gemini-flash-lite)
2. Verificação de entailment de cada claim contra os chunks usados na escrita (LLM juiz: gemini-flash ou cross-encoder mmarco-mMiniLMv2)
3. Score = fração de claims suportados por evidência nos chunks

Rodar sobre o golden atual. O score é registrado como Experiment no Langfuse. Esta fase **não muda comportamento em produção** — só mede.

### Arquivos
- `core/eval/metrics_writing.py` — adicionar scorer `grounding_faithfulness`
- `core/eval/registry.py` — adicionar scorer à suíte `writing`

### Gate de eval
Nenhum gate — esta fase É o estabelecimento do baseline. Output: número que Fase 4 vai superar.

### Dependências
Nenhuma. Pode rodar em paralelo com Fase 1A e Fase 2.

---

## Fase 2 — ProfileAgent obrigatório antes da escrita

### Problema
O ProfileAgent existe como grafo stateless (`profile_tools`: fetch_page, lookup_cnpj, submit_profile), mas não é chamado como pré-requisito do `WritingSession`. O WritingAgent recebe a `CompanyProfile` como está no DB — que pode estar incompleta. O WritingAgent descobre no meio da proposta que a empresa não tem os requisitos de elegibilidade do edital.

### Design
Checagem de completude de perfil na entrada do `WritingSession` (ou na rota `POST /writing/start`):

**Campos obrigatórios para iniciar escrita:**
`nome`, `tipo_entidade`, `trl`, `tamanho_empresa`, `uf`, `descricao_atividades`

Se algum campo estiver ausente, a sessão não é criada — a API retorna um payload estruturado com os campos faltantes (mesmo padrão de `request_user_info`). O frontend exibe um formulário inline ou direciona ao ExploreAgent para coletar via conversa.

O ProfileAgent (grafo com tools) permanece como caminho de enriquecimento rico via conversa — não é chamado automaticamente aqui. O gate é determinístico: checagem de presença de campos, sem LLM.

### Arquivos
- `domain/user_profile.py` — método `is_complete_for_writing() -> tuple[bool, list[str]]`
- `core/services/writing_session.py` — chamar `is_complete_for_writing()` na inicialização; lançar erro estruturado se incompleto
- `backend/routers/writing.py` — propagar o erro como payload JSON `{error: "profile_incomplete", missing_fields: [...]}`

### Gate de eval
`python -m radar.core.eval writing` — nenhuma regressão. Testar explicitamente: perfil incompleto → payload correto; perfil completo → sessão criada normalmente.

### Risco
Usuários com sessões ativas e perfil incompleto não são afetados (sessões existentes não passam pelo gate). Apenas novos starts são bloqueados. Verificar se o frontend já tem tela de edição de perfil acessível antes de mergear.

---

## Fase 3A — ExploreAgent com memória: exploration_log

### Problema
O ExploreAgent (KGMatchService modo `agent_enabled=True`) não persiste decisões entre sessões. Quando o cliente volta 3 meses depois, o agente não sabe que o Edital FINEP 779 foi descartado por CNPJ < 2 anos. Recomeça do zero.

### Design
Nova tabela `exploration_log` no schema público (dado de domínio, não memória de agente — contraste com o PostgresStore do WritingAgent):

```sql
CREATE TABLE exploration_log (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  edital_id    text NOT NULL,
  decision     text NOT NULL CHECK (decision IN ('shortlisted', 'discarded', 'pending')),
  reason       text,
  decided_at   timestamptz NOT NULL DEFAULT now(),
  decided_by   text NOT NULL CHECK (decided_by IN ('agent', 'human')),
  UNIQUE (workspace_id, edital_id)
);
CREATE INDEX ON exploration_log (workspace_id, decided_at DESC);
```

Novo tool para o ExploreAgent: `log_exploration_decision(edital_id, decision, reason)`. O agente chama esta tool quando recomenda ou descarta um edital.

No início de cada sessão do ExploreAgent, injetar no system prompt um bloco de "decisões anteriores":

```sql
SELECT edital_id, decision, reason, decided_at
FROM exploration_log
WHERE workspace_id = ?
ORDER BY decided_at DESC
LIMIT 20
```

Este bloco vai no prefixo estável do prompt (antes do histórico da conversa) — D6.

INSERT usa `ON CONFLICT (workspace_id, edital_id) DO UPDATE SET decision=EXCLUDED.decision, reason=EXCLUDED.reason, decided_at=NOW()` — última decisão prevalece, idempotente.

### Arquivos
- `supabase/migrations/xxx_exploration_log.sql` — tabela + índice + RLS (workspace_id = auth.uid() via workspaces)
- `core/llm/agent_tools/explore_tools.py` — tool `log_exploration_decision`
- `core/services/kg_match_service.py` — `_explore_agent`: carregar decisões anteriores; registrar a nova tool

### Gate de eval
Sem eval automático para esta fase — corretude funcional. Testar manualmente: duas sessões para o mesmo workspace; a segunda recebe o contexto das decisões da primeira.

### Riscos
- **ON CONFLICT:** o INSERT simples quebra na segunda visita ao mesmo edital. Usar `ON CONFLICT DO UPDATE` — obrigatório.
- **Crescimento do contexto:** com muitos editais explorados o bloco de decisões cresce. LIMIT 20 é o controle imediato; adicionar filtro `decided_at > now() - interval '6 months'` se o volume crescer.

---

## Fase 3B — ResearchAgent compartilhado com ExploreAgent

### Problema
O `deep_research` tool está disponível apenas no WritingAgent. O ExploreAgent não pode acionar pesquisa profunda sobre precedentes de aprovação ou interpretações de artigos do regulamento.

### Design
Confirmar onde `deep_research` está definido:
- Se em `writing_tools.py`: mover para `research_tools.py` (módulo compartilhado)
- Se já em `research_tools.py`: apenas registrar em `build_explore_tools`

Registrar a tool também no conjunto de tools do ExploreAgent via `build_explore_tools`.

### Arquivos
- `core/llm/agent_tools/research_tools.py` — confirmar/mover `deep_research`
- `core/llm/agent_tools/__init__.py` — `build_explore_tools` inclui a research tool
- `core/services/kg_match_service.py` — registrar a tool no explore agent

### Gate de eval
`python -m radar.core.eval rag` — nenhuma regressão no ResearchAgent via WritingAgent. Verificar no Langfuse que o trace do ExploreAgent mostra o ResearchAgent aninhado (requer Fase 0 em prod).

### Dependência
Fase 0 (config propagado) deve estar em prod — sem ela o trace aninhado não aparece e a verificação não é possível.

---

## Fase 4 — Grounding gate v2: claim-level entailment

### Problema
A métrica atual de grounding é instável (variância 0.05–0.625). A Fase 1B estabeleceu o baseline do scorer `grounding_faithfulness` — agora a substituição é comparável.

### Design
Substituir a métrica de grounding no gate do `save_draft` pelo scorer desenvolvido na Fase 1B:

1. Extrair claims atômicos do draft (LLM barato: gemini-flash-lite)
2. Verificar entailment de cada claim contra os chunks do RAG (NLI ou LLM juiz)
3. Score = fração de claims suportados
4. Gate: se score < threshold (inicial: 0.70, calibrar com dados), o critic bloqueia o save e retorna os claims sem suporte

O threshold começa permissivo e aperta conforme os dados mostram que o modelo é confiável. O score de grounding é também injetado como contexto adicional no compliance pass do ChecklistService.

### Arquivos
- `core/llm/agent_tools/writing_tools.py` — substituir métrica no gate do `save_draft`
- `core/services/checklist_service.py` — receber score de grounding como contexto no compliance pass
- Remover a métrica antiga e thresholds associados

### Gate de eval
`python -m radar.core.eval writing` — score de `grounding_faithfulness` ≥ baseline registrado na Fase 1B. Nenhuma regressão em qualidade de escrita.

### Dependência
Fase 1B (baseline em Langfuse) é pré-requisito duro. A PR de Fase 4 deve ser bloqueada no code review se o baseline não estiver registrado.

---

## Fase 5 — DiscoveryAgent: conversão para grafo ReAct

### Problema
O DiscoveryAgent é uma sequência fixa: busca Tavily → triage LLM → extract LLM → staging. Não tem raciocínio: não decide quando aprofundar um hub, não prioriza queries, não adapta o comportamento baseado no que encontrou.

### Design
Converter para grafo ReAct usando `_build_graph` (mesmo runtime do WritingAgent, `checkpointer=False` — cada run de descoberta é independente). Tools necessárias:

| Tool | Status | Fonte atual |
|---|---|---|
| `web_search(query)` | existe | `core/web_search.py` |
| `fetch_page(url)` | existe | `core/web_search.py` |
| `triage(url, snippet)` | existe como LLM call | `core/ingestion/opportunity_discovery.py` |
| `decide_depth(url, context)` | **novo** | a criar — decide se vale crawl em hub |
| `extract(url, text)` | existe como LLM call | `core/ingestion/opportunity_discovery.py` |
| `stage(opportunity)` | existe | `core/ingestion/opportunity_discovery.py` |

O agente recebe as queries de `wikis/_discovery.md` no system prompt (estável → prefixo, D6) e decide a sequência de execução. O gate humano (`POST /promote`) permanece intacto.

**Shadow-run obrigatório antes de desligar o pipeline atual:**
Flag `DISCOVERY_SHADOW_RUN=1` roda os dois em paralelo e registra: coverage (URLs encontradas por cada um), precision (% que passa pela triage humana), duração e custo por run. Mínimo de 1 semana de shadow-run antes de desligar o pipeline fixo.

Flag de rollout: `DISCOVERY_REACT_ENABLED=1` (default OFF).

### Arquivos
- `core/ingestion/opportunity_discovery.py` — funções LLM existentes viram tools LangChain; nova função `decide_depth`
- `core/llm/agent_tools/discovery_tools.py` — novo arquivo com as tools
- `core/tasks.py` — `discover_opportunities` despacha para o grafo quando `DISCOVERY_REACT_ENABLED=1`, para o shadow-run quando `DISCOVERY_SHADOW_RUN=1`, para o pipeline fixo como default

### Gate de eval
`python -m radar.core.eval triage` — nenhuma regressão na precisão da triage. Shadow-run: coverage ≥ pipeline atual, custo ≤ 1.5× o atual por run.

### Riscos (maiores desta spec)
- **Coverage gap:** o grafo pode pular queries ou parar cedo num turno ruim. Shadow-run é a única proteção — sem ≥ 1 semana de dados, não desligar o pipeline fixo.
- **Custo imprevisível:** o agente pode fazer mais calls de `web_search` do que o pipeline fixo. Cap de `max_steps=15` e monitoramento de custo por run na primeira semana.
- **Loop em hub:** `decide_depth` deve ser conservador por padrão. Um hub ambíguo não deve desencadear crawl profundo indefinido.

---

## MVP mínimo — "consultoria de fomento" observável ao usuário

O MVP entrega a metáfora de forma observável com o menor risco arquitetural:

**Incluir no MVP (Fases 0, 1A, 2, 3A):**
- Fase 0: visibilidade operacional no Langfuse
- Fase 1A: qualidade direta da proposta (Critic lê o draft completo; compliance usa o playbook)
- Fase 2: o usuário vê o fluxo completo — perfil incompleto bloqueia, profile gate é observável
- Fase 3A: ExploreAgent "lembra" entre sessões — visível quando o usuário volta e o agente referencia decisões anteriores

**Deferível (Fases 1B, 3B, 4, 5):**
- Fase 1B + 4: melhoria de qualidade interna — não visível diretamente ao usuário
- Fase 3B: ganho marginal para o ExploreAgent
- Fase 5: melhoria operacional, não visível ao usuário final

O MVP é implementável em 3-4 semanas sequenciais. As Fases 1A e 1B podem rodar em paralelo com a Fase 2. A Fase 5 é a mais arriscada e pode ser deferida indefinidamente enquanto o pipeline atual funcionar.

---

## Riscos arquiteturais em aberto

### R1 — DiscoveryAgent sem shadow-run (Fase 5)
**Risco:** conversão para ReAct sem validação empírica pode reduzir coverage de editais descobertos — o pipeline atual tem meses de operação estabilizada.
**Mitigação:** shadow-run mandatório ≥ 1 semana com flag separada. Se o shadow-run mostrar regressão de coverage, manter o pipeline fixo e adicionar apenas o `decide_depth` como pós-processamento incremental — sem substituir o radar.pipeline.

### R2 — Critic paralelo quebrando o contrato do WritingAgent (Fase 1A)
**Risco:** "Critic paralelo" pode ser mal interpretado. Nesta spec, o Critic do `save_draft` permanece serial — o que muda é que não trunca o draft. O paralelo que existe é no ChecklistService (3 passes em `asyncio.gather`). A unificação real (Critic + Checklist num único processo paralelo com insumos compartilhados) é uma mudança maior, fora desta spec — requer avaliação de impacto no contrato de resposta da tool `save_draft` (que é síncrona no LangGraph).

### R3 — Grounding gate v2 sem baseline (Fase 4)
**Risco:** trocar a métrica sem baseline impede comparação antes/depois. Se o novo scorer for mais rigoroso, vai bloquear drafts que o scorer atual aprovava — sem saber se é melhoria ou falso positivo.
**Mitigação:** Fase 1B é pré-requisito duro de Fase 4. Bloquear PR de Fase 4 no code review se o baseline não estiver no Langfuse.

### R4 — exploration_log sem ON CONFLICT (Fase 3A)
**Risco:** INSERT simples quebra quando o agente revisita um edital já logado (`UNIQUE` violation).
**Mitigação:** usar `INSERT ... ON CONFLICT (workspace_id, edital_id) DO UPDATE SET decision=EXCLUDED.decision, reason=EXCLUDED.reason, decided_at=NOW()` — idempotente, última decisão prevalece.

---

## Fora de escopo desta spec

- Unificação de Critic + ChecklistService num único processo paralelo com insumos compartilhados (Fase 4+ autônoma — exige avaliação de impacto no contrato de `save_draft`)
- RAPTOR para editais longos — avaliação separada após baseline de RAG
- BM25 substituindo bag-of-words temático — BACKLOG
- `find_analogues`/`neighbors` migrados do Obsidian para Postgres — BACKLOG
- Wiki_page narrativa do cliente no KG (visão para humanos, não memória de agente) — decisão de produto, fora desta spec
