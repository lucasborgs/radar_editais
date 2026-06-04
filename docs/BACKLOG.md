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
  Criados `uf`/`faturamento_anual`/`ano_fundacao` no perfil; falta o `profile_extractor`
  preenchê-los e o Stage 1 usá-los.
- **Adiados (curadoria Gemini+ChatGPT reconciliada com o código):**
  - **Gate sobre `eligibility_constraints`** (região/idade/faturamento): o
    `EditalExtraction` v2 já CAPTURA estruturado; falta (a) `profile_extractor`
    preencher os pares no perfil e (b) wirar no Stage 1. Hoje são contexto/HITL.
  - **`call_type`** (business_innovation|academic|grant|procurement|challenge):
    tipologia que muda a interpretação dos campos. Menos urgente — o `pme_filter`
    já descarta bolsa/acadêmico upstream.
  - **`absent` → `not_found`/`not_applicable`**: refinar a abstenção (over-eng p/ agora).
  - **`confidence` numérico** por campo (monitoramento/active-learning).
  - **CNAE / consórcio** como pares perfil↔edital.
  - **Versionar retificação ("Aditivo 01")**: atualizar campo específico vs reprocessar.
- **Onde:** [spec_extraction_schema.md](spec_extraction_schema.md), domain/edital_extraction.py,
  domain/user_profile.py. **Status:** aberto.

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
  chamada generativa ao LLM que devolve `{id: score}`. Candidato a virar
  **similaridade vetorial determinística**: embedding do tema/objetivo do edital
  × embedding do perfil (cosseno), pré-computado no ETL. O LLM ficaria só no
  Stage 2b (explicar o top-K), onde geração faz sentido.
- **Por que adiado:** o split 2a/2b + guardrails (commit desta sessão) já matou o
  truncamento e o `return {}` silencioso — o problema agudo está resolvido. O
  ganho do embedding é custo/latência/determinismo, não correção. E há um risco
  real a estudar antes (abaixo).
- **Por que vale:** scoring determinístico, **zero token por request**, sem
  truncamento, sem variância LLM no ranking.
- **Risco / guarda (Lucas, 2026-06-04):** similaridade por embeddings é
  **muito sensível à estratégia de geração dos embeddings** — o que se embeda
  (tema canônico? objetivo bruto? título?), qual modelo, normalização,
  granularidade. Um pipeline de embedding mal calibrado degrada o ranking de
  forma silenciosa (pior que o LLM, que ao menos "raciocina" o fit). **Não
  adotar sem um experimento controlado**: rodar a suíte `matching` (precisão@K)
  com o caminho embeddings vs o caminho LLM, no MESMO golden, e só trocar se
  empatar/superar. O harness de eval já existe para isso (`python -m core.eval
  matching`).
- **Ponto de entrada:** `core/hybrid_match_service.py::_call_stage2_scores`
  (trocar a implementação, manter a assinatura `{id: float}`); embeddings
  pré-computados no ETL (`pipeline/build_knowledge_graph.py`) + `core/embedder.py`.
- **Status:** aberto, em estudo (gated por experimento de eval).

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
