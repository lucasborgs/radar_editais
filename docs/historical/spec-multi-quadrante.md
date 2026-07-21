# spec_multi_quadrante.md — Radar multi-quadrante (evento + entidade)

> **Status:** proposta de arquitetura (2026-06-08). Não-normativo até aprovação.
> Quando aprovado, as partes de schema migram para `WIKI.md` (autoritativo) e
> esta spec vira o "porquê". **Persona-alvo travada:** startup deep-tech
> early-stage. Tudo aqui é otimizado para ela — não é um radar genérico.

---

## 0. A tese em um parágrafo

A startup não quer "editais abertos"; quer **todas as possibilidades de capital
e tração que pode acessar**. Isso são quatro quadrantes (fomento público,
obrigação regulatória, capital privado, aceleração). Eles **não** são quatro
produtos nem quatro fontes — são **um único Knowledge Graph e um único radar**,
com **quatro `node_type`** que se separam por **uma** distinção dura: **evento
vs entidade**. Editais/desafios/programas são *eventos* (têm prazo, vencem, saem
do radar). Fundos são *entidades* (persistem, não vencem, têm tese). Essa
distinção — não o quadrante — é o que ramifica schema, temporalidade, match e
escrita. O resto do sistema permanece compartilhado.

```
                         ┌─────────────── UM RADAR (UI unificada) ───────────────┐
                         │  edital   desafio   programa        investidor        │
                         └───────────────────────┬────────────────────────────────┘
                                                 │
                         ┌──────────────── UM KG (kg_store) ─────────────────────┐
   EVENTO (tem prazo) ►  │ edital · desafio · programa   │   investidor  ◄ ENTIDADE (persiste)
                         │  ─ fluem por core/temporal.py  │  ─ NÃO flui por temporal
                         │  ─ extração → OpportunityExtraction (status/deadline = SSOT temporal)
                         │  ─ match com gate de prazo+eleg.│  ─ match por tese (sem gate, sem prazo)
                         │  ─ escrita = proposta/aplicação │  ─ escrita = pitch/abordagem outbound
                         │  pontes UNIVERSAIS (evento↔entidade): tema · setor      │
                         │  pontes EVENT-SIDE: publico (gate) · fonte (emissor)    │
                         │   └─ no fundo, "publico" = estagio+setor (sem nó próprio)│
                         └────────────────────────────────────────────────────────┘
```

---

## 1. O precedente que já existe: o nó `ict`

Antes de propor `investidor`, registre que **o codebase já provou esse padrão**.
O nó `ict` (WIKI.md §6.1.2) é uma **entidade fora do ciclo de edital**:

| Propriedade de `ict` (hoje) | Vale igual para `investidor` |
|---|---|
| "Uma ICT **não lança edital**" → sem PDF, status, mechanism, vigência | Um fundo não lança edital → sem prazo, status |
| **Não** entra no `SCRAPER_REGISTRY` nem no ETL de edital | Não entra no pipeline de evento |
| Pipeline de ingestão + **artefato próprios** (`icts.json`) | Diretório próprio (`investidores.json`) |
| Liga ao grafo de editais **pela ponte do nó `tema`** (`edital∩ict` por slug) | Liga por `tema`/`setor` (`startup.tese ∩ fundo.tese`) |
| `id_format: "<source>:<slug>"` | `id_format: "investidor:<slug>"` |

**Conclusão:** `investidor` não inaugura uma classe nova de objeto — é o segundo
membro da classe "entidade-nó" que `ict` já abriu. A mesma decisão de design
(artefato próprio, ponte por tema, fora do `temporal.py`) se reaplica. Isso
derruba o custo da minha objeção original: o KG **é** a casa do fundo, exatamente
como já é a casa da ICT.

---

## 2. Taxonomia de `node_type` (proposta para WIKI.md §6.1)

Critério vigente (WIKI.md §355-359): *é nó só se for hub de navegação **e** tiver
identidade própria*. Os quatro passam. Bloco proposto (estende o yaml atual):

```yaml
node_types:
  # ─── EVENTOS (têm ciclo de vida temporal; fluem por core/temporal.py) ───
  edital:        # Q1 — fomento público direto (existe hoje)
    folder: editais
    kind_class: evento
  desafio:       # Q2 — open innovation / obrigação regulatória (ANP, ANEEL, corporates)
    folder: desafios
    kind_class: evento
    extra_tags: ["empresa-ancora/<slug>", "setor/<slug>"]
  programa:      # Q4 — aceleração / incubação (batch/cohort)
    folder: programas
    kind_class: evento
    extra_tags: ["modelo/<equity|no-equity>", "cohort/<ciclo>"]

  # ─── ENTIDADE (persiste; NÃO flui por temporal; artefato próprio) ───
  investidor:    # Q3 — VC / anjo / corporate venture
    folder: investidores
    kind_class: entidade
    artifact: "knowledge_graph/investidores.json"   # espelha icts.json
    tags: [investidor, "estagio/<slug>", "tese/<slug>", "setor/<slug>"]
    emoji: "💸"

  # ─── existentes inalterados ───
  tema: {folder: temas}
  publico: {folder: publicos}
  subprograma: {folder: subprogramas}
  fonte: {folder: fontes}
  ict: {folder: icts}
  home: {folder: ""}
```

**Decisão de modelagem:** `desafio` e `programa` são **eventos** (têm prazo de
inscrição/cohort) → reaproveitam quase tudo de `edital`. Só `investidor` é
entidade → reaproveita o caminho de `ict`. Ou seja, **dois caminhos no código,
quatro tipos no produto.**

`opportunity_type` (campo no card/extração) carrega o discriminador fino para a
UI e para os branches de match/escrita; `kind_class ∈ {evento, entidade}` é o
discriminador grosso que decide o caminho no radar.pipeline.

---

## 3. O que muda, camada por camada (com os seams reais)

### 3.1 Extração — `domain/edital_extraction.py`

Hoje: `EditalExtraction` (v2), source-agnostic, com `DECISION_FIELDS` /
`GATE_FIELDS` / `CONTEXT_FIELDS` e o padrão `Extracted[]`/`absent` de abstenção.
`status`/`deadline` **não** vivem aqui (comentário linha 106-107: SSOT é
`core/temporal.py`).

Proposta: **renomear o conceito para `OpportunityExtraction` e ramificar por
`kind_class`**, preservando o núcleo de evento intacto.

| Campo | edital | desafio | programa | investidor |
|---|---|---|---|---|
| `eligible_entities` (GATE) | ✓ | ✓ | ✓ | — |
| `trl_range` (GATE) | ✓ | ✓ (TRL alvo) | ~ | — |
| `mechanism` (GATE) | ✓ | ~ | ~ | — |
| `counterpart` | ✓ | ~ | — | — |
| `requires_ict_partner` (flag) | ✓ | ~ | — | — |
| **`empresa_ancora`** (novo) | — | ✓ (quem traz a dor) | — | — |
| **`poc_scope`** (novo) | — | ✓ | — | — |
| **`modelo_participacao`** (novo: equity/no-equity) | — | — | ✓ | — |
| **`beneficios`** (novo: capital/mentoria/espaço) | — | — | ✓ | — |
| **`tese`** (novo, texto) | — | — | — | ✓ |
| **`ticket_range`** (novo, {min,max BRL}) | — | — | — | ✓ |
| **`estagio_alvo`** (novo: pre-seed/seed/A) | — | — | — | ✓ |
| **`setores`** (novo) | — | — | — | ✓ |
| **`lead_follow`** (novo) | — | — | — | ✓ |
| `status`/`deadline` (via temporal) | ✓ | ✓ | ✓ | **N/A** |

Mecânica: `OpportunityExtraction` mantém o tronco de evento; um sub-model
`InvestorEntity` (espelhando `EditalExtraction` mas SEM os campos de evento) cobre
a entidade. `GATE_FIELDS` continua existindo **só para eventos** (entidade não
tem gate duro — fundo não desqualifica por CNPJ). O validador
`tests/test_wiki_schema_consistency.py` ganha o eixo `kind_class`.

### 3.2 Temporalidade — `core/temporal.py`

É a SSOT de `status`/`deadline` (lê do índice, calcula dias restantes contra
`date.today()`). **Regra dura:** `kind_class=entidade` **não passa por aqui**.
Um fundo não tem `[CONTEXTO TEMPORAL]`. Em vez disso ganha um eixo de
**frescor** (`verificado_em`, `dormante?`) — análogo conceitual ao status, mas
de *staleness de curadoria*, não de vigência legal. Isso evita o bug óbvio de um
fundo aparecer como "ENCERRADO" porque não tem `deadline`.

### 3.3 Descoberta / ingestão

Três torneiras, todas convergindo no mesmo KG:

| Quadrante | Mecanismo | Onde encaixa no código |
|---|---|---|
| Q1 evento | Hub (FINEP/FAPESP adapters) **+ feeder DOU** | `source_adapters` §12.4 + novo feeder em `opportunity_discovery` |
| Q2/Q4 evento | seed list de operadores/agregadores + Tavily | `web_sources` (migration 018) + triagem por tipo |
| Q3 entidade | **diretório curado** + re-enriquecimento periódico | novo: `investidores.json`, pipeline espelhando `icts.json` |

**O DOU como feeder (não adapter):** a Descoberta hoje (`opportunity_discovery.py`)
é "a torneira automática da fonte web" — candidatos do Tavily passam por
`_triage` → `_extract` → bronze `web_raw`. O DOU entra **exatamente como segundo
gerador de candidatos**, paralelo ao Tavily, alimentando o mesmo
`discover_opportunities()`. Ganho: precisão alta + agência emissora de graça.

**Gotcha cravado no código:** `_TRIAGE_SYSTEM` (`opportunity_discovery.py:94`)
está hard-wired em "fomento à inovação" e **rejeita** página institucional. Isso
mata `desafio`/`programa`/`investidor` na entrada. A triagem precisa virar
**per-`opportunity_type`** (ou um classificador que rotula o tipo em vez de
binário is_opportunity). Idem o `tema` restrito ao vocab §5.9 no `_extract`.

### 3.4 Match

Dois motores já existem e mapeiam limpo nos dois `kind_class`:

| | Evento (edital/desafio/programa) | Entidade (investidor) |
|---|---|---|
| Motor | `HybridMatchService` (Stage1 determinístico + Stage2 LLM) | `KGMatchService` (raciocínio sobre grafo, sem embeddings) |
| Semântica | **gate** de elegibilidade + vigência + tema | **alinhamento** de tese/estágio/setor |
| Sinal "por que agora" | countdown do `deadline` | força do match (sempre aberto) |
| Risco | já tratado | **inundação** → ranking é o produto |

Mudanças concretas:
- `KGMatchService.MATCH_SYSTEM_PROMPT` (linha 29) está cravado em "editais FINEP".
  Precisa virar **type-aware**: o mesmo motor que hoje rankeia editais é o certo
  para rankear fundos por tese — mas o prompt e o formato do card diferem.
- `HybridMatchService` já tem a dimensão soft sobre `eligibility_constraints`
  (linhas 446-488); para `desafio`/`programa` ela reaproveita; para `investidor`
  **não roda** (sem gate).
- `_get_index_for_prompt()` formata o índice assumindo campos de edital
  (`status`, `deadline`, `themes`). Precisa de um formatador por `kind_class`.

### 3.5 Escrita — `core/writing_session.py`

`OUTLINE_SYSTEM` (linha 98) e `WRITER_AGENT_SYSTEM` (linha 112) estão cravados em
"propostas para editais de fomento". A escrita é o ponto de **maior divergência**
entre tipos — não é template, é gênero:

| `opportunity_type` | Gênero | Público avalia | Substrato de RAG (`retrieve_chunks`) |
|---|---|---|---|
| `edital` | proposta formal | conformidade + mérito vs escopo | chunks do **edital** (artigos) |
| `desafio` | solução→problema | fit com a dor da **empresa-âncora** | enunciado + perfil da âncora |
| `programa` | application/form | time, tração, fit ao programa | regras do programa |
| `investidor` | **pitch / one-pager / cold outreach** | **retorno** (TAM, time, tração, ask) | **tese + portfólio do fundo-alvo** |

A virada de `investidor`: a escrita de edital é *inbound, rule-bound* (cumpra o
edital). A de fundo é *outbound, personalizada* (mostre por que você encaixa na
tese **daquele** fundo). Não há "artigo a cumprir" — o que condiciona o texto é o
**nó do fundo no KG**. Por isso a integração no grafo (§1) não é cosmética: é o
insumo de RAG da escrita outbound. Implementação: `WritingSession` ganha um
`mode` derivado de `opportunity_type` que seleciona o par
(system_prompt, retrieval_target).

### 3.6 Perfil — `domain/user_profile.py`

`CompanyProfile` hoje serve match de edital (trl, tamanho_empresa,
eligibility, `tipos_financiamento_interesse`). Para `investidor`/`desafio`
faltam campos que o **outro lado** avalia:

```python
# novos campos (investor/challenge-facing) — opcionais, não quebram match de edital
estagio: str = ""              # pre-seed | seed | serie-a  (≠ TRL)
mrr_arr: float | None = None   # tração financeira
round_alvo_brl: float | None   # quanto está captando (casa com ticket_range do fundo)
cap_table_resumo: str = ""     # quem já investiu / equity disponível
tracao_resumo: str = ""        # clientes, pilotos, cartas de intenção (≠ portfolio_projetos)
```

`to_context()` já é o ponto único de serialização para prompt — os campos novos
entram nela condicionalmente (mesmo padrão dos atuais). Crucial: `is_complete()`
/ `completion_pct()` passam a ser **relativos ao quadrante** — um perfil "completo
para edital" pode estar incompleto para investidor. Isso vira sinal de UX
("complete X para destravar matches de capital privado").

### 3.7 Grafo / pontes

`investidor` e `desafio` se ligam ao grafo de eventos pela **mesma estratégia da
ICT**: interseção de `tema`/`setor` (sem aresta direta startup↔fundo, computada
por slug compartilhado). Novos `link_types` (WIKI.md §6.2):

```yaml
investidor_has_thesis_theme: {from: investidor, to: tema}
desafio_posted_by:           {from: desafio, to: fonte}   # empresa-âncora vira fonte
```

---

### 3.8 Estágios de matching (kind_class-aware)

**Estado atual (event-shaped).** Dois motores coexistem como CLASSE, mas só um
faz match em produção:

| Motor | Método | Onde roda hoje |
|---|---|---|
| `HybridMatchService` | 2 stages: **Stage 1** determinístico (Pandas, pontua 6 dims/100, **elimina** < 25 = gate) → **Stage 2** LLM temático nos sobreviventes | `/match` + `core/eval/matching` (**produção**) |
| `KGMatchService.match()` | 1-shot: índice inteiro + perfil num prompt, LLM rankeia tudo (score-gestalt 0-10, sem gate, sem embeddings) | **dormente** — nenhum endpoint de match o chama; a classe serve `explore`/`get_graph`/`resolve_scope` |

> Nota: o sumário de `/match` ([api.py:414](../../backend/api.py)) ainda diz
> "Karpathy-style" — string stale da época em que o KG era o matcher. Hoje quem
> roda ali é o HybridMatch.

**Alvo pós-multi-quadrante.** O esqueleto de 2 stages **generaliza**; o que muda
por `kind_class` é (a) se o Stage 1 **elimina** (gate) ou só **pontua** (soft),
(b) quais dimensões, (c) sobre o quê o Stage 2 raciocina. Nasce um terceiro
layer (merge/rank) que não existe hoje.

```
                        PERFIL (CompanyProfile expandido)
                                    │
              ┌─────── Layer 0: roteia por kind_class ───────┐
              ▼ EVENTO (edital/desafio/programa)       ▼ ENTIDADE (investidor)
   Stage 1: GATE determinístico (elimina < 25)   Stage 1: SCORE soft (NÃO elimina)
     dims: elegib·tema·trl·mecanismo·contrap        dims: estágio·setor·ticket-fit
   Stage 2: LLM temático                          Stage 2: LLM de TESE (holístico,
     (descrição × tema do edital)                   estilo KGMatch: perfil × tese+portfólio)
              └──────► Layer 2: MERGE + RANK unificado ◄─────┘   ◄── NOVO
                       normaliza scores heterogêneos · sinal "por que agora"
                       (countdown p/ evento vs força-de-tese p/ entidade) ·
                       cap por quadrante (anti-inundação) → UM ranking
```

**Contrato por layer:**

| Layer | Entrada | Saída | Reuso |
|---|---|---|---|
| L0 roteamento | perfil + universo de oportunidades | candidatos agrupados por `kind_class` | novo (fino) |
| L1 evento | candidatos evento | lista pontuada 0-100 + sobreviventes do gate | **HybridMatch intacto** (desafio/programa = tweak de dims) |
| L1 entidade | candidatos entidade | lista pontuada (sem eliminação) | esqueleto reusado + **filosofia** do KGMatch no Stage 2 (não o código FINEP-hardcoded) |
| L2 merge/rank | listas heterogêneas | **um** ranking pro radar | **net-new** — é onde "match = produto" mora |

**Nota — Stage 2 de entidade é candidato a GraphRAG, não a índice plano.**
Cuidado com a herança: o `KGMatchService.match()` atual, apesar do nome "KG",
**não percorre grafo** — lê o `index.json` (lista plana, ~150 chars/edital) e só
carrega wiki pages pro top-3 pós-ranking. Para evento isso basta (ranking decide
sobre campos estruturados). Para **entidade**, porém, o sinal de tese mora no
**nó enriquecido do fundo** (tese + portfólio + co-investidores), não numa linha
rasa. Logo o Stage 2 de tese deve raciocinar sobre a **wiki page do fundo +
vizinhança no grafo** (setor/tema), ingerindo-a no contexto — GraphRAG de fato,
o caminho que o `.match()` justamente NÃO usa. O que se herda do KGMatch é a
*postura* holística (sem gate), não a mecânica de índice plano. As rotas de
grafo já existentes (`resolve_scope`, `_find_analogue_ids`, `get_graph_neighbors`)
são o ponto de partida do traversal.

**Impacto em `matching_weights` (ADR A5):** hoje os pesos
(hybrid_match_service.py:80) são 6 dimensões de
evento. Entidade tem dimensões diferentes (tese/estágio/setor/ticket) → **perfil
de pesos próprio por `kind_class`**. O cache (hoje keyed em `workspace_id`) passa
a `(workspace_id, kind_class)`. `_ELIMINATION_THRESHOLD` fica **event-only** —
entidade nunca elimina (fundo bom de tese mas estágio torto não pode sumir).

**Sequência vs objetivo "robustez antes de afinar match":** L0+L1-evento são
quase de graça (reuso). L1-entidade calibra o Stage 2 de tese. **L2 é onde a
iteração de qualidade vai morar** — pode sair INGÊNUA no MVP (eventos por
urgência + entidades por score, intercalados com cap por quadrante) e ser
afinada na fase dedicada de matching. O multi-quadrante **não obriga** resolver
o ranking unificado agora; obriga apenas **criar a costura** (L2) onde não havia.

---

### 3.9 Grafo curado (base) vs induzido (overlay): custo + binding

A pergunta "GraphRAG curado ou induzido?" é falsa dicotomia. **Curado é a base;
induzido é overlay cirúrgico.** Duas camadas, custos opostos:

```
Camada A — CURADA, viva, incremental   ← substrato de MATCH/ESCRITA (já existe)
   arestas de schema (edital→tema→…), exata, sempre fresca, custo ~zero/update

Camada B — INDUZIDA, periódica, insight ← overlay OPCIONAL (fora do request-path)
   extração livre de entidades/relações + comunidades; batch (trimestral),
   corpus BOUNDED, tolera stale → custo amortizado e com teto
```

**Regra de custo:** o loop central (descobrir→match→escrever) **nunca paga
indução**. Indução ∝ corpus e é hostil à incrementalidade (um doc novo desloca
comunidades → re-sumariza), então roda em batch sobre fatia delimitada, feeding
uma **superfície de insight separada**. No query-time se lê resumo pré-computado,
não se induz.

**Veredito por quadrante:** match Q1/Q2/Q4 (evento) e escrita = curado basta
(local; indução não melhora 1-pra-N). **Alvo 1 da indução = rede de fundos (Q3)**:
corpus ~30-50 → barato, e a relação (co-investimento, sobreposição de tese, quem
segue quem) é não-óbvia e invisível à curadoria. **Prêmio maior** = insight
longitudinal cross-quadrante ("memória da evolução das agências") — superfície à
parte, batch, fase posterior.

**Binding obrigatório (senão o insight flutua):** a indução é templada como
**contrato de reconciliação**, não extração livre. Toda saída induzida precisa
(a) ser **tipada** (`node_type`/`link_types` existentes, ou flag candidato-novo)
e (b) **resolver cada ponta a um id canônico** (`investidor:kptl`, não um nó novo
solto). É isso que liga o resultado induzido às wiki pages **curadas**. Reusa dois
primitivos já existentes: `themes_proposed` (quarentena do que não casou) +
`verificacao: provisorio` (gate de graduação). A indução **nunca** escreve direto
no grafo vivo — propõe; a reconciliação é o pedágio (e onde mora o custo
controlado da Camada B). Sem esse contrato, indução = segundo grafo desconectado.

---

## 4. Identidade por tipo (id_format)

| type | id_format | exemplo |
|---|---|---|
| edital | `<source>:<native_id>` (existe) | `finep:589` |
| desafio | `<operador>:<slug>` | `petrobras:conexoes-co2-2026` |
| programa | `<operador>:<slug>` | `baita:deeptech-2026-c1` |
| investidor | `investidor:<slug>` | `investidor:kptl` |

---

## 5. Sequência de implementação (amarrada na dívida existente)

Ordenada por **ROI × reuso**, encaixando no que já está em voo:

**Fase A — DOU feeder + cauda longa de FAPs (evento puro, reuso ~100%)**
Nenhum tipo novo. Só um segundo gerador de candidatos no
`opportunity_discovery` + alargar a triagem para aceitar editais de FAPs
estaduais. Mata o "fica cego" original com o pipeline que já existe.
*Gate:* nenhum schema novo; só cobertura.

**Fase B — `desafio` + `programa` (eventos, reuso ~80%)**
Adiciona dois `node_type` evento. Toca: `OpportunityExtraction` (campos novos),
triagem per-type, formatador de índice no `KGMatchService`, `mode` de escrita.
Depende de pagar parte da dívida §12.5 (extração ainda presa em `finep`/PDF).
*Gate:* rodar `python -m radar.core.eval extraction` e `matching` antes de mergear —
o baseline 0.95/0.95/0.92 (spec_extraction) não pode regredir.

**Fase C — `investidor` (entidade, caminho novo mas espelha `ict`)**
Cria `investidores.json` + pipeline de diretório (copy estrutural de
`icts.json`), prompt de match por tese no `KGMatchService`, `mode=pitch` na
escrita, campos de perfil novos. **Não toca** `temporal.py`, `HybridMatch`,
GATE_FIELDS.
*Gate:* `python -m radar.core.eval writing` com um golden de pitch antes de expor —
gênero novo, risco de alucinação alto. Exige `EVAL_WORKSPACE_ID`.

---

## 6. Riscos e gates

| Risco | Mitigação |
|---|---|
| **Inundação** (startup vê 100 matches fracos) | Ranking vira produto; cap por quadrante na UI; `is_complete` por quadrante segura match ruim |
| Triagem alargada deixa entrar lixo (notícia, blog) | Classificador per-type com precisão medida; manter o ledger de dedup |
| Escrita de pitch alucina (sem "artigo a cumprir") | Gate de eval writing (Fase C); RAG **obrigatório** sobre o nó do fundo, nunca free-form |
| `investidor` poluir o pipeline de evento | `kind_class` barra na entrada de `temporal.py`/GATE_FIELDS; espelhar `ict`, não `edital` |
| Schema drift doc×código | `test_wiki_schema_consistency.py` ganha o eixo `kind_class` |
| Re-enriquecer fundos vira custo recorrente | Cadência trimestral (não diária); `verificado_em` + soft-delete como `content_library` |

---

## 6-bis. Pente fino: blast radius e estrutura de mudança (de-risk 2026-06-08)

Auditoria do acoplamento real. "edital" é a **espinha**: 69 `.py` + 15
frontend + schema do `index.json` (`{editais, total_editais, summary.by_status}`,
lido por `temporal.py`/stats/match) + banco (`edital_chunks`,
`application_log unique(workspace, edital_id)`, `session_turns`) +
`build_knowledge_graph._build_editais()`.

**Regra de ouro: a mudança é ADITIVA e ISOLADA, nunca um rename.** Generalizar
`edital → oportunidade` tocaria ~84 arquivos + migração. Feito aditivo, colapsa
para ~10-12 arquivos, **zero migração de banco**. As 6 invariantes que protegem
a estrutura existente:

| # | Invariante | Por quê |
|---|---|---|
| ① | **investidor NÃO entra no `index.json`** — artefato próprio `investidores.json` (espelha `icts.json`); o radar junta na **leitura** (match/UI) | `index.json` alimenta `temporal.by_status` + stats; misturar entidade quebra os dois |
| ② | **NÃO renomear `EditalExtraction`** ([edital_extraction.py:80](../../domain/edital_extraction.py)) — ela é o schema de EVENTO (+campos opcionais); `InvestorEntity` é model separado | classe load-bearing; rename = 69 arquivos |
| ③ | **`node_type` ≠ `kind_class`** — desafio/programa são pastas novas no grafo mas **andam no pipeline de evento existente** (mesma tabela/índice/temporal), distinguidos por campo `opportunity_type` | "tipos separados" é decisão de grafo/UI, não de pipeline; eles *são* editais estruturalmente |
| ④ | **Zero migração de banco** — `edital_id` é `text` prefixado sem FK → tratar como `opportunity_id` genérico (`petrobras:x`, `investidor:kptl`) | nome da tabela vira dívida de nomenclatura, não problema estrutural |
| ⑤ | **Precedente `ict` cobre só dados+ponte** — match-ranking, escrita-pitch e card de UI de investidor são **net-new** (isolados, mas novos) | ICT nunca teve match-to-user nem escrita; não subestimar investidor |
| ⑥ | **Frontend: grafo estende barato** (lista hardcoded em KnowledgeGraph.tsx:56 + `NODE_STYLE`), **card de match de investidor é net-new** (`KGMatchResult` assume status/deadline) | |

**Consequência:** editais fica intocado **por construção** → baseline de eval
0.95 protegida por arquitetura, não por disciplina. Arquivos tocados (aditivos):
triagem per-type, extração (+campos opcionais + `InvestorEntity`), build_kg
(emite pastas novas + `investidores.json`), `kg_match_service` (prompt type-aware
+ merge na leitura), `writing_session` (branch de `mode`), `user_profile`
(+5 campos), `WIKI.md`, 3 de frontend, +diretório novo, +feeder DOU.

**Tensão com o objetivo "robustez antes de afinar match/RAG/escrita":**
investidor é o tipo cujo match (tese) e escrita (pitch) mais divergem — as
superfícies a afinar depois. O isolamento torna seguro lançar, mas **a qualidade
do pitch é a aresta áspera assumida do MVP**. Fase A (DOU) é robustez pura, zero
tipo/superfície nova — é a que melhor serve "fundação robusta primeiro".

---

## 7. O que NÃO fazer (anti-scope)

- **Não** criar um quinto motor de match nem um harness de eval paralelo
  (CLAUDE.md: registrar suíte em `core/eval/registry.py`).
- **Não** enfiar `investidor` em `temporal.py`/`status`/`deadline`.
- **Não** construir ETL bespoke para "Top 5" — o Hub fica em FINEP+FAPESP; o
  resto é torneira (DOU/seed/diretório).
- **Não** expandir vocab de `tema` ad hoc no `_extract` da Descoberta — passa
  pelo normalizador do build (defesa final §5.9).

---

## 8. Decisões (travadas 2026-06-08)

1. ✅ **`desafio` e `programa` = dois `node_type` separados.** Match e escrita
   divergem o suficiente (PoC-para-âncora vs application-de-cohort) para
   justificar dois caminhos honestos em vez de if-soup sob um subtipo.
2. ✅ **`investidor` entra no MVP** (não é Fase C diferida). Maior valor
   incremental para a persona. Custo mitigado pela decisão #3.
3. ✅ **Diretório de fundos = curadoria manual semente.** ~30-50 fundos
   deep-tech-relevantes (KPTL, MOV, Antler, Norte, Baita…) curados à mão uma
   vez. Automação de descoberta fica para a manutenção, não para o MVP — isso
   é o que torna #2 viável sem alargar demais a frente.
4. ⏳ **`empresa_ancora` de `desafio`: nó `fonte` ou campo?** Ainda aberta. Se a
   Petrobras posta 10 desafios, virar `fonte` dá hub de navegação (igual
   agência). Decidir na Fase B, quando o primeiro operador real entrar.

> **Impacto de #2 no §5:** investidor sai de "Fase C diferida" e passa a integrar
> o MVP. A sequência **A → B → C ainda vale como ordem de construção** (cada uma
> reusa mais que a próxima), mas as três compõem o MVP, não A+B só. A curadoria
> manual (#3) significa que o custo de C colapsa para: prompt de match-por-tese
> no `KGMatchService` + `mode=pitch` na escrita + 5 campos novos de perfil — o
> diretório em si é um JSON semeado à mão, não um radar.pipeline.
```
