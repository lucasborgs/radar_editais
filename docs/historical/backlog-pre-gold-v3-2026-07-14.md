# Histórico — backlog anterior ao gold v3 (snapshot de 2026-07-14)

> Snapshot preservado durante a revisão de qualidade do repositório. Os itens
> abaixo refletem arquiteturas, experimentos, decisões de produto e estados
> operacionais registrados entre junho e julho de 2026; não representam o
> backlog técnico atual. Consulte [`docs/BACKLOG.md`](../BACKLOG.md) para a lista
> reconciliada contra a implementação vigente.

> Documento **vivo**. Itens conscientemente adiados (não esquecidos). Cada item
> traz contexto suficiente para retomar sem reconstruir o raciocínio: **o quê**,
> **por que adiado**, **onde está specado**, **ponto de entrada**, **status**.
>
> Convenção: ao concluir um item, mova-o para "Concluídos" (com o commit/PR) ou
> remova-o. Ao adiar algo novo, adicione aqui na hora — o custo de esquecer é alto.

---

## Aberto

### 🔴 P0 DEPLOY — `supabase db push` (migration 034 fecha leak da fila procrastinate)

- **O quê:** a [migration 034](../../supabase/migrations/034_procrastinate_lockdown.sql)
  (RLS + REVOKE nas tabelas/funções `procrastinate*`) foi aplicada e verificada só no
  Supabase **local**. O leak-test apontado ao **remoto** ainda REPROVA
  (`tests/test_tenant_isolation.py::...::test_procrastinate_surface_negada`) — ou seja,
  **em produção a anon key ainda lê `procrastinate_jobs.args` (workspace_id/payloads
  cross-tenant) e deleta/enfileira jobs**. Junto sobem 032/033 (pendentes no ledger remoto).
- **Por que importa:** é o furo P0 da Frente 1 do leak-test pré-beta — vazamento
  cross-tenant + controle de fila por chamador anônimo. Bloqueia o beta externo.
- **Onde está specado:** `docs/security/tenant-isolation.md` (seção "FURO P0"),
  `docs/specs/pre-beta-verification.md` (Frente 1).
- **Ponto de entrada:** `supabase db push` (ou runbook `scripts/deploy.sh`). Depois,
  rodar o leak-test contra staging com `TENANT_ISOLATION_ALLOW_REMOTE=1` p/ confirmar verde.
- **Status:** aberto (2026-07-02) — fix pronto e testado local; falta só o push ao remoto.

### Regra de operação — branch retomada com gate de eval pendente roda o gate ANTES de código novo

- **O quê:** disciplina de processo (Frente 3 do `pre-beta-verification.md`), não
  workstream. Ao retomar uma branch que tem gate de eval pendente, **rodar o gate antes
  de escrever código novo nela** — evita empilhar trabalho sobre uma base cuja qualidade
  nunca foi medida.
- **Pendências conhecidas (2026-07-02):**
  - `feat/elig-constraints-producer` (PR2+PR3 WIP de [[project_eligibility_constraints]]):
    **3 gates NÃO rodados** (matching/produtor/golden). Localmente só existe
    `feat/elig-constraints-schema` (PR1 mergeado) — a branch do produtor está no remoto/stash.
  - agentic-evolution F2/F3A (PRs #36/#37, [[project_agentic_evolution_phases]]): gates de
    env não rodados.
  - Eval de escrita como gate da **remoção do legacy de match** (spec robustez).
- **Status:** regra ativa (2026-07-02). Sem entregável próprio — dissolve no fluxo.

### Hardening pré-beta — spec COMPLETA (6/6 PRs na branch); falta push/merge

- **O quê:** a spec `docs/specs/hardening-pre-beta.md` tem 6 PRs. Estado em 2026-07-02
  (todos consolidados na branch `feat/hardening-pre-beta`, HEAD 4953ed5ca):
  - **PR1** (segurança P0 — SSRF/caps/DEMO_MODE/rate-limit/delimitadores): ✅ integrado.
  - **PR2** (prompt caching — temporal no tail + `cache_control` dormente em OpenAI):
    ✅ integrado (merge de `feat/prompt-caching`; conflito semântico com o fix de
    outline reconciliado em `test_prompt_caching.py`).
  - **PR4** (retry nas tasks + fix ledger discovery + alerta e-mail): ✅ integrado
    (merge de `feat/resilience-email`).
  - **PR3** (geração batch paralela — `asyncio.gather`+Semaphore, F4): ✅ commit
    665dc33ea. `GENERATION_CONCURRENCY` (default 4); contrato `GenerationOutcome`
    idêntico; timeout de 300s agora preserva seções já concluídas.
  - **PR5** (observabilidade de custo — `llm_span` nas chamadas 1-shot, F11): ✅ commit
    0cee1dd50. 6 call sites instrumentados com workspace/session como metadata.
  - **PR6** (purge de checkpoints F9 + `truncated` F10 + cache no match F12): ✅ commit
    4953ed5ca. Task `purge_agent_checkpoints` (dom 06:00 UTC, `CHECKPOINT_RETENTION_DAYS`
    default 30); `truncated` em /writing/turn e /explore com aviso na UI; memo
    `_ecosystem_snapshot` + cache in-process dos embeddings da empresa.
- **Gates rodados (2026-07-02):** eval `writing` sem crash, `mean_saved=1.0`
  (`pct_grounded` 0.38 — dentro da banda de ruído 0.06–0.43 das runs do dia, ver
  Frente 2 de `pre-beta-verification.md`); eval `matching` 0.881/3.625 idêntico ao
  baseline; ruff + 629 pytest + `tsc --noEmit` verdes.
- **Falta:** push + merge da branch na main (decisão do Lucas — testa local primeiro).
- **Débito lateral:** edição não-commitada na worktree `feat/resilience-email`
  (`core/tasks.py` remove `build_knowledge_graph` legado do cron de ETL — alinha com o
  CLAUDE.md mas o código ainda o chama). Patch salvo no scratchpad da sessão; decidir
  integrar ou descartar.
- **Status:** implementação FECHADA (2026-07-02) — aberto só o push/merge.

### Match por hipergrado — 2ª camada (elegibilidade dura) via hiperarestas nativas

- **O quê:** o match cross-domínio (`core/services/hypergraph_match.py`) responde só
  *"é relevante pro meu tema?"* — **afinidade**, por cosseno sobre os NÓS (Tema/Tec/
  Aplicação). Falta a 2ª pergunta: *"eu posso? / isso me desqualifica?"* — **elegibilidade
  dura**. Esse sinal não está nos nós: mora nas **hiperarestas nativas** do KG
  (`Edital —exige→ TRL/porte/região`, `Edital —exclui→ setor`), que o match **ignora**
  hoje ([[project_hyperedges_underused]]).
- **Por que importa:** sem isso, (a) um match temático forte mas **inelegível** (TRL/
  porte/região fora) sobe igual; (b) **exclusões** não reprovam — empresa de tabaco casa
  «tabaco»↔«tabaco» com cosseno alto e o match a *aprova*, quando a aresta `exclui` deveria
  reprovar. Nó-cosseno não tem sinal negativo nem condição numérica.
- **Decisão de design já tomada:** o golden de matching cravou *"critério = afinidade;
  elegibilidade é camada SEPARADA"* (eval_data/golden/matching.json) — **essa camada
  separada são literalmente as arestas**. Logo NÃO é otimização do ranking de afinidade
  (esse está coberto sem elas); é **capacidade nova** (filtro duro + exclusões + explicação
  encadeada do `get_node_neighborhood`).
- **Por que adiado:** é o "obj 1" da discussão PÓS-sprints da spec
  (`docs/specs/hypergraph-architecture.md`). Sprints 1–3 entregam o eixo afinidade primeiro.
- **Ponto de entrada:** `hypergraph_match.find_matching_editais` (hoje lê só nós via
  `load_ecosystem_nodes`) — precisaria ler as `edges` dos subgrafos e cruzar requisitos/
  exclusões com o `CompanyProfile` (TRL/porte/UF). Casa com [[project_eligibility_constraints]]
  (o produtor de `eligibility_constraints` é a fonte estruturada equivalente).
- **Status:** aberto (2026-06-29). Reforçado a pedido após F3 (Sprint 1). Não bloqueia
  as sprints; é a evolução natural quando o eixo afinidade estabilizar.

### Filtragem por público-alvo — editais fora da persona (startups) entram no radar

- **O quê:** o radar ingere chamadas FINEP que **não são para o público-alvo**
  (startups/empresas) — ex.: seleção de GESTOR de um FIP, carta convite para agentes
  operacionais que repassam recursos, desafio não-startup. O gate de público existe
  (`core/pme_filter.py` + elegibilidade no `core/services/hybrid_match_service.py`, que
  cruza `publico_alvo`), mas **não tem dado para trabalhar**.
- **Causa-raiz (evidência 2026-06-28, triage dos 5 editais `n_pdfs:0` do deploy durável):**
  o `publico_alvo` vem **vazio (`[]`)** da API Liferay para esses tipos não-clássicos
  (finep:613 / 972778 / 958302 / 986129) — e a canonicalização §5.5 em
  `build_knowledge_graph._normalize_publico` derruba fragmentos fora do vocab → `[]`. Num
  caso vem **errado** (finep:968467 taggeado `['Startups','ICTs']`, mas o edital não é para
  startup). Filtro com input vazio/errado não exclui nada.
- **Por que importa:** polui match/radar com oportunidades irrelevantes para a persona
  (ruído no topo). A mesma cegueira atinge potencialmente editais que DEVERIAM entrar
  (público vazio → o gate não consegue classificar).
- **Por que é difícil:** o sinal não está estruturado na fonte — exige extração de
  elegibilidade/público mais rica que a taxonomia da API. Conecta direto a
  [[project_eligibility_constraints]] / extração v2: o produtor de `eligibility_constraints`
  (Fase 3 do extrator) é o lugar para capturar "gestor de FIP / carta convite / não-startup".
- **Sub-gap secundário (captura de PDF):** 4 dos 5 TÊM PDF na página, mas `extract_pdf_urls`
  (campo `documentos` da API) retornou vazio → `n_pdfs:0`. Moot para esses (descartáveis),
  mas a mesma falha poderia esconder PDFs de editais relevantes nesses formatos. Ponto:
  `pipeline/extractors/finep_api.py::extract_pdf_urls`.
- **Ponto de entrada:** `core/pme_filter.py` (gate atual) · `hybrid_match_service.py`
  (elegibilidade) · `pipeline/build_knowledge_graph.py::_normalize_publico` (+ §5.5 vocab) ·
  extração de elegibilidade ([[project_eligibility_constraints]]).
- **Status:** aberto (2026-06-28). Surgiu do deploy do Documento Canônico durável; não
  bloqueia (os 5 são descartáveis). Ver [[project_durable_source_docs]].

### Gate de eval `writing` — memória semântica (Et.5 LangGraph) vs bloco fixo

- **O quê:** rodar o A/B do eval `writing` (grounding/coerência) comparando
  `WRITING_SEMANTIC_MEMORY=1` (retrieval semântico via Store, Etapa 5) vs `=0` (bloco
  fixo de 6 insights). Confirma que a injeção query-conditioned **não regride** antes de
  cortar o bloco fixo de vez. Risco #4 da spec `docs/specs/langgraph-migration.md` (Et.5).
- **Por que adiado:** custo real de token — premissa MVP [[project_mvp_os_models]]: não
  queimar OpenAI em validação. O fallback estático cobre regressão-zero até lá, e
  `WRITING_SEMANTIC_MEMORY=0` reverte sem deploy.
- **Como rodar barato:** embeddings 100% OS (`.env` já em `embeddinggemma-pt-br`/768, zero
  token); agente + 2 juízes em `gpt-4o-mini` (centavos) com `--limit` baixo. `core.eval
  writing`. Baselines (bloco fixo) já salvos em `eval_results/*_writing.json` (20260619_*).
- **Pré-requisito que invalida a comparação se faltar:** o `EVAL_WORKSPACE_ID`
  (`3df4167e-3e43-4050-b4a6-bf766c128228`) precisa ter `reflection_insights` **backfilados
  no Store** (`scripts/backfill_memory_store.py`) — senão os dois braços retornam vazio e o
  gate não mede nada.
- **Status:** aberto (2026-06-20). Bloqueia apenas o "cortar o bloco fixo"; a Etapa 5 está
  mergeável com o fallback ligado.

### Gate de eval + telemetria da Etapa 6 (LangGraph) — validação real

- **O quê:** (a) rodar as 11 suítes de eval real e confirmar não-regressão vs baseline
  pré-migração (`writing`/`extraction`/`rag` são o núcleo) + re-wire do Experiment Langfuse;
  (b) com Langfuse real, confirmar que a `CallbackHandler` emite **usage de cache/reasoning**
  com as keys canônicas (risco #2 da Et.6) e que o trace mostra `turn → agent → tools →
  critic (aninhado)`.
- **Por que adiado:** custo de token (suítes reais) + precisa de conta Langfuse com tráfego.
  Premissa MVP [[project_mvp_os_models]]. O código está fechado e testado sem rede (705 passed);
  isto é a validação de observabilidade/regressão que exige ambiente real.
- **Já rodado (2026-06-20):** `writing --limit 1` (caminho real, embeddings OS) →
  `saved=1/coherent=1/factual_errors=0`. **Pegou e corrigiu** um bug cross-loop latente da
  Et.3 (subagente herdava o checkpointer do pai; fix `checkpointer=False`). Falta o gate
  estatístico (N casos) + Langfuse real.
- **Se a parity de usage falhar:** escrever um callback de usage custom (o `record_usage` em
  `core/infra/telemetry.py` já tem a lógica de extração como referência).
- **Status:** aberto (2026-06-20). Não bloqueia o merge do código da migração.

### Deploy — aplicar migrations 021-024 no Supabase remoto (knowledge-evolution)

- **O quê:** as migrations `021_weight_change_log`, `022_episodic_signal`,
  `023_research_findings`, `024_playbook_overlays` (PR #26) foram aplicadas e validadas
  só no Supabase **local**. Quando o #26 for pra prod, rodar no remoto (`supabase db push`
  ou pelo runbook de deploy).
- **Por que adiado:** é passo de deploy, não de PR. O #26 está empilhado sobre o #25 —
  só vira prod depois do #25 mergear + retarget pra main.
- **Risco:** baixo — sobem limpas no local (schema/RLS/FK conferidos via psql). A diferença
  no remoto seria estado de dados, não SQL.
- **Status:** aberto (2026-06-15).

### Job `run_meta_reflection` — Item 3 (learned overlays de playbook) só tem scaffold

- **O quê:** o Item 3 da spec de knowledge-evolution entregou só a estrutura — tabelas
  `playbook_overlays`/`meta_reflection_runs` (GLOBAL) + 4ª camada no `load_playbook` +
  `GET /playbooks/{m}/layers`. Falta o **job que ESCREVE overlays**: meta-reflexão
  cross-tenant que agrega `application_log` anonimizado por `(mechanism, source)`, roda
  LLM e insere overlays de nível 3.
- **Por que adiado:** depende de **volume cross-tenant** real — sem dados acumulados de
  vários workspaces, a meta-reflexão não produz nada testável. A 4ª camada é no-op até o
  job popular as tabelas.
- **Onde está specado:** `docs/spec_knowledge_evolution.md` (Item 3, plano passo 2);
  memory `project-knowledge-evolution-spec`.
- **Ponto de entrada:** novo job em `core/tasks.py` (procrastinate) escrevendo via
  `get_supabase_service`; `meta_reflection_runs` é a trilha de auditoria já criada.
- **Gatilho:** quando houver N workspaces com outcomes suficientes por mecanismo.
- **Status:** aberto (2026-06-15) — scaffold no PR #26.

### Smoke e2e do happy-path dos endpoints de escrita novos (knowledge-evolution)

- **O quê:** os writes novos (`POST /research-findings/{id}/promote`, `/writing/{id}/close`,
  `/me/weight-changes/{id}/revert`) foram smoke-testados só nos caminhos de **erro** (401
  sem auth, 404/200 com ID inexistente — zero 500). O **happy-path** (promover um finding
  real → vira content_item; fechar sessão com turnos → extrai sinal; reverter um peso
  aplicado) nunca foi exercitado ponta-a-ponta contra dados reais.
- **Por que adiado:** exige seed de dados (finding/sessão/weight_change reais) + chamadas
  LLM (extração de sinal no close). Lógica coberta por unit tests (mocks) e erro-path
  confirmado em runtime — risco baixo.
- **Ponto de entrada:** subir backend local + JWT do `EVAL_WORKSPACE_ID`; seed via psql no
  container `supabase_db_radar_editais`.
- **Status:** aberto (2026-06-15).

### Auto-apply de pesos — sem dedup de sugestão repetida entre ciclos (Item 1)

- **O quê:** `auto_apply_suggestions` lê `current_weights` fresco a cada ciclo e aplica o
  delta. Se o LLM sugerir a MESMA dimensão (ex.: `trl +5`) em ciclos sucessivos, o peso
  acumula (+5/ciclo até o clamp 0-100). Não há dedup contra "já apliquei essa sugestão
  recentemente".
- **Por que adiado:** é *by design* pela spec ("|delta|≤5 por dimensão por ciclo") +
  reversibilidade via `weight_change_log`. Vira problema só com auto-trigger frequente.
- **Como fazer:** a própria spec prevê "após N ciclos, comparar performance do match
  antes/depois como sinal real" — ou olhar o `weight_change_log` recente da dimensão antes
  de reaplicar.
- **Ponto de entrada:** `core/weight_approval.py::auto_apply_suggestions`.
- **Status:** aberto (2026-06-15) — watch-item do PR #26.

### Explicador de mecanismo no explore (tool user-facing)

- **O quê:** tool nova no agente de explore que explica, pro usuário final, o que
  cada mecanismo significa (subvenção = não-reembolsável/risco; crédito =
  reembolsável/folga; equity = upside/troca de participação) — "o que é / o que
  muda pra você / quando faz sentido". Agrega quando o explore compara editais de
  mecanismos diferentes (que ele já faz). **NÃO** é o `load_skill` (craft de
  ESCRITA, público errado) — é texto próprio voltado a quem ESCOLHE.
- **Esforço:** baixo (~meio dia, dominado pela redação + sua revisão). Função pura
  retornando texto de um dict estático — sem DB, sem LLM, sem retrieval, sem
  eval-gate (zero superfície de alucinação). Plumbing ~1h (padrão da
  `search_edital_trechos`); o gargalo é escrever bem 3 explicações user-facing e
  revisar (é orientação sobre dinheiro público).
- **Cobertura:** `subvencao` + `investimento`(→equity) no catálogo; `credito` tem
  playbook mas nenhum edital ainda. Cobrir os 3 + None/desconhecido → genérico.
- **Ponto de entrada:** `core/llm/agent_tools/explore_tools.py` (build_explore_tools)
  + linha no `EXPLORE_AGENT_SYSTEM` (kg_match_service.py). Vocab no `mechanism` do
  index.json.
- **Por que adiado:** nice-to-have de clareza, sem dor bloqueante; priorizado
  atrás do deep_research no explore.
- **Status:** aberto (2026-06-15).

### Chat cross-dim — precisão de tema (ruído no explore, pós-Fase 2)

- **O quê:** o chat de Descoberta cross-dimensional (PR #19/#22) recupera bem,
  mas mistura ruído na resposta de um tema. Dois sintomas observados em teste
  manual ("IA em saúde", 2026-06-13):
  1. **Nó-notícia vira edital** — `web:ba26ff22d7b4` ("FINEP lança série de
     editais R$3,3bi") aparece como oportunidade; é notícia/agregador, não uma
     chamada. (O Lucas já apontou isso no Obsidian.)
  2. **Over-assignment de tema nas ICTs** — unidades fora do tema (ex.: QUÍMICA
     VERDE, ELETROQUÍMICA, EMBARCADOS sob "saúde") casam o tema. Vem do
     normalizer de tema do `build_ict_graph` afrouxado na Fase 0 (matou órfãos
     140→9 trocando precisão por recall).
- **Frentes possíveis:** (a) marcar/filtrar nó-notícia para não graduar a edital
  — triagem mais dura no `_TRIAGE_SYSTEM`/filtro do build, ou flag de
  `verificacao`; (b) revisar o over-assignment de tema das ICTs (apertar o
  prompt `_MAP_SYSTEM` do build_ict_graph ou um threshold de confiança).
  Relaciona [[Re-tagger LLM de temas por item]] (mesma raiz: precisão temática).
- **Por que adiado:** é débito de QUALIDADE DO GRAFO, não do matcher (o
  `_theme_match` está correto e recall-first é intencional no explore). Sem dor
  bloqueante — a resposta é útil, só larga.
- **Gate:** se mexer em tema de ICT, conferir o chat cross-dim e (quando a
  Fase 3 entrar) `core.eval matching`.
- **Status:** aberto (2026-06-13).

### Golden RAG expandido — 24 → ~80 queries (item 3 da auditoria 2026-06-12)

- **O quê:** o golden de retrieval (`eval_data/golden/finep.json`, 24 queries
  sintéticas, 3 editais FINEP) tem piso de ruído de ~1 query — flips por
  não-determinismo de embedding (caso q14, 2026-06-12) e ±4pp de erro em
  hit@5. Expandir para ~80, estratificado por intent (prazo/valor/
  elegibilidade/critérios/objetivo) e por fonte (FINEP+FAPESP+web), com
  queries reais mineradas dos spans de `search_edital` no Langfuse (todo
  turno de escrita real deposita queries autênticas — a matéria-prima
  acumula sozinha enquanto este item espera).
- **Por que adiado:** golden grande sem decisão pendente é capacidade
  esperando pergunta — reranker fechou, bake-off de embeddings foi
  desaconselhado, metadata boost era de baixo risco. Fundação do aparato de
  decisão, não do produto.
- **Gatilho para retomar:** próxima decisão de retrieval com stakes reais
  (trocar modelo de embedding, mexer em chunking, tunar fts_weight/boosts)
  OU eval bloqueando um merge por inconclusivo.
- **Decisões de spec (esboço):** (a) mix real-minerado + sintético
  estratificado; (b) anti-contaminação: sintéticas NÃO geradas a partir do
  chunk-alvo (superestimam recall); (c) rotulagem: LLM propõe gold_text,
  humano valida (padrão vocab lint); (d) guard de drift: prereq da suíte
  avisa se edital do golden não tem chunks no DB (caso finep:768,
  2026-06-12, que contaminou um baseline com 8/24 nulls).
- **Ponto de entrada:** scripts/generate_golden.py + core/eval/rag.py
  (`_prereqs`); queries reais via Langfuse (spans tool_call de search_edital).
- **Status:** aberto (2026-06-12).

### Validação determinística de citações pós-draft (item 5 da auditoria 2026-06-12)

- **O quê:** o anti-alucinação da escrita é todo prompt-side
  (WRITER_AGENT_SYSTEM: "não cite anexo/artigo que você não viu no trecho").
  Check determinístico barato: regex extrai referências estruturais do draft
  ("Anexo N", "item X.Y", "Art. N") e valida contra os chunks do edital;
  referência sem lastro vira sinalização ao usuário — nunca auto-correção
  nem bloqueio (filosofia "AI drafts, humans decide").
- **Por que adiado:** o risco atual está coberto por 3 camadas (prompt forte
  + ChecklistService on-demand + revisão humana de todo draft). No beta, o
  check pegaria fabricações que ninguém está produzindo em escala — valor
  escala com volume de uso.
- **Gatilho para retomar:** primeiro caso real de citação fabricada
  reportado por usuário, OU volume de drafts a ponto de a revisão humana
  virar gargalo.
- **Decisões de spec (esboço):** (a) onde roda: 4º pass determinístico do
  ChecklistService (on-demand) é o caminho de menor atrito; validar a cada
  save_draft é a versão cara; (b) escopo inicial: só referências
  estruturais — valores R$ e datas têm falso-positivo alto, ficam para v2;
  (c) validar contra TODOS os chunks do edital no DB (o agente pode ter
  visto o trecho em turno anterior), não só os do turno corrente; (d) UX:
  badge/aviso por trecho suspeito, decisão fica com o humano.
- **Ponto de entrada:** core/services/checklist_service.py (3 passes
  paralelos via asyncio.gather — o 4º é determinístico e entra de graça);
  chunks via edital_chunks (query direta, sem embedding).
- **Status:** aberto (2026-06-12).

### Rerank cross-encoder em prod (estágio 2 do item 6 da auditoria)

- **O quê:** ligar o reranker cross-encoder (mMiniLM) em produção. Hoje prod
  roda `RERANK_BACKEND=off`; o estágio 1 (flip pra `llm` no painel, sem
  deploy) entrega +7pp de gold_recall@3 vs off. O estágio 2 troca llm→CE:
  build com `pip install .[rerank]` (+~1GB de imagem, torch CPU), warmup no
  startup do FastAPI (load lazy leva ~25s — sem warmup o 1º turno pós-deploy
  paga), conferir RAM da instância web (+0.7-1GB residente; ~US$7-10/mês),
  pesos baixados no build ou no warmup (disco efêmero re-baixa por restart).
- **Por que adiado:** na escala beta o ganho sobre o estágio 1 é marginal
  (ndcg 0.931 vs 0.870; latência empata na vCPU; API do llm custa centavos).
  Vale quando o volume crescer ou pra tirar a OpenAI do caminho crítico do
  rerank.
- **Números/gate:** benchmark 2026-06-12 no docstring de core/reranker.py e
  commit 8ebe6b7bf; suíte `reranker` + A/B na suíte `rag` (off 0.823 vs CE
  0.895 de gold_recall@3). bge-reranker-v2-m3 REPROVADO por footprint —
  não reavaliar sem GPU.
- **Ponto de entrada:** scripts/deploy.sh (bloco ENV, linha ~81, atualizar
  comentário do beta) + Dockerfile/build do serviço web + warmup em
  backend/api.py.
- **Status:** aberto (2026-06-12).

### Re-tagger LLM de temas por item (fonte-agnóstico)

- **O quê:** hoje o tema FINEP vem da taxonomia da própria fonte (que erra —
  fix de 2026-06-11) e o da web vem da extração da Descoberta; um passo de
  síntese que LÊ o texto do edital e ESCOLHE do `tema_vocab` (como a web já
  faz) uniformizaria o tagging para todas as fontes, inclusive futuras.
- **Por que adiado:** o fix da união tema∪taxonomia resolveu o caso conhecido;
  re-tagger é melhoria estrutural sem dor ativa. Complementa o vocab lint
  (`core/vocab_lint.py`): lint evolui o vocabulário, re-tagger aplica-o
  uniformemente.
- **Gate:** eval `matching` + `opportunity_type` (mexe na dimensão temática do
  HybridMatch).
- **Ponto de entrada:** pipeline/etl_process.py (síntese já lê o texto; é onde
  o re-tag custaria zero chamada extra) ou pipeline/build_knowledge_graph.py.
- **Status:** aberto (2026-06-12).

### Front-door 1a — restos do M5 (pós-PR #14)

- **O quê:** (a) streaming SSE no `/frontdoor/turn` (delta B4 da spec — hoje há
  só TypingIndicator); (b) telemetria de conversão (quantos anônimos → conta,
  em qual gate); (c) mensagem F6 "desde sua última visita entraram N fontes"
  (precisa de ranking persistido/contagem de novidades no backend);
  (d) labels PT-BR nos diffs de origem merge/documento (hoje caem no nome do
  campo; só os diffs do turno trazem label do LLM).
- **Por que adiado:** Lucas pediu a 1a inteira testável de uma vez; estes itens
  não bloqueiam o fluxo e dependem de dado de uso (telemetria) ou de backend
  novo (novidades).
- **Onde está specado:** docs/spec_frontdoor_ux.md §5 (B4), §8 (M5), §9.
- **Ponto de entrada:** backend/routers/frontdoor.py · frontend/src/app/page.tsx.
- **Status:** aberto (2026-06-11).

### Síntese de wiki — campos estruturais não invalidam o cache

- **O quê:** a wiki page copia campos estruturais do entry do índice (`themes`,
  `status`, `deadline`…) na hora da síntese, mas o cache hit do
  `pipeline/etl_process.py` reusa a página antiga inteira — mudança de
  normalização no build (ex.: fix de temas 2026-06-11) atualiza o índice mas
  deixa as wiki pages (o "card completo" do `GET /editais/{id}`) velhas até
  alguém rodar `--skip-cache`.
- **Fix candidato:** no cache hit, re-aplicar os campos estruturais do entry
  atual sobre a página lida (espelho do `_carry_forward_match_fields`, na
  direção oposta) antes de persistir no blob durável.
- **Workaround usado:** `etl_process.py --edital <ids> --skip-cache` + push
  manual das páginas pro cloud.
- **Status:** aberto (2026-06-11).

### Feedback do tester — botão no frontend (P1 launch / build-in-public)
- **O quê:** o backend já tem o canal (`POST /feedback` em `backend/auth_routes.py`
  + tabela `user_feedback`, migration 019, RLS por user_id — commit `b46b134b9`).
  Falta a peça de UI: um botão/modal persistente (ex.: canto inferior) que coleta
  texto livre e POSTa, anexando `context` leve (`{page, session_id}`) sem PII.
- **Por que adiado:** o backend fecha o valor mínimo (fundador lê via service role);
  o botão é a fricção-zero pro tester, mas é trabalho de frontend à parte. Não
  bloqueia o launch (testers podem reportar por DM/LinkedIn no day-1).
- **Ponto de entrada:** `frontend/src/lib/api.ts` (novo `submitFeedback`),
  componente compartilhado em `frontend/src/components/` + montar no layout. Reusar
  o `Modal`/toast `sonner` ([[project_frontend_conventions]]).
- **Status:** aberto (backend pronto, falta UI).

### Teto de custo global — kill-switch anti-bill-surpresa (P1 launch)
- **O quê:** o rate-limit atual (slowapi) é **por user/IP** — protege contra um
  abusador, NÃO contra o agregado de muitos testers num link público estourar o
  orçamento OpenAI. Falta um teto **global** (ex.: contador de tokens/dia no
  Postgres alimentado pelo `session_turns.tokens` que já populamos no #12 + limite
  via env `DAILY_TOKEN_BUDGET`; ao estourar, endpoints LLM degradam com 503 amigável).
- **Por que adiado:** o rate-limit por-user já é a primeira linha; o teto global é
  rede de segurança que só importa sob volume real (que ainda não existe). Mas é
  barato e dorme tranquilo num launch público.
- **Ponto de entrada:** `backend/api.py` (middleware/dependency de checagem antes
  dos endpoints LLM: `/chat`, `/draft`, `/writing/turn`, `/analyze`), leitura
  agregada de `session_turns.tokens` (custo já mensurável pós-#12). Considerar
  cache TTL pra não somar a cada request.
- **Status:** aberto (desenho esboçado, não construído).

### Wiki pages cheias → Postgres (Tier 2 do débito data-plane) — secundário
- **Contexto:** as wiki pages por-edital são ARQUIVO (`knowledge_graph/wiki/{src}/{id}.json`)
  e o Dockerfile não copia o dir → somem no cloud (FS efêmero, web≠worker). **Tier 1 já
  resolveu o crítico** (commit 3eec1201e): os 5 campos de match (`mechanism/trl_range/
  counterpart_required/eligible_entities/value_range`) foram promovidos pro index card
  (durável, Postgres) → matching restaurado. Falta o Tier 2.
- **O quê (Tier 2):** os consumidores SECUNDÁRIos ainda leem a wiki page CHEIA por arquivo
  (`wiki_page_path`): `checklist_service`, `compliance_monitor`, `opportunity_brief_service`,
  `kg_match_service` — usam `key_facts`/`key_requirements`/`objective`/`proposal_sections`,
  que NÃO estão no card. No cloud esses degradam (caem pro pouco que o card dá).
- **Fix:** mover a wiki page cheia pro store durável via o seam `kg_store` — `load_wiki_page(eid)`
  + `save_wiki_pages(map)` (blob `wiki` em kg_artifacts p/ ~dezenas de editais; tabela por-row
  se crescer a milhares). Trocar os 4 leitores de `wiki_page_path().read_text()` por
  `kg_store.load_wiki_page()`. Escritor (`etl_process`) já tem os dados — só faltaria o save.
- **Por que adiado:** secundário (não toca o discover/match, que é o core do produto e do
  build-in-public). As features afetadas são de escrita-assist. Eval-gate antes (não há suíte
  específica desses serviços; criar fixture mínima).
- **Ponto de entrada:** `core/kg_store.py`, `pipeline/etl_process.py` (escritor), os 4 leitores.
  **Status:** aberto (Tier 1 feito; Tier 2 secundário). Ver [[project_data_plane_prod]].

### BM25 — IMPLEMENTADO (2026-06-19). HyDE implementado, desativado por default

- **BM25:** substituiu `ts_rank` (FTS) como braço sparse do RRF. Implementado via
  `rank-bm25` (Python puro, sem mudança de schema). `sparse="bm25"` é o novo default
  em `retrieve_chunks`; `sparse="fts"` mantém o caminho legado. Teste offline
  (edital FINEP 768, 253 chunks): recall@5 sparse isolado 0.375→0.875. FTS legado e
  GIN index no `text_search` mantidos no schema por ora.

- **HyDE (Hypothetical Document Embeddings):** implementado em `core/retrieval/hyde.py`
  (`generate_hyde_doc`) e disponível em `retrieve_chunks(hyde=True)`. **Mantido com
  `hyde=False` como default** — eval offline mostrou regressão no braço dense isolado
  (recall@5 0.50→0.42 no golden FINEP com OpenAI embedding). Hipótese: golden anotado
  por `section/source_file` favorece vocabulário literal; HyDE move o vetor para
  paráfrases formais que divergem das anotações. Re-avaliar com pipeline completo
  (rerank ativo, `core.eval rag`) e com embedding Gemma antes de ativar em prod.
  Modelo configurável via `HYDE_MODEL` / `HYDE_BASE_URL` / `HYDE_API_KEY` (Ollama-ready).
  Script `eval_embedding_offline.py` aceita `--hyde` para testes isolados.
- **Status:** BM25 em prod. HyDE na branch, `hyde=False` até nova avaliação.

### Parsing/chunking estrutura-aware — INVESTIGADO E REFUTADO (benchmark-driven, 2026-06-06)
- **Hipótese:** parser estrutura-aware (Docling p/ PDF, numbering p/ FAPESP texto-plano)
  → modelo de blocos tipado → melhor `section_path` → melhor retrieval. Motivada pela
  patologia de "unit gigante" (FAPESP texto_cru achatado → units de ~24k chars).
- **Metodologia (no harness existente):** métrica **chunking-invariante** — token-recall
  sobre `gold_text` (estilo Chroma) em `core/eval/metrics_rag.py` (`gold_recall_at_k`,
  `gold_best_chunk_recall_at_k`); golden FINEP (24q) + FAPESP (15q) regenerados com
  `gold_text`. Benches em `scripts/bench_parsing.py` / `bench_fapesp.py` / `bench_contextual.py`
  (dense-only, isola a variável chunking).
- **Resultado: SEM ganho de retrieval.**
  - FINEP (Docling vs baseline pdfplumber+structurer-LLM): Δ gold_recall@5 **−0.005**, best_chunk@5 −0.012.
  - FAPESP (numbering-determinístico vs baseline): Δ gold_recall@5 **−0.020**, best_chunk@5 −0.055.
  - O baseline atual (units → structurer-LLM → `chunk_from_blocks`) já é forte (FINEP
    recall@5=0.92, FAPESP=1.0) **mesmo com as units de 24k** — a "patologia" não degrada o
    retrieval mensurável. O LLM structurer recupera estrutura bem.
- **Viés conhecido (documentado):** `gold_text` = texto de um chunk do baseline → vantagem
  de casa pró-baseline (sobretudo em best_chunk). Na métrica menos enviesada (union recall)
  é **empate**. De toda forma, nenhum alternativo supera.
- **Decisão:** NÃO rearquitetar parsing/chunking (sem contrato de blocos tipados, sem Docling
  no edital, sem structurer-determinístico). Código fica como **experimento arquivado**:
  `pipeline/adapters/base.py` (`split_by_numbering`, `blocks_from_typed`, `blocks_from_numbered_text`),
  `pipeline/parsers/docling_blocks.py`, benches. **Docling NÃO é dependência** (não está no
  pyproject; só os benches o importam lazy — `pip install docling` p/ rodá-los).
- **ADOTADO desta frente (rendeu):**
  - **Contextual Retrieval** (Anthropic) — único lever com ganho medido (+1-2pp consistente,
    FINEP). Wired em `core/tasks.py::chunk_edital_task` via `core/contextual_retrieval.py`
    (contexto-no-chunk antes do embed; coluna `text` segue original; gateado por content_hash;
    `CONTEXTUAL_RETRIEVAL=false` desliga). **Pendência operacional:** reindex do catálogo
    (`scripts/reindex_edital.py --all --force`) p/ contextualizar tudo (hoje só finep:779).
  - **Upload multi-formato** (docx/txt/md+pdf) — `core/content_library.extract_document_text`,
    nos endpoints de upload da library e `/profile/extract-from-document`. Resolve o gap real
    (cliente sobe proposta `.docx`).
  - **Infra de eval**: token-recall chunking-invariante + `core/eval/metrics_parsing.py` (métricas
    intrínsecas) + `gold_text` no `generate_golden`.
- **Revisitar SÓ se:** (a) corpus de **docs do cliente** (heterogêneo, multi-formato) for
  benchmarkado com docs reais — é onde a tese de parsing PODE render (editais são estruturados
  demais p/ mostrar diferença); (b) aparecerem queries table-specific (onde Docling 97.9% de
  tabela poderia ganhar). Ver também [[project_data_plane_prod]] (precedente Stage 2a: experimento parado).

### Chunking de docs do cliente (content_items) — adiado p/ benchmark com docs reais
- **O quê:** hoje `content_items` é embeddado summary-level (M9: `title+summary+content[:6000]`,
  1 vetor/item); conteúdo completo É armazenado (sem perda) e acessível ao Redator. Chunkar
  docs do cliente daria retrieval em nível de PASSAGEM (tabela `content_chunks` + path próprio).
- **Por que adiado:** é feature (não bug), toca o M9 (matching usa summary-level de propósito),
  e o benchmark precisa de docs de cliente REAIS (inexistentes pré-launch). Decidido medir
  quando testers subirem documentos.
- **Status:** adiado conscientemente.

### Budget builder mínimo (paridade Grantable "Build budget")
- **O quê:** planilha de orçamento por aplicação — line items que o usuário digita,
  validados contra o envelope do edital. **Não sugere valores** (filosofia
  "humans decide" / [[project_grantable_philosophy]]); só valida o que o humano põe.
- **O schema já dá o envelope:** a extração captura `funding_amount` (teto),
  `counterpart {required, percentage}` (contrapartida) e `mechanism`
  (`core/edital_extractor.py`). Falta a estrutura interna (rubricas) e a tabela de items.
- **Desenho (3 peças):**
  1. **Tabela nova** `application_budget_items` (filha de `application_log`, que já é o
     header e carrega `edital_id` → herda teto/contrapartida da wiki page):
     `rubrica` · `descricao` · `valor numeric(14,2)` · `origem ('solicitado'|'contrapartida')`.
     RLS espelha o padrão de `content_items`/`application_log` (migration 004).
  2. **Validador puro** (sem LLM, pegada do `core/profile_drift.py`): soma items, lê
     `funding_amount`+`counterpart.percentage` da wiki page, devolve flags
     (estouro de teto · contrapartida insuficiente · rubrica vedada).
  3. **Rubricas permitidas:** v0 hard-coded num skill `skills/finep_budget.md` (padrão
     dos compliance skills); v1 extrai `allowed_rubricas` da seção de orçamento via
     `edital_chunks` → novo campo na wiki page (`core/wiki_schema.py` + golden).
- **Por que adiado:** priorizado pipeline UI + perfil-de-proposta antes. Dificuldade
  real ~4 (a tabela é filha do log existente; único trabalho de IA = rubricas, opcional na v0).
- **Ponto de entrada:** `supabase/migrations/` (nova tabela), `backend/api.py` (CRUD +
  GET com flags), `core/` (validador), frontend nova tela de planilha.
- **Guard-rail:** o builder NUNCA preenche valores — só valida. Teste de aceitação por grep.
- **Status:** aberto (desenho fechado, não construído).

### ProfileExtractor — `faturamento_anual` raramente extraído do texto
- **O quê:** o `extract_from_text`/`_call_llm` ([core/profile_extractor.py](../../core/profile_extractor.py))
  extrai bem `uf`/`ano_fundacao` mas perde `faturamento_anual` mesmo quando o texto
  o afirma. **Evidência (walkthrough 2026-06-06):** proposta com "Faturamento anual
  R$ 2 milhões" → `uf='SP'` e `ano_fundacao=2019` vieram `high`, mas
  `faturamento_anual` veio `missing`/None.
- **Por que importa:** é um dos 3 campos thin-profile (o "teto do matching"). Agora que
  a cadeia UI/save foi fechada (o campo flui de ponta a ponta), o gargalo restante é
  só a extração. Mitigação atual: o usuário digita no campo novo do onboarding.
- **Ponto de entrada:** prompt `_EXTRACT_SYSTEM`/`_EXTRACT_USER` e o schema de saída em
  [core/profile_extractor.py](../../core/profile_extractor.py) — instruir a capturar valores
  monetários (R$ X milhões → número) e normalizar a escala. Medir com um caso de golden
  de extração de perfil (não existe ainda — criar fixture mínima).
- **Status:** aberto (qualidade de extração, não wiring).

### Extração v2 — itens adiados da curadoria + "perfil é o teto do matching"
- **Insight central:** o teto do matching é o `CompanyProfile` (fino), NÃO o schema
  do edital. Adicionar campo de decisão no edital só rende se houver o PAR no perfil.
  Criados `uf`/`faturamento_anual`/`ano_fundacao` no perfil. **FEITO (2026-06-05):**
  `profile_extractor`/`submit_profile`/`lookup_cnpj` preenchem os 3 campos, e o
  Stage 1 tem a dimensão soft `elegibilidade_dura` (região/idade/faturamento).
- **Pendente desta frente:** a dimensão nasce **DORMENTE** — os cards de prod ainda
  não carregam `eligibility_constraints` (vêm dos normalizadores, não do extrator v2).
  Ela liga sozinha quando a Fase 3 do extrator popular o campo no card pipeline.
  Até lá, ranking idêntico ao legado (provado por teste + eval).
- **Adiados (curadoria Gemini+ChatGPT reconciliada com o código):**
  - **Gate sobre `eligibility_constraints`** (região/idade/faturamento): wirado como
    dimensão SOFT no Stage 1 (nunca elimina). Falta só o extrator v2 popular o campo
    no card (Fase 3) para a dimensão sair da dormência.
  - **`call_type`** (business_innovation|academic|grant|procurement|challenge):
    tipologia que muda a interpretação dos campos. Menos urgente — o `pme_filter`
    já descarta bolsa/acadêmico upstream.
  - **`absent` → `not_found`/`not_applicable`**: refinar a abstenção (over-eng p/ agora).
  - **`confidence` numérico** por campo (monitoramento/active-learning).
  - **CNAE / consórcio** como pares perfil↔edital.
  - **Versionar retificação ("Aditivo 01")**: atualizar campo específico vs reprocessar.
- **Onde:** [spec_extraction_schema.md](extraction-schema.md), domain/edital_extraction.py,
  domain/user_profile.py. **Status:** aberto.

### Eval — sincronizar golden como Langfuse Dataset
- **O quê:** o harness já manda cada run para `langfuse.run_experiment` (com
  LANGFUSE_* setadas), agrupado por nome da suíte. Melhoria: criar/sincronizar o
  golden de cada suíte como um **Langfuse Dataset** e rodar os experiments contra
  ele → compare **por-item** entre runs (e não só agregados por nome).
- **Por que adiado:** o agrupamento por nome já entrega search/compare/present
  básico; Dataset é refino.
- **Ponto de entrada:** `core/eval/harness.py::_run_langfuse` (hoje passa `data`
  local) → adicionar `langfuse.create_dataset`/`create_dataset_item` idempotente
  por suíte. **Status:** aberto.

### Extração em produção — subir o tier da OpenAI (TPM)
- **O quê:** o tier atual tem TPM (tokens/min) = 30k. Editais FINEP, mesmo após
  selecionar só o edital (`_finep_edital_text`, sem anexos/duplicatas), batem
  ~10-15k tokens/extração. Para o golden (10 editais) os retries do SDK seguram
  (~3 min), mas para PRODUÇÃO (centenas de editais, 1×/cada no ETL) o lote fica
  lento/arriscado no rate-limit.
- **Por que adiado:** o golden roda bem no tier atual; só vira gargalo ao
  produtizar o extrator (Fase 3/4).
- **Ponto de entrada:** subir o tier no painel OpenAI (quase imediato após
  uso/pagamento) OU paralelizar com throttle respeitando o TPM. `core/edital_extractor.py`
  já tem `max_retries=6` + `RAW_CAP` via env como mitigações.
- **Status:** aberto.

### Ingestão web → matching (Descoberta Fase B)
- **O quê:** produtizar a ingestão de editais da web (discovery) no matching de prod.
- **Feito (Fase 1 ROADMAP, 2026-06-10):** wiring do feeder DOU atrás de
  `DISCOVERY_DOU_ENABLED` (busca D-1 UTC; spec_dou_feeder §6), reescopo do Tavily
  pras zonas não-DOU (§6.1), badge "não verificado" pro `provisorio`
  (índice→match→radar→card; política: rotular, não filtrar).
- **Em curso:** **shadow-run** local antes de ligar em prod — runbook e critério
  de graduação em `spec_dou_feeder.md` §9. Ligar = setar envs no .env do Docker Compose.
- **Status:** shadow-run pendente de rodar (~1 semana de runs).

### Triagem da Descoberta — 13 labels do golden aguardam decisão de persona
- **O quê:** o golden da suíte `triage` (`eval_data/golden/triage.json`, 122
  candidatos reais de 2026-06-10) tem **13 casos `review: true`** — rotulagem
  inicial por auditoria, mas a palavra final é decisão de PRODUTO, não de código.
- **As 2 decisões principais:** (a) **hub de desafio corporativo perene**
  (Tupy, beOn Claro) conta como oportunidade Q2 ou só desafio com chamada
  datada? (b) **credenciamento de incubadoras** (FAPESP 2026) — chamada p/
  incubadoras, não startups: oportunidade indireta conta pra persona deep-tech?
  Os outros 11: notícia recente de edital específico × política anti-notícia;
  vigência incerta. Cada caso tem `note` explicando o dilema.
- **Como resolver:** editar `expected` no JSON e re-rodar
  `python -m core.eval triage` (baseline atual: accuracy 0.8525, fn_guard
  0.9836 — os 2 FNs são exatamente casos em review).
- **Por que importa:** o A/B de 2026-06-10 (snippet × reason × content) provou
  que mudança de input só move erros de lugar; sem golden estável, nenhuma
  melhoria de triagem é decidível.
- **Status:** aguardando revisão do fundador.
- **Caso concreto novo (2026-06-11, teste em prod):** a notícia agregadora
  "FINEP lança série de editais com R$ 3,3 bilhões" (web:ba26ff22d7b4,
  `provisorio`) passou na triagem apesar do fix anti-página-lista do dia 2 —
  é notícia sobre VÁRIOS editais que já temos individualmente via API (duplica
  e polui o tema agro). Adicionar ao golden como `expected: reject` quando o
  golden for revisado; reforça a regra "1 URL = 1 oportunidade".

### Descoberta web — `titulo` vazio na extração (UX do card)
- **O quê:** no dry-run de 2026-06-09 (`discover_opportunities(write=False)`), os 23
  candidatos extraídos vieram com `titulo` **vazio** (incl. editais), apesar de
  `tema`/`status` populados — `_extract`/`_page_text` não capturam o título da página
  web crua. Card sem título é UX ruim.
- **Por que importa agora:** não bloqueia ativar a torneira web, mas precisa estar
  resolvido **antes de ligar `write=True` em prod** (senão entram editais sem título
  no índice/match).
- **Fix mínimo:** fallback `titulo ← hit.title` (título do resultado de busca, sempre
  presente) quando `_extract` devolver vazio. Fix melhor: investigar por que o título
  não sai da página (provável débito da qualidade do chunk HTML — ver entrada de
  parsing/chunking HTML).
- **Onde:** `core/opportunity_discovery.py` (`_extract`, `_page_text`).
- **Revisão (2026-06-10):** o fallback `or hit.title` EXISTE desde a origem do
  engine (`_extract`, linha do `"title"`), e o parsing do Tavily preenche
  `hit.title` — por leitura, o sintoma não se explica. Hipótese: o dry-run
  inspecionou a chave `titulo` (o registro usa `title`). Hits DOU trazem
  `Identifica` como título (robusto).
- **Status:** sem ação de código; **revalidar no shadow-run** (spec_dou_feeder §9
  inclui "% de title não-vazio" na inspeção). Reabrir só se o dado real reproduzir.

### Grafo induzido (GraphRAG) — overlay de insight, NÃO base do match
- **O quê:** uma **Camada B** induzida sobre o grafo curado — extração livre de
  entidades/relações + detecção de comunidades + sumarização — como **overlay
  batch** sobre corpus delimitado, alimentando uma **superfície de insight
  separada** (não o match/escrita). **Alvo 1 (barato):** rede de fundos do Q3
  (co-investimento, sobreposição de tese, quem segue quem; corpus ~30-50 →
  trivial e não-óbvio). **Prêmio maior:** insight longitudinal cross-quadrante
  ("memória da evolução das agências").
- **Por que adiado:** indução é cara ∝ corpus e **hostil ao ETL incremental**
  (doc novo desloca comunidades → re-sumariza). O loop central (descobrir→match→
  escrever) é servido pela **Camada A CURADA** — indução é feature de
  *profundidade*, não de *robustez*, logo **fora do MVP** ([[project_multi_quadrante]]).
- **Binding obrigatório (a parte que não pode faltar):** a saída induzida só vale
  se **ligar às wiki pages curadas**. Template = **contrato de reconciliação**:
  emitir nós/arestas **tipados** (`node_type`/`link_types` existentes ou flag
  candidato-novo) e **resolver cada ponta a id canônico** (`investidor:kptl`, não
  nó solto). Reusa `themes_proposed` (quarentena) + `verificacao: provisorio`
  (gate de graduação). Sem o contrato → segundo grafo desconectado, insight
  flutua, custo desperdiçado.
- **Onde está specado:** `docs/spec_multi_quadrante.md` §3.9 (e §3.8 sobre o
  GraphRAG curado no Stage 2 de tese).
- **Status:** adiado (pós-MVP). Alvo 1 (rede de fundos) destrava barato assim que
  o `node_type investidor` existir (Fase C).

### Matching Stage 2a — scoring por embeddings em vez de geração-LLM
- **O quê:** o Stage 2a (pontuação temática de TODOS os elegíveis) é hoje uma
  chamada generativa ao LLM que devolve `{id: score}`. Foi construído um caminho
  alternativo de **similaridade vetorial determinística** (cosseno perfil × edital).
- **Veredito (2026-06-05): PARADO.** Construído atrás de flag `MATCH_STAGE2A_BACKEND`
  (default `llm`), mas **não vale a pena perseguir** — dois motivos que se somam:
  1. **Economia no eixo errado.** O custo do 2a escala com nº de *requests* de match
     (user-initiated, baixa frequência), NÃO com o catálogo (vão batched numa chamada).
     ~$0.0004/match no gpt-4o-mini → ~$4/mês a 10k matches. Otimizar isto é ruído.
  2. **Latência/disponibilidade não some.** O Stage 2b (explicação do top-K) continua
     sendo chamada-LLM no MESMO request → trocar só o 2a não tira o LLM do caminho
     crítico. Troca precisão por quase nada operacional.
- **Experimento (`core.eval matching`, mesmo golden, 2 casos):** LLM p@3=0.834/p@5=0.800
  vs Embeddings p@3=0.500/p@5=0.600 → embeddings perde. (Fixture minúscula → confiar só
  no *sinal* "não ganhou", não nos números.) `objective` ausente (0/34) handicapa o
  embedding, mas o gap é grande demais p/ o campo explicar sozinho.
- **Revisitar SÓ se o shape do produto virar:** (a) **pré-computar matches em lote**
  (cron varrendo perfis × editais → volume N×M, custo/item passa a importar), ou
  (b) catálogo na casa dos milhares com match em alta frequência, ou (c) exigência de
  ranking 100% determinístico por princípio (não por custo). Nenhum é a forma atual.
  Se revisitar: golden de matching MUITO maior + considerar híbrido (embedding
  pré-filtra, LLM rankeia o topo) em vez de substituir.
- **Ponto de entrada (já existe):** `core/match_embeddings.py` (cosseno summary-level,
  cache file-based, sem tocar `retriever.py`/ADR M9); roteamento em
  `core/hybrid_match_service.py::_call_stage2_scores`. Embeddar = `title`+`objective`+
  `themes` × pitch do perfil.
- **Status:** parado conscientemente. Código vive atrás da flag a custo zero.

### Matching — fit-forte sub-rankeado (ex.: finep:612 / iFlorestal)
- **O quê:** com o Stage 2 já consertado, `finep:612` (tema "agro - bioeconomia",
  fit forte com o perfil iFlorestal, elegível com Stage1 score=60) ainda não
  entra no top-5; o `expected_hit` da suíte matching segue 0 para esse caso.
- **Por que adiado:** não é bug — é tuning de qualidade. Pode ser peso do Stage 1
  (score 60 mediano dilui o fit temático na combinação 60/40) ou a expectativa da
  fixture estar desatualizada para o catálogo atual.
- **Ponto de entrada:** investigar o `score_tematico` que o Stage 2 dá a
  finep:612 vs o breakdown do Stage 1; revisar pesos (`matching_weights`) ou a
  expectativa em `tests/fixtures/eval_matching.json`. Medir com `python -m
  core.eval matching`.
- **Status:** aberto.

### ICT — Fase C.2: ICT na escrita (peça 4)
- **O quê:** quando o usuário escolhe um parceiro ICT na tela do grafo, importá-lo
  para a ContentLibrary do workspace (`create_item(type_='ict_partner', …)`) para
  que `search_library` o use ao escrever a seção de parceria — com proveniência.
- **Por que adiado:** Fase C.1 (matchmaking) entrega o valor central; a escrita é
  extensão. Depende de UI (botão "selecionar parceiro" na tela do grafo).
- **Onde:** [spec_ict_phase_c.md](ict-phase-c.md) peça 4 / fase C.2.
- **Ponto de entrada:** endpoint `POST /library/from-ict` + reuso de
  `create_item`/`enrich_content_task`/`search_library` (tudo já existe).
- **Guard-rail (não violar):** o Redator **não** recebe `find_ict_partners` nem lê
  `icts.json`. ICT entra na escrita só via decisão humana → library. Sugestão ≠
  compromisso. Teste de aceitação por grep.
- **Status:** aberto.

### ICT — Fase B: fonte PNIPE/MCTI
- **O quê:** segunda fonte de ICTs — laboratórios do [PNIPE](https://pnipe.mcti.gov.br/search)
  (metadados ricos: Sobre, Endereço, Contato, área de atuação, técnicas).
- **Por que adiado:** PNIPE é grande e ruidoso (toda a infraestrutura de C&T do
  país) → exige estratégia de filtro/paginação antes de entrar no grafo. EMBRAPII
  (Fase A) já provou o tipo de nó end-to-end.
- **Onde:** [spec_ict_mapping.md](ict-mapping.md) Fase B.
- **Ponto de entrada:** `pipeline/extractors/ict_pnipe.py` (espelha
  `ict_embrapii.py`); dedup cross-source já existe em `build_ict_graph` (por nome
  normalizado). Verificar se a busca é client-side (pode exigir Playwright).
- **Status:** aberto.

### Descoberta de Oportunidades (item 2.2) — Fase B + graduação (A e C feitas)
- **Feito (Fase A):** engine `core/opportunity_discovery.py` (web_search → triagem
  → extração → bronze + ledger de dedup). Vocab em `wikis/_discovery.md`.
- **Feito (Unificação Opção A + Fase C/recorrência):** a Descoberta deixou de ter
  bronze/índice próprios — virou a **torneira automática da fonte `web`** (WIKI.md
  §12.4). Grava `web_raw/web_discovery_*.json` no schema web (`url_hash`/`texto_cru`/
  `verificacao=provisorio`), entra pelo `_build_editais("web")` e **é chunkada pro
  RAG** pelo adapter web — fechando o gap "provisório de snippet = escrita rasa".
  Removidos `_build_discovery_editais`/`_normalize_discovery`/`load_discovery_bronze`.
  Identidade em `core/web_identity.py`. Task procrastinate `discover_opportunities`
  + cron diário 04:00 UTC (busca → web_raw → enfileira chunk → rebuild). Ledger
  file-based mantido. **Pré-requisito de uso:** `TAVILY_API_KEY` + chave LLM.
- **Fase B (aberto):** verificação humana não-bloqueante — endpoint verificar/
  rejeitar, match/escrita distinguindo provisorio×verificado (rótulo/bucket — item
  3 das decisões: bucket no MVP), aviso de fonte não-verificada na escrita. O eixo
  `verificacao` já é por-item no índice; falta a UI/API e o ranqueamento no match.
- **Graduação (aberto):** fonte recorrente de formato estável → extractor próprio
  no `SCRAPER_REGISTRY`/§12.4 (sai de `web:provisorio` → `<fonte>:verificado`). O
  campo `agency` já é preservado no bronze web para alimentar isto. Ledger
  file-based pode graduar para Supabase se virar multi-worker.
- **Onde:** [spec_descoberta_oportunidades.md](discovery-opportunities.md).

### DeepResearch — Fases B e C (Fase A feita)
- **Feito (Fase A):** `core/web_search.py` (port Tavily REST), `core/deep_research.py`
  (subagente run_agent + anti-fabricação), tool `deep_research` no Redator. Stateless,
  não persiste. Falta `TAVILY_API_KEY` no ambiente para uso real.
- **Fase B (FEITO — PR #26, 2026-06-15):** entregue como `research_findings` (staging
  table, verified=false) + `build_research_tools(workspace_id, db)` persistindo silencioso
  + `GET /research-findings` + `POST /research-findings/{id}/promote` (cria content_item,
  type='other'+tag deep_research) + `ResearchFindingsQueue` na library. Difere do shape
  esboçado aqui (`/library/from-research`/`web_research`) mas cumpre a mesma intenção: o
  fato escolhido vira memória via gate humano de baixo atrito. Guardrails: cap de pendentes
  (`RESEARCH_FINDINGS_MAX_PENDING=50`) + TTL 30d (filtro no GET).
- **Fase C (aberto):** decay por tipo (`web_research` com meia-vida menor) + tool no
  Explorador + eval anti-fabricação (casos cuja resposta certa é "não encontrei").
- **Onde:** [spec_deepresearch.md](deep-research-design.md).
- **Pré-requisito de uso:** configurar `TAVILY_API_KEY` (e `WEB_SEARCH_BACKEND=tavily`,
  default). Sem chave, a tool degrada com mensagem.

### RAG — golden `finep` com sections obsoletas (brittle a re-chunk)
- **O quê:** a suíte `rag` casa `expected` por `source_file` + `section` EXATA
  (`core/eval/metrics_rag.py::_matches`). O golden `eval_data/golden/finep.json` (03/06) tem
  13/24 queries com `section` específica que **não existe mais** nos chunks atuais —
  os editais foram re-chunkados e as labels de section deslocaram. Resultado: hit@5
  aparenta 0.50 mesmo o retriever acertando o PDF no rank 1.
- **Medição real (2026-06-05, golden relaxado p/ nível-PDF):** hit@5=**1.00**, hit@3=0.92,
  RR=**0.845**, faithfulness=4.71, null_result=0 → **retriever validado em nível de
  documento.** O número section-level estava medindo gabarito quebrado, não retrieval.
- **Ações:** (a) regenerar o golden contra os chunks atuais
  (`scripts/generate_golden.py --source finep --editais 768 762 743`) + revisão humana
  (passo crítico, ver docstring do script); (b) decidir o design durável do match: a
  igualdade EXATA de section é frágil a qualquer re-chunk — considerar matching em
  nível de PDF (robusto, coarser) ou section fuzzy/normalizada. Golden relaxado em
  `eval_data/golden/finep_relaxed.json` (sections nulladas) serve de baseline interino.
- **Status:** retriever OK; golden a regenerar. Não bloqueia hospedar.

### Escrita — eval validado; resíduos de fixture/juiz/TPM
- **Resultado (2026-06-05, fixture curada + juízes gpt-4o):** saved=1.0, coherent=1.0,
  **pct_grounded=0.917**, factual_errors=0.33/caso. Agente de escrita VALIDADO (grounding
  alto = afirmações ancoradas nos chunks). O baseline aparente "0.32" era 100% confound:
  pares perfil↔edital mismatch + métrica 0-claim=0.0 + juiz em gpt-4o-mini.
- **Resíduo `factual_errors=0.33`** = lacuna de fit perfil↔CATÁLOGO, não fabricação. O
  catálogo FINEP local não tem edital que case de verdade com iFlorestal (nicho florestal);
  o tema canônico "agro-bioeconomia" é grosso, mas as linhas específicas (agro/alimentos/
  energia/cidades) não cobrem monitoramento florestal. **Ação:** adicionar à fixture de
  escrita um par perfil↔edital com fit temático REAL (ex.: perfil agro/alimentos × 774, ou
  energia × 772) para medir escrita sem a penalidade de mismatch estrutural.
- **Juiz da escrita EXIGE gpt-4o, não mini:** o gpt-4o-mini extrai 0 claims de rascunhos de
  2500+ chars e flagra fits legítimos como mismatch → mede o juiz, não a escrita. Mas
  gpt-4o-juiz + gpt-4o-agente disputam os **30k TPM** da OpenAI (mesmo gargalo do
  [tier OpenAI]). Saídas: subir tier, OU rodar o agente na Anthropic (libera o TPM OpenAI
  pros juízes). Hoje contornado com `LLM_MAX_RETRIES` alto + fixture pequena (6 casos).
- **Feito nesta sessão:** fixture curada (removidos 772/777 mismatch), métrica de grounding
  exclui casos 0-claim (`eval_grounding`→None; novo score `n_claims`), prompt do redator
  ganhou regra anti-fabricação de referências (anexos/artigos não-vistos). 378 testes verdes.
- **Status:** escrita validada p/ beta; resíduos são rigor de fixture/infra, não bloqueiam.

### RAG/Escrita — editais SEM PDF: código RESOLVIDO; pendência é OPERACIONAL (cloud)
- **Correção (2026-06-08):** o item antigo dizia que `chunk_edital`/`_build_chunks_for_edital`
  chunkam "só PDF" — **FALSO hoje.** O refactor source-agnostic (2026-06-06,
  [[project_source_agnostic_chunking]]) trocou o caminho por `get_adapter(source).to_documents(native)`
  → o adapter FAPESP devolve o texto do registro/HTML, passa por `chunk_from_blocks → embed →
  upsert` igual ao PDF. Localmente FAPESP **tem** chunks (bench mediu 104 em 2 editais). Não há
  fix de código a fazer aqui.
- **O que sobra (operacional, não código):** o **cloud** está com `edital_chunks` de FAPESP
  **vazio** porque o `chunk_edital` nunca rodou pra FAPESP lá: o pré-aquecimento (2026-06-05) é
  ANTERIOR ao refactor, e a `bronze_data/` (fonte FAPESP) **não está na imagem** (Dockerfile não
  a copia) → re-chunkar no cloud só funciona após o ETL/scrape rodar lá, OU rodando local
  (com bronze) apontando o Supabase pro cloud. **Ação:** `reindex_edital.py fapesp:18067` /
  `fapesp:18203` com creds do `.env.cloud`.
- **Edge remanescente — `finep:613`** ("Programa de Investimento em Startups"): veio sem anexo,
  só título+descrição (~957 chars). Chunka pouco, mas o card já cobre. Baixa prioridade.
- **Status:** código resolvido; falta rodar a indexação FAPESP contra o cloud (em andamento).

### RAG — melhor estratégia de chunkeamento para fontes HTML longas
- **O quê:** o chunking de fontes HTML (FAPESP `html_body`, web `html_clean` —
  edital inteiro num corpo único de texto) gera chunks de qualidade irregular.
  Investigar uma estratégia melhor antes de escalar fontes web.
- **Evidência (fapesp:18203, texto_cru de 103.859 chars → 50 chunks):**
  1. **Units gigantes quando falta `\n\n`:** `split_into_units` (base.py, alvo
     ~3500 chars, quebra por parágrafo) emitiu 2 units de ~23.8k chars porque o
     início do corpo não tem quebra de parágrafo (bloco denso de menu/cabeçalho).
     Unit de 24k força o structurer a um output enorme → risco de timeout (o
     próprio comentário do adapter FAPESP avisa). Falta um fallback de split por
     sentença/comprimento quando o parágrafo é grande demais.
  2. **Micro-chunks por fragmentação de seção:** doc heading-denso (139 headings
     em 444 blocos) → o `chunk_from_blocks` fecha chunk em CADA fronteira de
     `section_path`, gerando chunks de 9-84 chars ("só o título"). A regra de
     merge de `MIN_TOKENS` só funde dentro da MESMA seção, então headings órfãos
     viram poeira no índice (ruído no retrieval, dilui o top-k).
- **Direções a avaliar:** (a) split estrutura-aware na fronteira HTML antes do
  structurer (âncoras `<h1..h3>`, `§12.3` já prevê "split por âncora") em vez de
  char-count cego; (b) merge de chunks órfãos sub-`MIN_TOKENS` ATRAVÉS de
  fronteira de seção quando o chunk anterior/seguinte é da mesma raiz; (c)
  anexar heading ao corpo da seção em vez de virar chunk próprio; (d) medir o
  impacto no `core.eval rag` (golden FAPESP) antes/depois — não tunar às cegas.
- **Por que adiado:** não bloqueia o beta (FINEP+FAPESP rendem retrieval útil
  hoje, 19/20 vigentes com chunks); mas é dívida que cresce com o nº de fontes
  HTML (web genérica + Descoberta entram pelo mesmo `html_clean`).
- **Ponto de entrada:** `pipeline/adapters/base.py::split_into_units`,
  `core/chunker.py::chunk_from_blocks` (regra de fronteira/merge), `core/eval/`
  suíte `rag`. **Status:** aberto.

### Descoberta — dedup cross-fonte (mesma oportunidade, URLs diferentes)
- **O quê:** o ledger da Descoberta dedupa por URL normalizada (`_norm_url` em
  `core/opportunity_discovery.py`). Mas a MESMA oportunidade chega por fontes
  diferentes com URLs diferentes — `pdfPage` do DOU (feeder Fase A) ≠ página HTML
  da agência (achada pelo Tavily). Dedup por URL **não pega** esse duplicado →
  dois `web:<url_hash>`, dois nós no grafo, duplicata no radar.
- **Decisão de fonte (já specada):** quando há overlap, **DOU vence** (canônico,
  estruturado); DOU-sourced pode nascer com `verificacao` > `provisorio`.
  Ver `docs/spec_dou_feeder.md` §6.1.
- **Mitigação imediata (barata): FEITA (2026-06-10)** — queries do Tavily
  reescopadas em `wikis/_discovery.md` pras zonas que o DOU NÃO cobre
  (FAPs/estaduais, desafios open-innovation, Q4 aceleradoras; Q3 VC ficou fora —
  investidor é diretório curado). O Tavily deixou de re-varrer o federal.
- **Solução durável:** dedup semântico (título+órgão+nº do edital, ou
  similaridade) + prioridade de fonte no merge. Casa com o item de proveniência
  abaixo e com `verificacao`.
- **Por que adiado:** só morde quando DOU + Tavily rodam juntos no federal; a
  mitigação imediata (encolher Tavily) já segura o MVP. **Ponto de entrada:**
  `core/opportunity_discovery.py` (`_known_urls`/`_norm_url`/ledger), `wikis/_discovery.md`
  (queries do Tavily). **Status:** aberto (destrava quando a flag `DISCOVERY_DOU_ENABLED` ligar).

### DOU — sync de ciclo de vida (retificação/prorrogação/encerramento → temporal)
- **O quê:** o DOU não anuncia só abertura — anuncia o ciclo inteiro (826 atos/dia
  em DO1+DO3, 2026-06-09): Retificação (errata), Prorrogação (prazo estendido),
  Alteração, Suspensão, Revogação, Republicação, Resultado/Homologação
  (encerramento). É um **stream de ciclo de vida por oportunidade**, não só
  descoberta. Capturar esses atos e atualizar a oportunidade no radar:
  prorrogação→novo prazo; suspensão/revogação→status; resultado→ENCERRADA.
- **Por que importa:** `core/temporal.py` hoje infere status de "prazo < hoje". O
  DOU é a fonte **autoritativa** das transições reais (prorrogação muda o prazo;
  suspensão muda o status independente do prazo). Radar com prazo errado é inútil
  → manter editais VIVOS é multiplicador de robustez. Casa com a memória
  longitudinal das agências.
- **IDENTIDADE é o nó duro (Option B `nº+órgão` TESTADA E REPROVADA, dry-run
  2026-06-09):** a hipótese de id estável `<órgão>-<nº>-<ano>` colide em massa — o
  "Nº N/ANO" do DOU é escopado por unidade(UASG)+tipo de ato, não por ministério
  (`ministerio-da-educacao-1-2026` fundiu 50+ atos distintos). E a ligação
  retificação→original mora no TEXTO do corpo ("retifica-se a publicação de DD/MM,
  pág. X"), não no título. Logo a identidade de ciclo de vida exige **(a)** org
  unit-level (artCategory completo, não topo) **+ (b)** parse de cross-referência
  no corpo do ato — é parte DESTE problema, não um pré-requisito barato.
  Descoberta/dedup continua no `url_hash` (correto, único por aviso).
- **Por que adiado:** identidade unit-level + parse de cross-ref no corpo é
  trabalho real; e o rendimento de ciclo de vida de FOMENTO (vs licitação) é baixo
  por dia. Não bloqueia o MVP de descoberta.
- **Ponto de entrada:** `core/dou_feeder.py` (parse dos artType de ciclo),
  `core/temporal.py` (aplicar transição), `core/edital_id.py` (id estável).
  **Status:** aberto; decisão de id é P-agora, sync é pós-MVP.

### DOU feeder — maturação de precisão (pós dry-run 2026-06-09)
- **Contexto:** dry-run da cadeia DOU→triagem→extração: 63 candidatos → 9
  aprovados. Threading do órgão (artCategory→SearchHit.agency) e aperto da
  triagem (deep-tech/P&D) JÁ FEITOS. Restam 3 frentes adiadas:
- **(a) `org_allowlist` de C&T é a alavanca PRIMÁRIA de precisão p/ DOU
  (conclusão revertida pelo dry-run):** o dry-run provou que a triagem de TEMA
  sobre o aviso fino é não-confiável (9→0→2 aprovados, inconsistente entre runs —
  o tema não está no aviso, só no edital linkado). Já o ÓRGÃO (`artCategory`) é
  sempre confiável no XML. Logo, para DOU, **filtrar por órgão é mais robusto que
  por tema**: `org_allowlist` C&T (`ciência`, `finep`, `cnpq`, `embrapii`,
  `desenvolvimento, indústria`, `defesa`, `comunicaç`, `energia`, `petróleo`,
  `senai`, `amparo à pesquisa`) cortou 97→5 determinístico/barato. **Caveat:** FAPs
  estaduais aparecem sob "Governo do Estado de X" (não topo C&T) → allowlist perde
  essa cauda; mitigar adicionando padrões de FAP/governo estadual quando entrarem.
  Resíduo nos 5 (alteração, portaria, UASG) → regra barata + triagem lenient. A
  triagem (rebalanceada p/ reject-driven, "na dúvida aprova") vira 2ª passada, não
  o filtro principal. `dou_candidates(org_allowlist=...)` já existe.
- **(b) Extração profunda via edital linkado:** o aviso DOU é FINO (título+órgão+
  prazo vêm bem; tema/detalhes vêm pobres) — confirma "DOU = descoberta, não
  extração". O conteúdo rico está no PDF/edital apontado pelo aviso. Seguir o
  link (`pdfPage`/URL do edital) p/ extração completa quando o aviso passar na
  triagem. Hoje o aviso surge a oportunidade; a profundidade é follow.
- **(c) Endurecer retry do login INLABS:** o handler `logar.php` dá 502
  intermitente (visto ao vivo: ora furou na 1ª, ora sustentado >12s). O retry
  atual (`_login`, 4×/3s) é fino p/ um cron diário → backoff maior + tolerância
  a dia perdido (descoberta não pode quebrar por manutenção do INLABS).
- **Ponto de entrada:** `core/dou_feeder.py`, `core/opportunity_discovery.py`
  (`_triage`/`_extract`). **Status:** aberto (destrava com a Fase A em prod).

### Fontes — proveniência/confiança por campo (dissolver "estruturada vs. cega")
- **O quê:** hoje há dois caminhos implícitos de metadado. FINEP/FAPESP trazem
  `status`/`deadline`/`tema` do scrape (confiável, *schema-on-write*); a fonte web
  genérica (`web:<hash>`, em implementação) não tem listagem estruturada → metadado
  vem da síntese da wiki (inferido, *schema-on-read*). São duas categorias com code
  paths distintos (`_NORMALIZERS["web"]` "thin" + resto cai na síntese).
- **Insight (estado da arte):** o eixo certo a abstrair NÃO é "estruturada vs. cega" —
  é **confiança/proveniência por campo**. Frameworks de structured-extraction
  (instructor, LLM-as-extractor) tratam toda fonte igual: extrai-pra-schema, e a fonte
  estruturada é só o caso `confidence=1.0` com valor pronto; a "cega" é a mesma pipeline
  com confiança baixa. Some a dicotomia: um path só (síntese roda em todas as fontes;
  scrape estruturado entra como **prior de alta confiança** que ela confirma).
- **O que renderia de concreto:** campo `provenance` + `confidence` **por campo** (não
  por documento) — "deadline veio do scrape FINEP (1.0)" vs. "deadline inferido do texto
  web (0.6)". Vira sinal de ranking no match e de UI ("verifique este prazo").
  Casa com o `confidence` numérico por campo já adiado na frente de Extração v2 (acima).
- **Por que adiado:** o adapter web genérico (path separado) está correto e suficiente
  para 1 fonte cega. O custo de abstrair só se paga quando "fontes cegas" virarem 5-10.
- **Gatilho para revisitar:** nº de fontes não-estruturadas crescer (web discovery
  produtizado + N portais HTML), OU o match passar a precisar do sinal de confiança
  para des-rankear metadado inferido.
- **Onde:** `pipeline/adapters/`, `build_knowledge_graph.py` (`_NORMALIZERS`),
  `pipeline/etl_process.py` (síntese). **Status:** aberto (design nomeado, não construído).

### Multi-quadrante — follow-ups (pós-sessão 2026-06-10)

Contexto: sessão fechou Fase B surfacing, `mode=pitch` (investidor), critic
pitch-aware, suítes de eval (investor_match + opportunity_type + gate de extração)
e o radar unificado L2. Spec em `docs/spec_multi_quadrante.md` (+ `_schema`).
Memória: `project_multi_quadrante`. Os itens abaixo são o que ficou conscientemente
de fora — nenhum bloqueia o que foi entregue.

- **Frontend do radar unificado (L2).** Backend pronto (`POST /match/radar`,
  `core/radar_service.py`), mas a página de matching ainda mostra 2 seções
  separadas (MatchCard + InvestorCard). Falta a view de UM ranking com badge de
  quadrante + sinal `why_now`. **Onde:** `frontend/src/app/matching/page.tsx`.
- **Botão "escrever pitch" no card de investidor (Q3).** O endpoint
  `/writing/start` já aceita id `investidor:` (mode=pitch), mas não há gancho de
  UI a partir do InvestorCard. **Onde:** `frontend/src/app/matching/page.tsx`.
- **Ranking do radar L2 — base feita (RRF + floor), refinos pendentes.** Resolvidos:
  (1) normalização de scores heterogêneos via Reciprocal Rank Fusion (funde por
  rank-dentro-do-tipo, intercala eventos e fundos); (2) floor de qualidade via
  tier forte/fraco (rebaixa o rank-1 fraco sem eliminar — evento usa flag
  `eligible`, entidade usa `_ENTITY_FLOOR=6.0`). Pendente afinar: (a) **pesos por
  quadrante** (hoje 50/50 implícito — talvez priorizar eventos por urgência de
  deadline ou preferência do usuário); (b) **calibrar `_ENTITY_FLOOR`** com dado
  real (hoje 6.0 chutado pela generosidade observada do scorer LLM); (c) usar
  `why_now`/urgência como critério de ordenação, não só display. **Onde:**
  `core/radar_service.merge_radar` (`_RRF_K`, `_ENTITY_FLOOR`, `_is_weak`).
- **Match tipo-aware de desafio/programa — BLOQUEADO-POR-DADOS.** O HybridMatch
  já não QUEBRA com desafio/programa (dims de edital ausentes degradam para
  neutro, não eliminam), mas não usa sinais próprios (`empresa_ancora`,
  `poc_scope`, dims/pesos por `kind_class`). E o eval de MATCH desse tipo não tem
  casos: **não há desafio/programa no índice** porque a torneira web está inerte e
  FINEP/FAPESP só emitem `edital`. **Destrava:** ligar a Descoberta web OU o feeder
  DOU → dado entra no índice → curar golden (perfis→desafios esperados, provável
  reuso da suíte `matching` filtrando por `opportunity_type`) + afinar scoring.
- **Critic de pitch mais rico.** Hoje o critic pitch-aware cruza contra o nó do
  fundo (contradição de tese/estágio) + coerência entre seções, mas não valida
  fatos externos (não conhece tração real). Insumo futuro: perfil/biblioteca para
  checar coerência de tração/números. **Onde:** `core/agent_tools/critic_agent.py`.
- **Ligar a torneira web em prod (sair do "inerte").** Não é código — são 3 chaves
  de ops: worker procrastinate ativo + `TAVILY_API_KEY` + chave LLM no .env do Docker Compose. O
  cron diário (`discover_opportunities_task`, 04:00 UTC) liga sozinho. Pré-requisito
  do fix de `titulo` vazio (item acima) antes de `write=True` em prod.

---

## Débitos conhecidos (menores)

- **`domain/vocabulary.canonicalize_themes` é stub** (só lowercase/dedupe). O vocab
  canônico de temas vive em WIKI.md §5.9; quando uma fonte emitir variação de tema,
  implementar o mapa de sinônimos para convergir ao §5.9.
- ~~**Export Obsidian ainda FINEP-only** — nós `ict` não são exportados ao vault.~~
  RESOLVIDO 2026-07-06: `scripts/export_to_obsidian.py` reescrito p/ o schema
  v2 (Oportunidade/Ator/Conceito) — cobre todos os `kind`/`dim` com dado real
  (icts, investidores, agências, FAPs, corporates, aceleradoras, temas,
  tecnologias, aplicações, programas, investimentos), restaura os wikilinks
  cross-source e via-arestas (quebrados desde a migração v2, que comparavam
  contra strings de tipo v1) e passa a renderizar `constraints[]`,
  `macro_temas[]` e `aperture` nas notas de edital.
- **Flag só sobre texto coletado** — exigência de ICT em anexo PDF não baixado é
  falso-negativo estrutural (limite da heurística, documentado em §5.10).
- **Hyper-Extract (`core/retrieval/hyper_extractor.py`) — 3 dívidas achadas na
  auditoria de 2026-07-03** (ver [[project_hyperextract_schema_audit]]):
  1. Dedup só por lowercase-exato em 10 dos 12 tipos de nó (só Mecanismo e Fonte
     têm normalizador pós-extração). Sinônimos ("IA" vs "Inteligência Artificial")
     viram nós diferentes no grafo; só reconciliam por cosseno no match, nunca no
     KG. Cresce o corpus, cresce a fragmentação — relacionado a
     [[project_hyperedges_underused]]. Pode ficar moot se a exploração de schema
     em andamento consolidar os tipos-eixo.
  2. Dois dicionários de canonicalização de Fonte divergentes: `WIKI.md §5.4
     fontes_canonicas` (via `core.kg.schema`) vs `hypergraph_catalog._FONTE_CANONICAL`
     (hardcoded, usado de fato pelo normalizador vivo). Viola o princípio do
     projeto ("regra vive no doc") — risco de drift silencioso.
  3. Output do hipergrado (`hypergraphs/{id}.json`) não versiona schema/prompt
     (só `source_hash`), ao contrário do silver (`meta_sidecar` com
     `structurer_prompt_version`/`structurer_model`). Convenção atual é "mudou
     prompt? apague `hypergraphs/` na mão" — não documentada, não enforced.

---

### Gate de grounding (writing eval) confiável — investigado 2026-06-13

- **O quê:** `pct_grounded` do `core.eval writing` não é gate confiável. Investigação
  (PR #25) provou que NÃO é regressão de produto nem do retriever (finep:774=83
  chunks, finep:769=151, `retrieve_chunks` saudável; suspeitos `1eb00699a`/`8e29384b6`
  LIMPOS). A instabilidade (medido 0.05–0.625 entre runs do MESMO fixture) tem duas
  causas: (a) fixture **misfit** — iFlorestal (florestal) pareada com editais de
  agro/agricultura familiar → o agente fabrica claims de fit que o juiz corretamente
  não sustenta; (b) **variância do output do agente** entre runs (drafts estocásticos
  → nº de claims e grounding mudam por run). Micro-média (Σgrounded/Σclaims) foi
  avaliada e descartada: NÃO resolve — a variância é do draft, não da agregação
  (spread 0.59 ≈ macro 0.575 nos 3 runs reais).
- **Por que adiado:** a investigação já entregou o valor (o número não é regressão;
  o gate é que é cego). Tornar o gate útil é trabalho de fixture + custo de N-runs,
  sem decisão de retrieval pendente agora.
- **Como fazer:** (a) ✅ FEITO 2026-06-14 — fixture reescrita com pares **bem-casados
  ancorados nos CHUNKS** (não no resumo do wiki): `espectra`→finep:774 (linha
  hiperespectral) e `tratorbr`→finep:769 (trator+implementos). Grounding pooled
  0.05→0.50, factual_errors 0.67→0.33, saved 0.83→1.0, e um caso limpo a 3/3=1.0,
  0 erros (prova que o agente ANCORA bem em fit limpo). (b) domar variância via
  **N-runs + média** ou temp fixa no eval — AINDA ABERTO (tradeoff: N-runs multiplica
  custo de LLM); (c) opc.: reportar grounding só quando Σclaims do run ≥ limiar.
- **LIÇÃO METODOLÓGICA (2026-06-14):** não dá pra desenhar fixture de fit a partir do
  resumo do wiki (themes/objective) — escopo real (exclusões, entregáveis obrigatórios)
  vive nos chunks. It.1 (perfis do wiki) falhou: finep:774 EXCLUI cana (perfil usava
  bagaço de cana); finep:769 exige `trator + 6 implementos obrigatórios` (perfil era
  sensor+app). Só ler os chunks deu fit limpo. Registrado no `_comment` da fixture.
- **Gatilho para retomar:** querer usar `pct_grounded` como gate de merge real (falta
  só domar variância, item b), OU mexer no Redator.
- **Ponto de entrada:** `tests/fixtures/eval_cases.json`, `core/eval/writing.py`
  (`task`), `core/eval/metrics_writing.py` (juízes).
- **Status:** parcialmente resolvido (2026-06-14): fixture bem-casada feita; resta
  domar variância (b). Ver memory `project-agent-patterns-deepagents`.
- **Corroboração (2026-06-15, PR #26):** o gate do Item 5 (Critic sub-agente) rodou um
  A/B writing eval critic-novo vs critic-antigo: grounding **flat ~0.40** nos dois
  (0.417 vs 0.396) → reconfirma que o número é do writer/retrieval, **independente** do
  critic. Item 5 não regrediu nada (saved=1.0, zero over-block). Reforça item (b): a
  alavanca real é variância do draft + qualidade de ancoragem do redator, não o critic.

### Redator inventa escopo/procedimento do edital (resíduo real, fixture limpa)

- **O quê:** com fixture bem-casada (2026-06-14), os erros factuais que SOBRAM são
  sinal de produto real, não artefato. Em `tratorbr`→finep:769 o redator: (1)
  **deturpou o escopo** do edital — afirmou que finep:769 "apoia Agritech /
  agricultura de precisão" quando o edital é de MECANIZAÇÃO (trator+implementos);
  (2) **inventou um passo procedural** — "Reuniões iniciais com a equipe da Finep" na
  metodologia, que VIOLA a regra de conflito de interesse do edital (especialistas
  ad-hoc sem vínculo). Ambos pegos pelo juiz factual.
- **Por que adiado:** estreito (1 dos 2 perfis, minoria das seções) e não bloqueia —
  o agente ancora bem em fit limpo (espectra 3/3). Mas é a próxima alavanca real de
  qualidade de escrita, agora que o instrumento mede a coisa certa.
- **Frentes possíveis:** (a) prompt do redator — instruir a NÃO afirmar o escopo do
  edital sem respaldo em chunk recuperado (descrever só o que o edital diz, verbatim);
  (b) puxar regras procedurais (conflito de interesse, vínculos) pro contexto via
  retrieval antes de redigir metodologia; (c) ComplianceMonitor/Critic pegar esse
  tipo de claim antes do save.
- **Gatilho:** próxima rodada de melhoria do Redator, ou se grounding virar gate.
- **Ponto de entrada:** prompt do redator em `core/services/writing_session.py`,
  juiz `judge_factual_errors` em `core/eval/metrics_writing.py`.
- **Status:** aberto (2026-06-14).

---

### Tool-calling como teto do bake-off no tier agêntico (tier 5)

- **O quê:** o tier agêntico (writing + critic) usa function/tool calling em LOOP.
  A capability de plugar qualquer modelo já existe (2 protocolos: Anthropic nativo +
  OpenAI-compat com base_url — `AGENT_OPENAI_BASE_URL`/`CRITIC_OPENAI_BASE_URL`, commit
  8b2eadf63). Mas **conectar ≠ funcionar**: modelos open/baratos falam chat.completions
  e mesmo assim têm tool-calling fraco → quebram o loop (JSON de tool malformado, não
  param, ignoram a tool). Esse é o TETO real do open no tier 5, não o protocolo.
- **Por que está parado:** depende de dois pré-requisitos que ainda não existem —
  (a) **gate de grounding confiável** (ver entradas acima; sem ele não dá pra julgar a
  saída agêntica), e (b) um **provider ZDR/pago** (writing/profile = dado de cliente →
  proibido free-tier-com-treino). Sem os dois, testar candidato barato aqui seria
  degradar às cegas — viola a premissa "só corta custo com gate verde".
- **O que medir quando destravar:** taxa de tool-calls válidos / loops concluídos sem
  fallback, ANTES da qualidade (BFCL é proxy — Qwen3.5-397B lidera o open; DeepSeek
  decente). Só candidato que sustenta o loop entra no gate `writing`.
- **Gatilho para retomar:** gate de grounding confiável + escolha de provider ZDR.
- **Ponto de entrada:** `core/llm/agent_runtime.py` (`run_agent`/`_call_openai`),
  `core/llm/agent_tools/critic_agent.py`; perfil em `docs/specs/demo-cost-profile.md`,
  spec em `docs/specs/llm-embedding-bakeoff.md` §5.
- **Status:** aberto (2026-06-16) — capability pronta, promoção bloqueada.

---

### Learning loop dos playbooks/KG — evolução por uso (parado: sem usuários)

- **O quê:** como o ecossistema de conhecimento (nós de mecanismo/fonte + skills)
  evolui pelo uso. Motor JÁ existe no tier-empresa: outcome (`aprovada/reprovada` em
  applications) → `reflect_workspace` ([reflection_service.py](../../core/reflection_service.py))
  com `MIN_OUTCOMES_FOR_REFLECTION=3` + `confidence` low/med/high + `evidence_ids`
  (provenance) → auto-insert (privado, blast radius 1). Falta levantar o mesmo motor
  a **dois tiers compartilhados** (overlay de fonte, base de mecanismo).
- **Gate compartilhado (a desenhar):** piso cross-workspace (≥N outcomes de ≥K
  workspaces), só `confidence=high` elegível, **fila de curadoria humana**
  (não auto-promove — filosofia Grantable), eval-gate (precisa do grounding
  confiável), provenance+data p/ aposentar, e **filtro fato↔craft**: delta que é
  FATO do instrumento → vai pro nó KG (conhecimento); delta que é CRAFT → vai pra
  skill (competência).
- **Substrato (decisão):** o destino do conhecimento é o **nó do KG** (mecanismo/
  fonte como nós com wiki_page, à la LLM-wiki de Karpathy — "o grafo é a fonte de
  conhecimento"). Arquivos `skills/*.md` em git são o **bootstrap** (tier-0); o
  loop é idêntico nos dois (escreve delta em markdown). Reconciliação Karpathy:
  o LLM **propõe** sempre; o gate **promove** nas páginas compartilhadas.
- **Por que parado:** sem usuários reais não há outcomes → nada a aprender. É
  fundação de aparato, não de produto. Espelha a disciplina do golden RAG.
- **Gatilho para retomar:** volume de outcomes reais (aplicações com status final)
  acumulando + decisão de subir qualidade além da curadoria manual.
- **Ponto de entrada:** `core/reflection_service.py` (motor), `applications.py`
  (trigger), o substrato de nó KG (`core/kg/`), `docs/specs/skills-by-mechanism.md`.
- **Status:** desenho fechado, implementação parada (2026-06-14).

### Playbooks: conhecimento tácito por mecanismo — spec travada 2026-06-14

- **O quê:** redesenho do subsistema de "skills" do Redator/Monitor. Hoje são
  keyed por fonte (`skills/<source>_compliance.md`) e misturam regra dura (que é do
  edital/RAG) com tácito. Spec completa em [docs/specs/skills-by-mechanism.md](skills-by-mechanism.md):
  separar **normativo (RAG)** de **tácito (playbook)**; keying por **mecanismo**
  (campo já estruturado) + overlays de fonte; **seções-nomeadas = tipos =
  roteamento** pros consumidores que já existem (Redator↔escrita/tom,
  Monitor↔heurísticas/anti-padrões, Critic intocado). 7 decisões (D1–D7) travadas.
- **Por que adiado:** decisão de design fechada (validada contra 2 modelos
  externos), mas a implementação é média (loader + extrator web + roteamento) e o
  conteúdo de domínio (playbooks por mecanismo) é o gargalo, incremental.
- **Pré-requisito de dados:** `mechanism` está 100% vazio na web (extrator da
  Descoberta não preenche) — estender `_extract` faz parte do roteiro (D7).
- **Conexão:** mover tácito do prompt de geração pro avaliador é fix plausível
  parcial do grounding (ver entrada acima).
- **Gatilho para retomar:** querer subir a qualidade/aderência da escrita para
  além do RAG puro, OU FAPESP/web entrarem em produção precisando de praxe curada.
- **Ponto de entrada:** `core/skills.py` (loader), `opportunity_discovery._extract`
  (mechanism), `compliance_monitor.py` + `writing_tools.load_skill` (roteamento).
- **Status:** **loader + flip dos consumidores IMPLEMENTADOS (2026-06-14)** —
  `load_playbook` compõe 3 camadas por seção; Monitor/Redator flipados; antigos
  `*_compliance.md` removidos (tácito FINEP → `source/finep/global.md`). Playbooks de
  `subvencao`/`credito`/`equity` ativos (SEED). **Pendente:** (a) extrator web preencher
  `mechanism` (D7) — web 100% None; (b) migrar os 5 `investimento`→`credito` na fonte de
  dados (D2); (c) `mechanism/_generic.md` p/ o fallback None não ficar vazio (D3);
  (d) shadow/eval de injeção + learning loop (sem usuários ainda).
- **Overlay `source/bndes/credito.md` (pendente de fonte):** a entrevista de `credito`
  (2026-06-14) rendeu praxe BNDES rica (análise econômico-financeira/governança no
  centro, narrativa corporativa menos tecnológica, FGI/FGO como garantia), mas BNDES
  **não é fonte indexada** hoje — só FINEP/FAPESP. Matéria-prima está em
  `docs/specs/playbook-interview-credito.md` (bloco F). **Gatilho:** BNDES virar fonte
  ativa do pipeline → criar o overlay a partir dessas respostas.
- **Playbook `matching` (EMBRAPII) — adiado por escopo (2026-06-14):** EMBRAPII/ICT
  são insumo do **Match** (parceria empresa↔ICT), não da **escrita**; não há valor em
  autorar o playbook de redação agora. Template de entrevista preenchido já existe em
  `docs/specs/playbook-interview-matching.md` (semente). `bolsa` ficou **fora de
  escopo** de vez (sistema não atende bolsas). **Gatilho:** decidir que a escrita
  cooperativa entra no produto → rodar a entrevista e destilar `skills/mechanism/matching.md`.

## Fechado-adiado (revisitar só no gatilho)

### ICT — tuning do flag `requires_ict_partner`
- **Decisão (2026-06-03):** **não** tunar agora. O flag é *hint de proatividade,
  não gate* — `find_ict_partners` funciona independente dele, então os erros são
  de baixo custo. Otimizar uma heurística não-medida e não-crítica é prematuro.
- **Estado atual:** 10/20 vigentes marcados (todos FINEP; FAPESP sempre `false`).
  Pattern [1] faz 9/10. "Falsos-positivos" não confirmáveis sem ground-truth.
- **Gatilho para revisitar:** o flag virar **load-bearing** — UI filtrar/ordenar
  editais por ele, OU a seleção de parceiro (C.2) virar fluxo primário.
- **Como revisitar então:** rotular amostra (exige ICT? s/n) → medir precisão/
  recall → ajustar patterns §5.10 (incl. contexto negativo); se empacar, graduar
  para classificador LLM no build.
- **Onde:** WIKI.md §5.10.

## Concluídos (referência)

- **ICT Fase A** (ingestão EMBRAPII + schema + icts.json) — commit `381810614`.
- **ICT Fase C.1** (flag + query + tool no Explorador) — commit `381810614`.
