# Spec Unificada — v3: Dissociação Match / KG / RAG (revisão 2)

- **Status:** Implementada (Fases 0–5 concluídas em 2026-07-11; arquitetura vigente)
- **Data:** 2026-07-09
- **Autores:** Lucas + Claude
- **Substitui:** `v3-match-kg-redesign.md` (2026-07-07), `hypergraph-migration.md` (2026-07-09) e a revisão 1 desta spec
- **Mudanças-chave vs revisão 1:**
  1. **Hyper-Extract morre de vez** (não é "mantido" — `gold.py` é o único ingestor)
  2. Ontologia validada contra o ecossistema early-stage/PME: 5 kinds, 4 arestas, `kind` separado de `source`
  3. `estagio_alvo` sai dos editais (vocabulário de investidor); elegibilidade de edital = faturamento/idade-CNPJ/porte/CNAE/vínculo, via avaliador PR5 (camada única, `unknown` nunca elimina)
  4. Stage 1 SQL reduzido a "vivo" (status/deadline com guarda de NULL) — sem hard filter semântico
  5. Agregação do Stage 2 = **sum-of-max por chunk da empresa** (não max global) + filtro de boilerplate por `section_path`
  6. Superfície própria de chunks de match (embed cru do silver, **sem** contextual retrieval); lazy chunking do writing intacto
  7. Tools de substituição do mapeamento especificadas (busca semântica + join por tags + BFS)
  8. `company_chunks` com workspace_id + RLS
  9. Eval-first: bake-off offline antes de migrar consumidores; métrica de recall do Stage 1
  10. Constraints de elegibilidade extraídas das seções de elegibilidade do **silver** (resolve pendência do produtor)

---

## 1. Motivação (resumo)

O hipergrafo v2 tenta servir 4 funções — match, navegação, catálogo, modelo canônico — e falha em todas. Evidências:

- **Match:** cada iteração de qualidade virou filtro pós-hoc no consumidor (threshold → marginsum → MaxSim → stop-list de ~130 genéricos → curated gate → damping 0.30), nunca no produtor. Sintoma de representação errada: o sinal não está nos conceitos extraídos.
- **Perda em cascata:** bronze → curado → hipergrafo → conceitos → match = 4 camadas de perda (EMBRAPII: `about` ~2000 chars vira 1 linha; edital: texto inteiro vira conceitos).
- **O grafo nunca foi topologia:** descritores com fan-in≈1 — Conceitos não conectavam entidades; o hipergrafo funcionava como índice de embeddings com passos extras e ruído.
- **Fragilidade operacional:** dupla representação (JSON em disco gitignored + blob PG), curated gate com fail-open silencioso, disco efêmero no Railway.
- **Custo:** 2+ chamadas LLM por chunk de 2048 + 2 passes por edital, para alimentar um match que no fim é cosseno.

Estado da arte (2024-2026) endossa a direção: funil hard-filter → denso → rerank → LLM verdict é o padrão de produção (LinkedIn, recsys 4-stage); representações especializadas por feature derivadas da mesma fonte são a convenção; a evidência do hipergrafo N-ário era fraca (paper único, sem replicação).

## 2. Princípios

1. **Match usa texto real** (silver), não conceitos extraídos.
2. **Uma só camada de elegibilidade**, determinística, com semântica do PR5: `unsat` elimina, `unknown` **nunca** elimina.
3. **Aresta só existe se for preenchível deterministicamente** a partir das fontes de hoje. Relações semânticas = tags compartilhadas + busca vetorial + LLM em query-time.
4. **LLM em exatamente dois pontos do plano de dados:** tagger+constraints por edital no ingest; verdict no topo do funil. Todo o resto é determinístico e re-executável de graça.
5. **Postgres é a source of truth única.** Nada de runtime lê JSON de disco.
6. **Duas superfícies de embedding com ciclos de vida distintos:** match (cru, eager, barato) e writing (contextual, lazy, caro). Não se misturam.
7. **Fronteira redefinida:** match consome `match_chunks` e `entities`; **nunca** `edital_chunks` (writing). Writing não muda.
8. **Vocabulários vivem nos docs** (WIKI.md via `core/kg/schema.py`): taxonomia de setores, regras de normalização de tags, vocabulário de constraints.

## 3. Escopo pré-beta (fontes fixas)

| Fonte | Arquivo/caminho | Volume | LLM no ingest |
|---|---|---|---|
| Editais | `data/silver/structured_docs/{finep,fapesp,fapesc,web}/{id}.jsonl` + metadados do adapter | ~150 | 2 chamadas leves/edital |
| ICTs | `data/bronze/ict_raw/embrapii_*.json` | 90 | zero (normalização determinística) |
| Investidores | `data/silver/investidores.json` | 17 | zero (já gold de fábrica) |
| Programas | `data/silver/programas.json` | 10 | zero (já gold de fábrica) |

Expansão de fontes (scrapers de programas, outras credenciadoras, mais investidores) é pós-beta. Wins baratos de curadoria (sem pipeline): adicionar InovAtiva (`mecanismo=premio`) e RHAE (`mecanismo=bolsa`) como linhas em `programas.json`.

## 4. Ontologia

Validada contra pesquisa do ecossistema early-stage/PME (2026-07-09): os atores porta-de-dinheiro para esse público são FINEP, CNPq, FAPs, Sebrae, EMBRAPII+unidades e investidores — todos cobertos pelos 5 kinds. Incubadoras/aceleradoras ficam **fora como entidade**; vínculo de incubação vira constraint (ex.: Acelera Startup SC/FAPESC exige vínculo com Startup SC/MIDIHUB).

### 4.1 Nós — `kind` (operacional) × `type` (supertipo derivado)

| kind | type | origem pré-beta |
|---|---|---|
| `edital` | oportunidade | FINEP/FAPESP/FAPESC/web (discovery promovida) |
| `programa` | oportunidade | curados (10) — guarda-chuva recorrente sem deadline próprio |
| `investidor` | ator | curados (17) — tese funciona como oportunidade na trilha investidor |
| `ict` | ator | EMBRAPII (90) |
| `agencia` | ator | derivado dos metadados (`operador`, fonte) — FINEP, FAPs, CNPq, MCTI, **Sebrae**, EMBRAPII |

`kind` ≠ `source`: `source` é proveniência (`finep`, `fapesp`, `fapesc`, `web`, `embrapii`, `curadoria`), nunca tipo de domínio.

### 4.2 Arestas — 4 tipos, todos determinísticos

| type | direção | exemplo |
|---|---|---|
| `operado_por` | edital/programa → agencia | Centelha SC → FAPESC |
| `subordinado_a` | edital → programa | Centelha SC 2026 → Programa Centelha |
| `exige_parceria_com` | edital → ict | (raro; também é constraint de elegibilidade) |
| `credenciada_por` | ict → agencia | unidade → EMBRAPII |

Cortados da proposta anterior: `vinculado_a` (função primária — "ator descoberto em edital" — desaparece com atores vindos só de curadoria) e `coinveste_com` (sem dado). Arestas semânticas (`abrange_tema`, `viabiliza`, …) não existem — ver §8 (substituição).

### 4.3 Tags — duas camadas

- **`setores[]`** — taxonomia fechada, 16 itens (Agro, Saúde, Energia, TIC, Bioeconomia, Defesa, Mobilidade, Urbano, Educação, Química, Materiais, Sustentabilidade, Marítimo, Social, Finanças, Multissetorial). 1-3 por entidade. Curados já trazem (normalizar case; mapear `tese_themes` → setores). **Nunca hard filter no match** — só facet de catálogo e boost opcional de ranking.
- **`tecnologias_tags[]`** — folksonomia normalizada. Editais: via tagger LLM (máx. 8). Investidores: `tese_keywords` prontos. ICTs: `areas_raw` normalizado. Programas: `tese_themes`/vazio. **Passe determinístico de normalização obrigatório** (lowercase, singular, mapa de sinônimos persistido — seed = `concept_canon` existente). Decisão: re-tag fresh via LLM; os conceitos v2 morrem como dado, a curadoria sobrevive como regra de normalização.

Tags não são nós (fan-in≈1 no dado real). Com índice GIN, tags compartilhadas são as arestas semânticas implícitas — join barato, sem manutenção.

### 4.4 Atributos de elegibilidade (o que editais de fato declaram)

Editais brasileiros não segmentam por rodada de investimento — segmentam por porte legal (MEI/ME/EPP), faturamento-teto contínuo (Centelha R$4,8mi / Tecnova R$16mi / Mais Inovação R$90mi) e idade de CNPJ (Centelha: máx. 12 meses). `estagio_alvo` existe **só em investidor**.

Constraints (vocabulário no WIKI.md, avaliadas por `eligibility.py`): `porte`, `faturamento` (lte/gte), `idade_empresa_meses` (lte/gte — direção varia), `sede_uf`, `forma_juridica`, `trl`, `cnae`, `parceria` (exige ICT), `vinculo_incubacao` (exige), `investidor_privado` (exige — PIPE Invest).

## 5. Schema (Migration 036)

### 5.1 `entities`

```sql
CREATE TABLE entities (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind              text NOT NULL CHECK (kind IN ('edital','programa','investidor','ict','agencia')),
    type              text GENERATED ALWAYS AS (
                        CASE WHEN kind IN ('edital','programa') THEN 'oportunidade' ELSE 'ator' END
                      ) STORED,
    source            text NOT NULL,   -- finep, fapesp, fapesc, web, embrapii, curadoria
    native_id         text NOT NULL,   -- id original no bronze/silver (ex. "finep:589", "investidor:indicator-capital")
    name              text NOT NULL,
    description       text NOT NULL DEFAULT '',   -- texto silver/curado (~2000 chars)
    mecanismo         text CHECK (mecanismo IN ('subvencao','bolsa','parceria_pd','premio','equity')),
    formato           text,            -- edital_periodico | fluxo_continuo | credenciamento
    setores           text[] NOT NULL DEFAULT '{}',
    tecnologias_tags  text[] NOT NULL DEFAULT '{}',
    status            text,            -- aberta | encerrada | ativa | inativa (NULL se n/a)
    deadline          date,            -- NULL = fluxo contínuo / sem prazo
    uf                text,            -- display/constraint (NULL = nacional)
    ticket_min        numeric,         -- display (cards)
    ticket_max        numeric,         -- display (cards)
    constraints       jsonb NOT NULL DEFAULT '[]',  -- vocabulário §4.4, avaliado por eligibility.py
    requisitos_texto  text[] NOT NULL DEFAULT '{}', -- resíduo não-estruturado (informa sem gate)
    curated           bool NOT NULL DEFAULT false,
    verificado_em     date,
    metadata          jsonb NOT NULL DEFAULT '{}',  -- campos por kind: estagio_alvo/lead_follow/fund_status (investidor), institution_type/contact (ict), cadencia/beneficio (programa)
    embedding         vector(1536),    -- embed da description (busca semântica do explore/catálogo + trilha investidor)
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, native_id)
);
CREATE INDEX idx_entities_kind ON entities(kind);
CREATE INDEX idx_entities_status_deadline ON entities(status, deadline);
CREATE INDEX idx_entities_setores ON entities USING GIN(setores);
CREATE INDEX idx_entities_tags ON entities USING GIN(tecnologias_tags);
```

Validação de `setores` (1-3 itens, vocabulário fechado) é feita em aplicação no ingest — não em CHECK (o CHECK com `array_length` passa por semântica de NULL e dá falsa segurança).

### 5.2 `entity_relationships`

```sql
CREATE TABLE entity_relationships (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id   uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    type        text NOT NULL CHECK (type IN ('operado_por','subordinado_a','exige_parceria_com','credenciada_por')),
    properties  jsonb NOT NULL DEFAULT '{}',
    UNIQUE (source_id, target_id, type)
);
CREATE INDEX idx_er_source ON entity_relationships(source_id);
CREATE INDEX idx_er_target ON entity_relationships(target_id);
```

### 5.3 `match_chunks` — superfície do match (corpus público)

```sql
CREATE TABLE match_chunks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id     uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    idx           int NOT NULL,
    section_path  text[] NOT NULL DEFAULT '{}',
    kind          text,             -- kind do bloco silver (heading/paragraph/…)
    text          text NOT NULL,
    embedding     vector(1536) NOT NULL,
    UNIQUE (entity_id, idx)
);
```

- ~~Embed cru do texto do silver, sem contextual retrieval.~~ **REVOGADO pelo gate da Fase 1.5 (2026-07-10):** a célula `contextual` venceu o cru em TODAS as métricas (MRR 0.505→0.666) — o embedding dos `match_chunks` é **contextualizado** (`core/contextual_retrieval.py`, doc de contexto = blocos temáticos do próprio silver; texto armazenado segue cru, só o vetor muda — mesma convenção de `edital_chunks`). Os motivos originais do cru ((a) contexto institucional como anti-sinal, (c) simetria com o lado empresa) eram hipóteses e foram refutados empiricamente; o custo (d) foi aceito (~US$0,7/corpus, pago só no re-ingest por `source_hash`).
- **Só seções temáticas entram** (objetivos, temas, linhas, escopo). Boilerplate (cronograma, documentação exigida, disposições gerais) é excluído por `section_path`/`kind` — regra no WIKI.md.
- Editais e programas geram chunks (programas: description curada = 1-2 chunks). Investidores/ICTs usam só `entities.embedding` (descrições curtas — single-vector basta).

### 5.4 `company_chunks` — lado empresa (dado de tenant)

```sql
CREATE TABLE company_chunks (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL,
    origin        text NOT NULL CHECK (origin IN ('profile','library_doc','hyde')),
    doc_id        uuid,             -- FK lógica p/ content_library quando origin=library_doc
    text          text NOT NULL,
    embedding     vector(1536) NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
-- RLS obrigatória por workspace_id (mesmo padrão das tabelas de writing).
-- Critério de aceite: leak-test durável (mesmo protocolo do checkpointer LangGraph).
```

Fontes: texto do perfil + documentos da ContentLibrary do workspace (mesmo chunker do silver). **Cold start** (sem docs): HyDE gera pseudo-doc a partir do perfil (`origin='hyde'`, reusa `core/retrieval/hyde.py`), regenerado quando o perfil muda.

## 6. Ingestão gold (`core/kg/gold.py` — `ingest_all()`)

Caráter do módulo: **ingestor com mapeadores determinísticos por fonte + tagger LLM só para editais.** Não é extrator. 3 das 4 fontes já são gold de fábrica.

```
ingest_all():
  investidores.json  → mapa determinístico → entities(kind=investidor) + embed(description)
  programas.json     → mapa determinístico → entities(kind=programa) + operado_por + embed + match_chunks(1-2)
  ict_raw (embrapii) → mapa determinístico → entities(kind=ict) + credenciada_por(EMBRAPII)
                        (tags = areas_raw normalizado; UF via regex do address) + embed
  agencias           → derivadas dos metadados (operador/fonte), upsert idempotente

  editais (por edital, do silver structured_docs + metadados do adapter):
    1. metadados determinísticos (título, agência→operado_por, url, prazo, status, ticket, uf)
       → upsert entities(kind=edital); subordinado_a quando programa-pai identificável (ex. Centelha)
    2. LLM call A — tagger: seções temáticas do silver → {setores: 1-3 da lista, tecnologias_tags: ≤8}
    3. LLM call B — constraints: seções de elegibilidade do silver (via section_path)
       → constraints estruturadas (vocab §4.4) + requisitos_texto residual
       [resolve a pendência: o produtor lê o texto-fonte, não o resíduo do grafo]
    4. normalização determinística de tags (mapa de sinônimos; seed = concept_canon)
    5. embed(description) + embed das seções temáticas → match_chunks
  checkpoint a cada 5, resiliente a erro, idempotente por (source, native_id) + source_hash
```

**Por que o tagger NÃO se funde no structurer:** o structurer é 1 chamada LLM **por página** (transcrição verbatim, "neutro e burro"); o tagger precisa da visão do edital inteiro. Fundir não economiza chamada (vira N opiniões por página + agregação), acopla ciclos de cache incompatíveis (silver re-roda por `source_hash`; tags re-rodam quando a taxonomia muda) e mistura tarefas cognitivas no prompt da camada-fundação.

Dependências: `embedder.py`, `llm_client.py` (factory), `schema.py`/WIKI.md, adapters por fonte, Supabase. **Não depende de:** lib `hyperextract`, passes LLM de canonicalização, `kg_store` blob.

## 7. Match: funil

```
Perfil empresa (workspace) ── company_chunks (§5.4)
         │
         ▼
Stage 0 — Vivo (SQL, determinístico, sem semântica)
    SELECT * FROM entities
    WHERE kind IN ('edital','programa')
      AND (status IS NULL OR status IN ('aberta','ativa'))
      AND (deadline IS NULL OR deadline >= now())      -- NULL = fluxo contínuo PASSA
         │
         ▼
Stage 1 — Elegibilidade (avaliador PR5, camada ÚNICA)
    eligibility.py avalia constraints jsonb vs perfil → sat/unsat/unknown
    unsat elimina; unknown NUNCA elimina (perfil incompleto é o estado normal)
         │
         ▼
Stage 2 — Afinidade (pgvector sobre match_chunks)
    score(edital) = Σ_{c ∈ company_chunks} max_{m ∈ match_chunks(edital)} cosine(c, m)
    (sum-of-max por chunk da empresa — família ColBERT; NUNCA max global)
    + boost opcional por setores ∩ ; piso calibrado no golden (§10)
         │
         ▼
Stage 3 — Precisão (top 5-10)
    reranker cross-encoder (opcional, RERANK_BACKEND) → LLM verdict
    verdict lê: pares de trechos que geraram o score + linha de entities
    (constraints, requisitos_texto, ticket, prazo)
```

**Trilha investidor** (paralela, mesmo endpoint): cosseno perfil-agregado × `entities.embedding` dos investidores com `fund_status='ativo'` + gate de `estagio_alvo`/`setores` do metadata. ICTs no match = pós-beta (backlog existente).

**Explicabilidade (contrato do frontend):** o payload troca `n_paths`/conceitos por `matched_excerpts[]` (par trecho-empresa ↔ trecho-edital, top-3 por score) e `macro_temas` por `setores`. Mostrar o trecho real é estritamente melhor que paths de conceito ("AI drafts, humans decide").

## 8. Explore / mapeamento do ecossistema

Substituição concreta das arestas semânticas — 4 tools sobre SQL (via `entity_catalog.py`):

1. **`search_entities(query, kind=None)`** — busca semântica sobre `entities.embedding` ("quais atores atuam em visão computacional?"). É o consumidor da coluna.
2. **`related_by_tags(entity_id)`** — join por `tecnologias_tags` compartilhadas (GIN) — as arestas semânticas implícitas.
3. **`get_node_neighborhood(entity_id, depth)`** — BFS estrutural via CTE recursiva sobre `entity_relationships`.
4. **RAG leve sobre `description`/`match_chunks`** para o "por quê" (o agente cita o texto).

`entity_catalog.py` mantém as assinaturas de `hypergraph_catalog.py` (`list_editais`, `get_edital`, `get_opportunity`, `list_entity_catalog`, `get_stats`, `investment_offers_by_fund`, …) lendo de `entities` — migração de consumidores sem quebra. `find_matching_entities` (writing) é substituído por `search_entities` com filtro de kind.

## 9. O que morre / fica / é novo

**Morre:** lib `hyperextract` + `hyper_extractor.py` inteiro (incl. `run_hyper_extract_company`, passes canon-fresh e constraints-do-grafo) · `hypergraph_match.py` · `hypergraph_catalog.py` · `_GENERIC_LABELS`/`_is_generic_concept` · curated ICT gate + `curated_icts.json` (vira `curated` bool) · `kg_store.save_hypergraphs`/`load_all_hypergraphs` · `scripts/canonicalize_concepts.py` (CLI) · `company_hypergraphs` · BFS em JSON · tipo Conceito · `data/knowledge_graph/hypergraphs/` como artefato de runtime.

**Fica:** structurer (silver — inalterado) · `eligibility.py` (avaliador único, mesma semântica) · `constraints_producer` (adaptado: input = seções de elegibilidade do silver; output = colunas de entities) · `match_verdict.py` (adaptado: lê entities + matched_excerpts) · `embedder`/`chunker` · **WritingSession inalterada** (RAG contextual lazy sobre `edital_chunks` — intacto, incl. PR #44) · memória do agente (PostgresStore/checkpointer) · `anti_class_verdict` como filtro pós-LLM de tags · a *curadoria* do canon (vira mapa de normalização).

**Novo:** `entities` + `entity_relationships` + `match_chunks` + `company_chunks` (migration 036) · `core/kg/gold.py` · `core/kg/entity_catalog.py` · Stage 0-3 do match · tools §8 · blocos novos no WIKI.md (`setores_taxonomia`, `tag_normalization`, `match_sections`) · flags `MATCH_ENGINE=v2|v3` e `CATALOG_BACKEND=hypergraph|sql`.

## 10. Plano de migração (eval-first)

**Fase 0 — Schema** (1 PR): migration 036 (§5) + RLS de `company_chunks` + `supabase db push`.

**Fase 1 — Gold + ingest** (1 PR): `gold.py` + `ingest_all()` batch; popular entities/rels/match_chunks/embeddings; verificação SQL (§12). Match v2 continua intocado.

**Fase 1.5 — Bake-off offline (GATE — nada de consumidor antes disso):**
- Golden de matching existente (pares empresa→editais, engine-agnostic) rodado contra v2 e v3.
- Métricas: (a) **Stage 0+1 recall** = % de positivos do golden que sobrevivem aos filtros — alvo ~100%; qualquer perda é bug de dados, não trade-off; (b) ranking do Stage 2 vs golden (o gate 0.881 do v2 não é comparável — estabelecer baseline v3 próprio); (c) hard negatives novos de elegibilidade (empresa inelegível × edital tematicamente perfeito).
- Células extras baratas: embed cru vs contextual (fecha a hipótese §5.3); com/sem HyDE no cold start; com/sem boost de setores.
- Calibrar piso do Stage 2 e pesos aqui.

**Fase 2 — Match v3 atrás de flag** (1 PR): `MATCH_ENGINE=v2|v3`; `find_matching_editais` v3 (Stage 0-3) + trilha investidor; payload novo (`matched_excerpts`, `setores`) com adaptação do frontend (`MatchedEditalCard`, `VerdictBlock`, types).

**Fase 3 — Bridge + consumidores** (3 PRs):
- **PR-A** (só `list_editais`/`get_edital`): `entity_catalog.py` + migrar writing_session, checklist_service, writing_tools, critic_agent, planning_node, routers applications/writing.
- **PR-B** (catálogo + explore): explore_tools (tools §8), opportunity_service, source_docs, temporal, routers explore/catalog. **Não migrar `hypergraph_match.py`** — ele morre na Fase 5, migrá-lo é retrabalho.
- **PR-C** (verdict + tasks): match_verdict, tasks.py.

**Fase 4 — Pipeline diário + discovery** (1 PR): `run_daily_etl` = scrapers → bronze → adapter → silver (structurer) → `ingest_all()` incremental (diff por `source_hash`) → embeddings. Substitui `build_all_hypergraphs`. Promote do discovery (admin-only) entra no mesmo caminho silver→gold. `chunk_edital` (writing) permanece lazy.

**Fase 5 — Limpeza** (1 PR): deletar tudo da lista "Morre" (§9), remover flags, atualizar WIKI.md/CLAUDE.md/architecture.md, eval matching aponta só para v3.

## 11. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Constraints extraídas por LLM erradas → filtro mata elegível | Semântica PR5: `unknown` nunca elimina; hard negatives no golden; `requisitos_texto` residual sempre exibido no card |
| Tags fragmentam (iot/IoT/internet das coisas) | Passe determinístico obrigatório no ingest; mapa de sinônimos versionado no WIKI.md; seed = concept_canon |
| Boilerplate domina o Stage 2 mesmo com filtro de seção | sum-of-max limita o dano (1 chunk ruim ≠ score global); regra de seções ajustável no WIKI.md; medido no bake-off |
| Perfil curto → sinal fraco no cold start | HyDE (`origin='hyde'`); a UI já empurra onboarding progressivo de perfil |
| Regressão vs v2 durante transição | Fase 1.5 é gate; `MATCH_ENGINE` flag permite A/B e rollback |
| Vazamento cross-tenant em `company_chunks` | RLS por workspace + leak-test durável como critério de aceite da Fase 0 |
| 6/17 investidores não verificados | `verificado_em IS NULL` → excluídos de gates; exibidos com disclaimer |
| Programas/descrições LLM não verificadas | usar como está (curadoria manual); scrapers pós-beta |
| `ingest_all` lento | asyncio.gather + checkpoint a cada 5; só ~150 editais × 2 calls leves |

## 12. Critérios de sucesso

```sql
SELECT count(*) FROM entities GROUP BY kind;            -- 5 kinds populados
SELECT count(*) FROM entities WHERE kind='edital'
  AND (array_length(setores,1) NOT BETWEEN 1 AND 3);    -- = 0
SELECT count(*) FROM entity_relationships;               -- > 0 (4 tipos)
SELECT count(*) FROM match_chunks;                       -- > 0
SELECT count(*) FROM entities WHERE kind='conceito';     -- erro: kind não existe (CHECK)
```

- Fase 1.5: Stage 0+1 recall ~100% dos positivos do golden; baseline v3 de ranking estabelecido; hard negatives de elegibilidade passam.
- `MATCH_ENGINE=v3` + eval matching ≥ baseline v3 da Fase 1.5.
- `grep -r "hypergraph_catalog\|load_all_hypergraphs\|hyperextract" core/ backend/` → vazio após Fase 5.
- Leak-test de `company_chunks` passa.
- Lazy chunking do writing comprovadamente intacto (nenhum caminho novo escreve em `edital_chunks`).

## 13. Pós-beta (registrado, fora de escopo)

Scrapers de programas/investidores → expansão do mapeamento · ICTs no match (backlog existente) · InovAtiva/RHAE via curadoria · desafios corporativos e CPSI/encomendas (fragmentados, sem catálogo centralizável) · Sebraetec (serviço, não caixa — categoria própria se entrar) · investigar fundos próprios de aceleradoras e BNDES/Criatec (gap de dado, não decisão) · `exige_vinculo_incubacao` por estado conforme editais aparecerem.
