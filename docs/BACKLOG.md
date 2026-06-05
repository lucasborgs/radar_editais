# Backlog — pendências para posterioridade

> Documento **vivo**. Itens conscientemente adiados (não esquecidos). Cada item
> traz contexto suficiente para retomar sem reconstruir o raciocínio: **o quê**,
> **por que adiado**, **onde está specado**, **ponto de entrada**, **status**.
>
> Convenção: ao concluir um item, mova-o para "Concluídos" (com o commit/PR) ou
> remova-o. Ao adiar algo novo, adicione aqui na hora — o custo de esquecer é alto.

---

## Aberto

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

### Descoberta de Oportunidades (item 2.2) — Fases B e C (Fase A feita)
- **Feito (Fase A):** ingestão `verificacao` (§5.11) + `_build_discovery_editais`
  no build (descoberta → KG provisório, via pme_filter); engine
  `core/opportunity_discovery.py` (web_search → triagem → extração → bronze
  discovery_raw/ + ledger de dedup file-based). Vocab em `wikis/_discovery.md`.
  **Pré-requisito de uso real:** `TAVILY_API_KEY` + chave LLM; rodar o engine e
  depois `build_knowledge_graph`.
- **Fase B (aberto):** verificação humana não-bloqueante — endpoint verificar/
  rejeitar, match/escrita distinguindo provisorio×verificado (rótulo/bucket — item
  3 das decisões: bucket no MVP), aviso de fonte não-verificada na escrita.
- **Fase C (aberto):** task procrastinate `discover_opportunities` (encadeia build)
  + cron diário; graduação de fonte recorrente para extractor próprio (§12.4).
  Ledger file-based pode graduar para Supabase se virar multi-worker.
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

### RAG/Escrita — editais SEM PDF ficam sem chunks (FAPESP + FINEP-sem-anexo)
- **O quê:** `scripts/reindex_edital.py`/`chunk_edital_task` chunkam **só PDF** (via
  `FINEP_PDFS_DIR` + adapter FINEP). Editais cujo conteúdo vive no **registro
  estruturado** (não em PDF) ficam com `edital_chunks` vazio → na escrita o
  `search_edital` volta vazio e o agente redige só com perfil+card (menos ancorado;
  NÃO quebra — degrada). Confirmado no pré-aquecimento do cloud (2026-06-05): 17/20
  editais do índice com chunks; **3 sem**: `fapesp:18067`, `fapesp:18203` (FAPESP nunca
  tem PDF) e `finep:613` (FINEP "Programa de Investimento em Startups" — veio sem anexo,
  só titulo+descricao de ~957 chars; NÃO é falha de download).
- **Fix (vale p/ os 3):** caminho de chunking a partir do **texto do registro**
  (`descricao`/`texto_cru`) quando não há PDF — idealmente no mesmo pipeline
  (`_build_chunks_for_edital` → fallback p/ record-text quando `adapter.to_documents`
  vier vazio), passando por `chunk_from_blocks` + embed + upsert. Idempotente como o PDF.
- **Por que não foi feito agora:** one-off frágil p/ 3 editais de conteúdo curto rende
  pouco sobre o que o card já dá; productizar o path é o certo. Não bloqueia o beta.
- **Ponto de entrada:** `core/tasks.py::_build_chunks_for_edital`, `core/chunker.py`.
  **Status:** aberto.

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
