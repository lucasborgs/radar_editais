# Backlog — pendências para posterioridade

> Documento **vivo**. Itens conscientemente adiados (não esquecidos). Cada item
> traz contexto suficiente para retomar sem reconstruir o raciocínio: **o quê**,
> **por que adiado**, **onde está specado**, **ponto de entrada**, **status**.
>
> Convenção: ao concluir um item, mova-o para "Concluídos" (com o commit/PR) ou
> remova-o. Ao adiar algo novo, adicione aqui na hora — o custo de esquecer é alto.

---

## Aberto

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

### Parsing/chunking estrutura-aware — INVESTIGADO E REFUTADO (benchmark-driven, 2026-06-06)
- **Hipótese:** parser estrutura-aware (Docling p/ PDF, numbering p/ FAPESP texto-plano)
  → modelo de blocos tipado → melhor `section_path` → melhor retrieval. Motivada pela
  patologia de "unit gigante" (FAPESP texto_cru achatado → units de ~24k chars).
- **Metodologia (no harness existente):** métrica **chunking-invariante** — token-recall
  sobre `gold_text` (estilo Chroma) em `core/rag_eval.py` (`gold_recall_at_k`,
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
  - **Infra de eval**: token-recall chunking-invariante + `core/parsing_eval.py` (métricas
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
- **O quê:** o `extract_from_text`/`_call_llm` ([core/profile_extractor.py](../core/profile_extractor.py))
  extrai bem `uf`/`ano_fundacao` mas perde `faturamento_anual` mesmo quando o texto
  o afirma. **Evidência (walkthrough 2026-06-06):** proposta com "Faturamento anual
  R$ 2 milhões" → `uf='SP'` e `ano_fundacao=2019` vieram `high`, mas
  `faturamento_anual` veio `missing`/None.
- **Por que importa:** é um dos 3 campos thin-profile (o "teto do matching"). Agora que
  a cadeia UI/save foi fechada (o campo flui de ponta a ponta), o gargalo restante é
  só a extração. Mitigação atual: o usuário digita no campo novo do onboarding.
- **Ponto de entrada:** prompt `_EXTRACT_SYSTEM`/`_EXTRACT_USER` e o schema de saída em
  [core/profile_extractor.py](../core/profile_extractor.py) — instruir a capturar valores
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
- **Onde:** [spec_extraction_schema.md](spec_extraction_schema.md), domain/edital_extraction.py,
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
- **Por que adiado:** FINEP+FAPESP já cobrem volume considerável; discovery foi
  validado (provou a cegueira do Stage 1, ao vivo) mas productionizar é fluxo à parte,
  e há outros fluxos a validar antes. Web já roda (`discover_opportunities`), entra no
  índice local via `build_knowledge_graph`, mas fica fora do índice de prod.
- **Ponto de entrada:** isolamento prod + qualidade/dedup dos itens `provisorio`.
- **Status:** adiado conscientemente.

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
- **Onde:** [spec_ict_phase_c.md](spec_ict_phase_c.md) peça 4 / fase C.2.
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
- **Onde:** [spec_ict_mapping.md](spec_ict_mapping.md) Fase B.
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
- **Onde:** [spec_descoberta_oportunidades.md](spec_descoberta_oportunidades.md).

### DeepResearch — Fases B e C (Fase A feita)
- **Feito (Fase A):** `core/web_search.py` (port Tavily REST), `core/deep_research.py`
  (subagente run_agent + anti-fabricação), tool `deep_research` no Redator. Stateless,
  não persiste. Falta `TAVILY_API_KEY` no ambiente para uso real.
- **Fase B (aberto):** gate de learning — endpoint `POST /library/from-research` +
  `create_item(type_='web_research', source_url=…, enrich=True)` + painel de "fontes
  pendentes" no frontend. É onde o fato escolhido vira memória do projeto.
- **Fase C (aberto):** decay por tipo (`web_research` com meia-vida menor) + tool no
  Explorador + eval anti-fabricação (casos cuja resposta certa é "não encontrei").
- **Onde:** [spec_deepresearch.md](spec_deepresearch.md).
- **Pré-requisito de uso:** configurar `TAVILY_API_KEY` (e `WEB_SEARCH_BACKEND=tavily`,
  default). Sem chave, a tool degrada com mensagem.

### RAG — golden `finep` com sections obsoletas (brittle a re-chunk)
- **O quê:** a suíte `rag` casa `expected` por `source_file` + `section` EXATA
  (`core/rag_eval.py::_matches`). O golden `eval_data/golden/finep.json` (03/06) tem
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

---

## Débitos conhecidos (menores)

- **`domain/vocabulary.canonicalize_themes` é stub** (só lowercase/dedupe). O vocab
  canônico de temas vive em WIKI.md §5.9; quando uma fonte emitir variação de tema,
  implementar o mapa de sinônimos para convergir ao §5.9.
- **Export Obsidian ainda FINEP-only** — nós `ict` não são exportados ao vault.
- **Flag só sobre texto coletado** — exigência de ICT em anexo PDF não baixado é
  falso-negativo estrutural (limite da heurística, documentado em §5.10).

---

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
