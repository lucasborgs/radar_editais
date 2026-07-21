# Radar de Editais — Schema e vocabulários

> **Autoridade:** regras de domínio e vocabulários consumidos pelo código. Para
> runtime e demais documentos, consulte o
> [índice da documentação](docs/README.md).

Este documento é o **schema autoritativo** dos vocabulários e contratos de
ingestão. O catálogo ativo é o gold relacional (`entities`,
`entity_relationships`, `match_chunks`); blocos anteriores ao v3 permanecem
identificados como compatibilidade somente quando ainda são lidos por
`src/radar/core/kg/schema.py` ou por ferramentas locais.

O código consome os blocos YAML deste doc em runtime via `radar.core.kg.schema`; os
testes de schema e vocabulário cobrem os accessors ativos. **Mudanças de regra
devem começar aqui**, e não em constantes paralelas no código.

---

## 1. Propósito

O Radar de Editais é uma **plataforma de inteligência e automação de escrita
para fomento à inovação**. Bronze imutável e documentos silver alimentam o
catálogo gold relacional. O match usa trechos reais em `match_chunks`; a escrita
usa `edital_chunks`; catálogo e Explore consultam entidades e relações SQL.

O sistema atende dois propósitos:

**Operação (foco no proponente).** Matching de alta precisão entre perfil de PMEs e editais **vigentes** (inicialmente FINEP), mais assistente de escrita baseado em contextos históricos e técnicos validados.

**Inteligência (foco longitudinal).** Editais encerrados permanecem no bronze e
no silver reconstruível; a vigência é aplicada no Stage 0 do match, não por um
índice JSON separado.

> **Classificação PME/startup (legado):** o vocabulário preservado em
> `wikis/_pme_filter.md` continua parseável para compatibilidade, mas não há
> classificador PME ativo no pipeline gold.

> **Arquitetura de camadas:** o vocabulário oficial das camadas, a fronteira
> agnóstico/individualizado e o contrato de Documento Canônico estão em **§12**.
> Termos ad-hoc anteriores ("Ramo A/B") estão depreciados — ver §12.

---

## 2. Artefatos

| Artefato | Path | Descrição |
|---|---|---|
| Bronze | `data/bronze/` | Captura imutável por fonte e documentos oficiais |
| Documento canônico | Postgres: `edital_source_docs` (fallback no bronze local) | Conteúdo durável e agnóstico de fonte entregue pelos adapters |
| Doc estruturado (silver) | `data/silver/structured_docs/{source}/{id}.jsonl` | Blocos estruturais usados pelo gold e pelo RAG de escrita. Ver §11 |
| Catálogo gold | Postgres: `entities`, `entity_relationships`, `match_chunks` | Catálogo, relações e trechos do match v3 |
| Chunks de escrita | Postgres: `edital_chunks` | Contexto da WritingSession, aquecido diariamente e garantido sob demanda |
| Estado da descoberta | Postgres: `discovered_opportunities`, `discovery_promotion_runs` | Staging e execução do gate humano |
| Vault Obsidian | `data/hyper_extract_output_v2/vault/` | Espelho Markdown do gold, regenerado pelo ETL diário ou via `scripts/export_to_obsidian.py` |

---

## 3. Fontes

Cada fonte tem um schema específico em `wikis/<fonte>.md` que **estende** este doc global. Regras definidas aqui valem para todas; regras em `wikis/<fonte>.md` sobrescrevem quando aplicável.

| Fonte | Schema | Status |
|---|---|---|
| FINEP | [wikis/finep.md](wikis/finep.md) | ativo (v1) |
| FAPESP | [wikis/fapesp.md](wikis/fapesp.md) | ativo (v1) |
| FAPESC | [wikis/fapesc.md](wikis/fapesc.md) | ativo (v1) |
| Web | regras globais §12.4 + [wikis/_discovery.md](wikis/_discovery.md) | ativo, com gate humano |
| EMBRAPII | extractor curado `src/radar/pipeline/extractors/ict_embrapii.py` | ativo para ICTs |

---

## 4. Representação atual e histórico de wiki pages

O catálogo atual não produz nem consome wiki pages JSON. Editais, programas,
investidores e ICTs são entidades do gold relacional; seus contratos ativos
estão no §13 e na implementação de `src/radar/core/kg/gold.py`.

O schema das wiki pages e os índices JSON pertencem à linhagem hyper-extract,
removida no v3. A descrição histórica foi preservada em
[`docs/historical/kg-entity-wiki-pages.md`](docs/historical/kg-entity-wiki-pages.md)
e [`docs/historical/hypergraph-architecture.md`](docs/historical/hypergraph-architecture.md).
Esses documentos não são autoridade de runtime.

---

## 5. Vocabulários

### 5.1 Mechanism (financial)

```yaml
mechanism:
  subvencao:    "Subvenção (não reembolsável)"
  reembolsavel: "Crédito reembolsável"
  investimento: "Investimento direto"
  misto:        "Misto"
  null:         "Não informado / não se aplica"
```

### 5.2 Eligible entities

Valores abertos, mas sugeridos para consistência:

```yaml
eligible_entities_suggested:
  - empresas
  - startups
  - ICTs
  - universidades
  - institutos_de_pesquisa
  - cooperativas
  - pessoas_fisicas
```

### 5.3 Status

```yaml
status:
  ABERTA:              { emoji: "🟢", tag: "aberta",       order: 0 }
  Desconhecido:        { emoji: "⚪", tag: "desconhecido", order: 1 }
  ENCERRADA:           { emoji: "🔴", tag: "encerrada",    order: 2 }
  RESULTADO_DIVULGADO: { emoji: "🟡", tag: "resultado",    order: 3 }
```

### 5.4 Fontes canônicas (siglas)

Mapeamento case-insensitive → sigla canônica. Usado pelo normalizador de `fonte_recurso`. **Uma fonte é uma instituição que paga** (agência de fomento, banco público, empresa com cláusula regulatória de PD&I, bloco internacional). Modalidades de aplicação (subvenção, reembolsável) pertencem a §5.7; fundos setoriais e programas específicos pertencem a §5.6.

```yaml
fontes_canonicas:
  finep:          FINEP
  fndct:          FNDCT
  mcti:           MCTI
  mec:            MEC
  bndes:          BNDES
  capes:          CAPES
  cnpq:           CNPq
  embrapii:       EMBRAPII
  sebrae:         SEBRAE
  petrobras:      Petrobras
  eletrobras:     Eletrobras
  bb:             BB
  caixa:          CAIXA
  bnb:            BNB
  uniao europeia: União Europeia
  ue:             União Europeia
```

Split de valores multi-fonte por: `;` `|`. **Não** usar `,` (aparece legitimamente dentro de nomes de temas, ex.: `"Agricultura, agronegócio e saúde animal"`) nem `/` no splitter global (tratado pelo normalizador: `FINEP/FNDCT` → extrai ambas as siglas da mesma string via regex `\b{sigla}\b` case-insensitive, **sem** early-break).

Fragmentos que não casam com nenhuma canônica, nem com §5.6, nem com §5.7 são descartados silenciosamente. Contexto regulatório não-financiador (ex.: `"conforme Resolução nº 918/2023 da ANP"`) permanece no texto estruturado e nos requisitos textuais do gold, não vira relação.

### 5.5 Públicos canônicos

Lista fechada de tipos de proponente aceitos. Modificadores em parênteses (ex.: `"(Fundações)"`, `"(Pública ou Privada)"`) são descartados — a especificidade permanece em `requisitos_texto` e nos chunks de evidência.

```yaml
publicos_canonicos:
  empresa:                                             Empresas
  empresas:                                            Empresas
  startup:                                             Startups
  startups:                                            Startups
  ict:                                                 ICTs
  icts:                                                ICTs
  instituicao de pesquisa:                             Instituições de pesquisa
  instituicoes de pesquisa:                            Instituições de pesquisa
  universidade:                                        Universidades
  universidades:                                       Universidades
  cooperativa:                                         Cooperativas
  cooperativas:                                        Cooperativas
  gestor de fip:                                       Gestores de FIP
  gestores de fip:                                     Gestores de FIP
  gestores de fundos de investimento em participacoes: Gestores de FIP
```

Normalização: lowercase + strip de acentos + match por substring. Strings que não casam com nenhum valor canônico (ex.: prosa longa com qualificadores específicos) são descartadas.

### 5.6 Subprogramas e fundos setoriais

Programas específicos e fundos setoriais que não são fontes financiadoras em si, mas agrupam editais de uma linha temática ou regime regulatório próprio. Nó de grafo separado de fonte (§5.4); distinto da modalidade financeira `mechanism` (§5.1), que é tag.

```yaml
subprogramas_canonicos:
  ct-infra:   CT-Infra
  ctinfra:    CT-Infra
  ct-hidro:   CT-Hidro
  ct-agro:    CT-Agro
  funttel:    Funttel
  rota 2030:  Rota 2030
  mover:      MOVER
  centelha:   Centelha
```

Extração na mesma string de `fonte_recurso` bruta, após §5.4. Case-insensitive, regex `\b{alias}\b`. Múltiplas matches na mesma string são permitidas.

### 5.7 Drop-list de modalidades

Termos que aparecem no campo `fonte_recurso` do portal mas **não são fontes** — são modalidades financeiras (já cobertas por `mechanism`, §5.1). Descartados silenciosamente pelo normalizador.

```yaml
modalidades_drop_list:
  - subvencao
  - subvencao economica
  - reembolsavel
  - investimento direto
  - cooperativo
  - cooperativo ict
  - cooperacao internacional
  - acao transversal
  - acoes transversais
  - recursos proprios
  - proprios
  - proprio
```

Match: lowercase + strip de acentos + comparação por substring. Uma string bruta pode cair simultaneamente em §5.4, §5.6 e §5.7 — cada normalizador extrai o que reconhece da mesma string.

### 5.8 Faixas TRL

O campo `trl_range: {min, max}` é numérico (1-9). Colapsa-se em 3 faixas semânticas usadas como dimensão de matching/filtro, não como entidade do gold (§6.1). Um edital recebe todas as faixas que sobrepõem seu range.

```yaml
trl_faixas:
  trl_pesquisa:   { min: 1, max: 3, label: "TRL 1-3 (Pesquisa)" }
  trl_prototipo:  { min: 4, max: 6, label: "TRL 4-6 (Protótipo)" }
  trl_industrial: { min: 7, max: 9, label: "TRL 7-9 (Industrial)" }
```

Regra de mapeamento: edital com `trl_range = {min: a, max: b}` recebe a faixa `f` se `max(a, f.min) <= min(b, f.max)`. Se `trl_range` é `{null, null}`, sem tag de faixa.

### 5.9 Temas canônicos (áreas estratégicas)

Vocabulário **canônico e autoritativo** de temas-macro (`tema`, §6.1). É o alvo
único para o qual TODOS os produtores de tema convergem:
- **Editais**: hoje as fontes (FINEP/FAPESP) já rotulam com essas macro-áreas;
  `src/radar/domain/vocabulary.canonicalize_themes` (stub) deve, ao evoluir, mapear
  variações para esta lista.
- **ICTs** (§6.1.2): o normalizador fino→macro de `build_ict_graph` mira nesta
  lista — é o que garante que `ict.themes` e `edital.themes` compartilhem
  representação (a ponte do grafo).

Um tema só entra aqui quando há **evidência de cobertura** (editais e/ou ICTs que
o exijam). `materiais, química e manufatura avançada` foi adicionado a partir de
~66 áreas de expertise de unidades EMBRAPII sem tema-macro correspondente
(materiais/compósitos/ligas, manufatura/processos, química/insumos, estruturas) —
ver `themes_proposed_index` em `icts.json`.

```yaml
tema_vocab:
  - "agro - bioeconomia e alimentos"
  - "energia e transição sustentável"
  - "espaço - defesa e segurança"
  - "materiais, química e manufatura avançada"
  - "mobilidade e logística"
  - "saúde e ciências da vida"
  - "tecnologias digitais e conectividade"
```

Invariante (test_wiki_schema_consistency): todo tema usado por editais ou ICTs
deve estar nesta lista. Tema novo no corpus sem entrada aqui = quebra o
validador → decida (adicionar ao vocab ou corrigir o produtor).

Variações conhecidas mapeadas em `src/radar/domain/vocabulary._SYNONYMS` (2026-06-11):
a taxonomia Liferay da FINEP usa "Indústria e Materiais Avançados" para o
tema de materiais. As demais categorias da taxonomia fora do vocab são
mecanismos/programas ("Subvenção Econômica", "Seleção de Gestores…") ou temas
sem cobertura ("Meio Ambiente - Água e Clima", "Cidades…", "Educação…") — o
filtro de vocabulário do build os descarta por design; entram no vocab apenas
com evidência de cobertura (regra acima).

**Evolução do vocabulário (vocab lint):** `python -m radar.core.vocab_lint` coleta o
sinal de demanda (variações fora do vocab no índice + `tema_livre` da Descoberta
web + oportunidades sem-tema), pede UMA proposta à LLM (tier barato) e grava um
relatório em `data/vocab_lint/` com diff pronto-pra-colar. O lint NUNCA aplica
nada — ele PROPÕE; o humano aplica AQUI (o doc é o dono) e em `_SYNONYMS` se for
o caso ("AI drafts, humans decide"). Barra conservadora: tema novo só com **≥3
evidências independentes**; sinônimo só com equivalência semântica clara a um
tema existente. Aplicação fica gated por `test_wiki_schema_consistency` + evals
de não-regressão (`radar.core.eval matching`, `opportunity_type`).

### 5.9.1 Vocabulários do multi-quadrante

`setor` (vertical de indústria) é DISTINTO de `tema` (domínio tecnológico) — para
deep-tech costumam coexistir (tema=`saúde e ciências da vida`, setor=`saude`).
`estagio`/`modelo` servem o quadrante investidor/programa (spec_multi_quadrante).

```yaml
setor_vocab:
  - oleo-gas
  - energia
  - saude
  - agro
  - defesa
  - industria
  - financeiro
  - mobilidade
  - meio-ambiente
  - espacial
  - ti-software
  - multissetorial
estagio_vocab: [pre-seed, seed, serie-a, growth]
modelo_vocab: [equity, no-equity]
```

### 5.10 Exigência de parceria com ICT (`requires_ict_partner`)

Campo de decisão do contrato `EditalExtraction`, usado pela suíte de avaliação
de extração. Quando o texto afirmar a exigência, o valor deve vir acompanhado
de estado `stated` e evidência verbatim; quando não afirmar, o extrator deve se
abster (`absent`, valor/evidência nulos). O campo não é gate do match nem relação
gold. O histórico da antiga heurística e da sugestão proativa de parceiros está
em [`docs/historical/ict-phase-c.md`](docs/historical/ict-phase-c.md).

### 5.11 Confiança da fonte (`verificacao`)

Campo do edital indicando o nível de confiança da origem (item 2.2 — descoberta
de oportunidades, ver
[discovery-opportunities.md](docs/historical/discovery-opportunities.md)):

```yaml
verificacao_values: [verificado, provisorio, promovido]
```

- **`verificado`** — fonte confiável: FINEP, FAPESP e fontes graduadas a extractor
  próprio (§12.4). **Default** para tudo que vem do `SCRAPER_REGISTRY`.
- **`provisorio`** — descoberta da web aberta ainda em staging/bronze, antes da
  decisão humana. Não entra no catálogo nem no match enquanto não for promovida.
- **`promovido`** — descoberta aprovada pelo gate humano e encaminhada ao mesmo
  fluxo silver → gold das fontes fixas.

É metadado de proveniência, não entidade (§6.1). Fontes fixas usam
`verificado`; itens provisórios permanecem fora das superfícies de produto.

---

## 6. Entidades, relações e constraints ativas

### 6.1 Entidades do gold relacional

O catálogo representa `edital`, `programa`, `investidor`, `ict` e `agencia` em
`entities`. Relações estruturais vivem em `entity_relationships`; propriedades
de baixa cardinalidade como mecanismo, ano, TRL e público permanecem atributos
da entidade, não entidades autônomas. O vault Obsidian é apenas uma projeção
regenerável desse gold, sem autoridade sobre o catálogo ou o match.

`source` identifica a origem operacional do edital (`finep:589`); não deve ser
confundido com `fonte_recurso`, que identifica quem financia.

#### 6.1.2 Entidade `ict` (fora do ciclo de edital)

ICTs (Instituições de Ciência e Tecnologia) são **parceiras** que muitos editais
FINEP/FAPESP exigem para viabilizar a candidatura. **Uma ICT não lança edital** —
apenas participa de projetos. Logo, a entidade `ict` **não** flui pelo ETL de edital
(sem PDF, status, mechanism, vigência) e **não** entra no `SCRAPER_REGISTRY`.
O ingest lê os arquivos curados `data/bronze/ict_raw/embrapii_*.json`, materializa
`entities(kind=ict)` e a relação `credenciada_por` com a EMBRAPII. Afinidade entre
edital e ICT é calculada por setores e tecnologias compartilhados; não existe
relação direta edital↔ICT. Quando um edital exige parceria com uma ICT, essa
exigência é uma constraint do edital, não uma relação com uma ICT específica.

A definição legal de "ICT" é ampla; o campo `kind` absorve a variação
(unidade EMBRAPII, laboratório PNIPE, instituto, universidade) sem exigir
taxonomia perfeita.

```yaml
ict_schema:
  artifact: "data/bronze/ict_raw/embrapii_*.json"
  id_format: "<source>:<slug>"          # ex.: embrapii:inteligencia-artificial-ceia-ufg
  node_fields: [id, name, kind, source, url, about, address, contact, areas_raw, themes, themes_proposed, summary, brings_cofinancing]
  required_fields: [id, name, kind, source, themes]
  kinds: [embrapii_unit, laboratorio, instituto, universidade]
  sources: [embrapii, pnipe]
  notes:
    - "themes: temas CANÔNICOS de edital (mesma representação de edital.themes — rótulos, não slugs) que a ICT cobre. É a ponte: edital.themes ∩ ict.themes."
    - "O ingest normaliza áreas cruas para as taxonomias gold de setores e tecnologias (§13)."
    - "areas_raw: rótulos de expertise crus da fonte (EMBRAPII: action_lines + tech_skills). Display + matching fino futuro."
    - "themes_proposed: áreas que não casaram com nenhum tema de edital — candidatas à expansão do vocabulário (não entram em themes nem na ponte)."
    - "brings_cofinancing: bool. ICT cujo ARRANJO aporta recurso não-reembolsável ao projeto (Unidade EMBRAPII: ~1/3 do custo do projeto vem do aporte EMBRAPII). É o que dispara o selo 'pode trazer co-financiamento' no complemento do match. Default false; derivado true para source=='embrapii'. Generaliza além do kind (um lab PNIPE não traz). Opcional (não em required_fields) — ICTs antigas sem o campo seguem válidas."
    - "contact: dict {responsavel, email, telefone, site, ...} (campos opcionais)."
    - "O artefato bronze é a entrada curada; a representação consumida pelo produto vive em entities."
```

#### 6.1.3 Entidade `investidor` (fora do ciclo de edital)

Fundos/anjos/corporate venture (Q3). Como a `ict` (§6.1.2), é **entidade fora do
ciclo de edital**: sem PDF/status/mechanism/vigência, NÃO flui pelo ETL de evento
nem por regras temporais. A curadoria versionada vive em
`data/silver/investidores.json` e é materializada em `entities(kind=investidor)`.
O match usa a afinidade semântica entre o perfil da empresa e a entidade, além
dos atributos normalizados. Populado por **curadoria manual** (~30-50 fundos, decisão
spec_multi_quadrante §8 #3), não descoberta automática. Valores PLANOS (o wrapper
O artefato curado é materializado diretamente, sem schema paralelo de extração LLM.

```yaml
investidor_schema:
  artifact: "data/silver/investidores.json"
  id_format: "investidor:<slug>"        # ex.: investidor:kptl
  node_fields: [id, name, tese, tese_themes, setores, estagio_alvo, ticket_range, lead_follow, portfolio, co_investidores, site, contato, generalista, fund_status, tese_keywords, anti_tese, verificado_em, source_urls]
  required_fields: [id, name, tese_themes, setores, estagio_alvo]
  notes:
    - "tese_themes: temas CANÔNICOS (§5.9, mesma representação de edital.themes) — é a ponte investidor↔edital. INVARIANTE: ⊆ tema_vocab (senão a ponte nunca casa)."
    - "estagio_alvo ⊆ estagio_vocab (§5.9.1); setores ⊆ setor_vocab; ticket_range: {min_brl, max_brl} | null."
    - "generalista: true = fundo SEM tese setorial (investe em qualquer setor) → tese_themes DEVE ser [] e o match usa ESTÁGIO, não tema (senão casaria com tudo = ruído). Match-por-tese só vale p/ fundo com tese."
    - "fund_status: ativo|captando|dormante — frescor da ENTIDADE (análogo do status do edital; fundo dormante = ruído). verificado_em + source_urls: proveniência do enriquecimento."
    - "tese_keywords: texto livre FINO (análogo do areas_raw da ICT) p/ match além dos 7 temas grossos. anti_tese: o que o fundo NÃO faz (poder de REJEITAR no match)."
    - "co_investidores: semente da Camada B induzida (rede de fundos, BACKLOG) — inerte no MVP."
    - "O JSON versionado é a entrada curada; a representação consumida pelo produto vive em entities."
```

#### 6.1.4 Entidade `programa` (recorrente, fora do ciclo de edital)

Programas de fomento RECORRENTES (aceleração/incubação/subvenção/fundo que se
repetem por edição — ex.: Centelha, BNDES Garagem, InovAtiva). Como `investidor`
(§6.1.3), é **entidade fora do ciclo de edital**. A curadoria versionada vive em
`data/silver/programas.json`; o ingest materializa a entidade e a relação
`operado_por` com a agência responsável. Populado por **curadoria manual** (não descoberta). DISTINTO do
`opportunity_type=programa` (um evento datado): aqui é o programa-âncora ESTÁVEL.

```yaml
programa_schema:
  artifact: "data/silver/programas.json"
  id_format: "programa:<slug>"          # ex.: programa:centelha
  node_fields: [id, name, operador, tipo, descricao, formato, cadencia, beneficio, ticket_range, tese_themes, setores, estagio_alvo, elegibilidade, site, faq_url, source_urls, status, verificado_em]
  required_fields: [id, name, operador, tipo, estagio_alvo]
  notes:
    - "tipo: aceleracao|incubacao|subvencao|fundo|capacitacao — natureza do programa."
    - "formato: cohort|edital-periodico|fluxo-continuo. cadencia: texto livre (anual, 2x/ano, contínuo)."
    - "tese_themes ⊆ tema_vocab (§5.9; [] se multissetorial); setores ⊆ setor_vocab; estagio_alvo ⊆ estagio_vocab (§5.9.1). Mesma ponte do investidor (edital.themes ∩ programa.tese_themes)."
    - "status: ativo|dormante — frescor da entidade (dormante = ruído). verificado_em + source_urls: proveniência da curadoria."
    - "faq_url: P&R oficial do programa — alvo de retrieval valioso p/ escrita conversacional."
    - "O JSON versionado é a entrada curada; a representação consumida pelo produto vive em entities."
```

### 6.2 Relações estruturais

As relações ativas em `entity_relationships` são `operado_por` (edital ou
programa → agência), `subordinado_a` (edital → programa) e `credenciada_por`
(ICT → agência). O catálogo tolera `exige_parceria_com` em dados compatíveis,
mas o produtor atual representa a exigência genérica como constraint, pois não
há uma ICT específica como alvo. Wikilinks do vault são projeções de leitura geradas por
`scripts/export_to_obsidian.py`, não contratos do data plane.

### 6.3 Normalização de identificadores

```yaml
slugify_rules:
  unicode_normalize: NFD
  strip_combining_marks: true
  lowercase: true
  allowed_chars: "[\\w\\s-]"
  separator: "-"
  max_len: 80
  fallback: "sem-nome"
```

### 6.4 Constraints de elegibilidade dura (v3)

O KG é relacional (tabelas gold `entities`/`entity_relationships`/`match_chunks`,
migration 036); o hipergrado N-ário e seu schema de nós/arestas morreram com a
linhagem hyper-extract (v3 PR-C). Spec: `docs/specs/v3-unified.md`. O único
vocabulário deste bloco ainda vivo é o das **constraints de elegibilidade dura**
(coluna `entities.constraints`), lido pelo produtor `src/radar/core/kg/constraints_producer.py`
via `schema.constraint_tipos()` / `schema.constraint_ops()`. É o SUBCONJUNTO que o
produtor valida (`_valid`), distinto do vocab completo §13.4 (`constraint_vocab`).

```yaml
constraint_enums:
  # Elegibilidade DURA (D6/PR5) — coluna `entities.constraints` (jsonb): objetos
  # AVALIÁVEIS `{tipo, op, valor}`, distintos do texto residual `requisitos_texto`
  # (que só informa). Um constraint se AVALIA contra o perfil da empresa
  # (sat/unsat/unknown) no Stage 1 do match (`src/radar/core/services/eligibility.py`):
  # `unsat` elimina; `unknown` (campo faltando no perfil) NUNCA elimina — marca
  # "elegibilidade não verificada" no card. Semântica por tipo:
  #   porte         op in|not_in   valor=[mei,me,epp,media,grande]  (perfil: tamanho_empresa)
  #   sede_uf       op in|not_in   valor=[SC,SP,…] (UF)             (perfil: uf)
  #   faturamento   op lte|gte     valor=<BRL/ano>                  (perfil: faturamento_anual)
  #   trl           op lte|gte|in  valor=<1-9> ou [1-9]             (perfil: trl)
  #   forma_juridica op in|not_in  valor=[empresa,startup,ict,universidade,…] (perfil: tipo_entidade)
  #   parceria      op exige       valor=<ator_kind> (ex. ict)      (relacional — unknown no perfil)
  constraint_tipos: [porte, sede_uf, faturamento, trl, forma_juridica, parceria]
  constraint_ops: [in, not_in, lte, gte, exige]
```

---

## 7. Regras de vigência

### 7.1 Classificação

```yaml
vigencia_rules:
  vigente: "deadline parseável E deadline > hoje"
  historico: "caso contrário"
  deadline_format: "dd/mm/yyyy"
```

### 7.2 Normalização de status

Aplicada ao construir o índice, antes de classificar vigência:

```yaml
status_normalization:
  - "SE deadline parseável AND deadline > hoje  → status = ABERTA"
  - "SENÃO SE raw_status == 'ENCERRADA'         → status = ENCERRADA"
  - "SENÃO                                      → status = raw_status (preserva Desconhecido, RESULTADO_DIVULGADO, etc.)"
```

Motivação: status bruto do portal nem sempre é confiável; prazo futuro é evidência forte de abertura.

---

## 8. Histórico do workflow de síntese

O produtor de wiki pages (`etl_process.py` / `build_knowledge_graph`) foi
removido e não integra o data plane atual. O registro arquitetural completo
está em [`docs/historical/kg-entity-wiki-pages.md`](docs/historical/kg-entity-wiki-pages.md)
e [`docs/historical/hypergraph-migration.md`](docs/historical/hypergraph-migration.md).
O prompt e as regras de atualização dessa linhagem permanecem apenas nesses
documentos históricos; os produtores ativos estão descritos nos §§11–13.

---

## 9. Convenções

### 9.1 IDs de edital

```yaml
id_extraction:
  priority:
    - "chamada[.chamada_id]                    se presente"
    - "regex /chamadapublica/(\\d+) no link     fallback 1"
    - "último segmento do link                  fallback 2"
  type: str
```

### 9.2 Deduplicação bronze

Por `link`. Primeiro arquivo bronze lido (ordem alfabética) vence.

### 9.3 Formatos

- **Data**: `dd/mm/yyyy` (entrada) / `yyyy-mm-dd` (saída meta)
- **Moeda**: `int` em reais (BRL), sem casas decimais
- **TRL**: `int` em `[1, 9]`

---

## 10. Como adicionar uma nova fonte

1. Criar `wikis/<fonte>.md` espelhando a estrutura de `wikis/finep.md`.
2. Escrever scraper em `src/radar/pipeline/extractors/<fonte>.py` produzindo JSON bronze em `data/bronze/<fonte>_raw/`.
3. Escrever o `SourceAdapter` correspondente e registrá-lo em §12.4.
4. Cobrir os vocabulários/accessors afetados com testes direcionados.
5. Validar o caminho bronze → silver → `radar.core.kg.gold.ingest_all()`.

---

## 11. Documento estruturado (camada silver)

Artefato intermediário entre o bronze e dois consumidores: a ingestão gold
(`radar.core.kg.gold`) e o chunkeamento RAG (`chunker`). Uma única estruturação dos
documentos serve aos dois.

**Invariante — a camada é "burra":** ela só lineariza, limpa e rotula
estrutura. **Não** sintetiza `objective`/`mechanism` e **não** fatia (não decide
tamanho de chunk — isso pertence aos produtores especializados). Todo
campo opinativo aqui reacopla A e B e invalida os caches independentes (§11.4).
Code review deve barrar qualquer campo que não seja estrutura fiel.

A passada é **por página**: 1 chamada LLM por página do PDF, paralelizável. A
continuidade de seção entre páginas é preservada via `carry_section_path` (o
último `section_path` aberto na página anterior é passado como contexto).

### 11.1 Schema do bloco

O artefato é um JSONL: uma linha por bloco, ordem global preservada.

```yaml
structured_doc_schema:
  block_fields:
    idx:          { type: "int",       desc: "ordem global no documento, 0-based" }
    doc:          { type: "str",       desc: "PDF de origem (ex: Edital.pdf, Anexo_II.pdf)" }
    page:         { type: "int",       desc: "página de origem, 1-based" }
    section_path: { type: "list[str]", desc: "hierarquia da seção; [] se fora de seção" }
    kind:         { type: "enum",      desc: "tipo do elemento (ver kinds)" }
    text:         { type: "str",       desc: "texto limpo, verbatim, sem artefato de layout" }
  kinds: [heading, paragraph, table, list, signature, boilerplate]
  meta_sidecar:                       # data/silver/structured_docs/{source}/{id}.meta.json
    silver_version:           "str — versão do schema do bloco (bump = re-roda A e B)"
    structurer_prompt_version: "str — versão do prompt §11.3"
    structurer_model:         "str — modelo usado"
    source_hash:              "str — hash do texto dos PDFs de origem"
```

`kind=boilerplate|signature` permite ao Ramo A descartar ruído (rodapé,
assinaturas) sem heurística. `section_path` resolve o problema de chunks
"sem seção" de forma **fonte-agnóstica** — derivado pela LLM, não por regex
de `Art./§`.

### 11.2 Parâmetros do structurer

```yaml
structurer_params:
  silver_version: "1"
  prompt_version: "2"
  temperature: 0.0
  max_tokens: 4000
  per_page: true
  carry_section_path: true        # encadeia o último section_path entre páginas
  max_concurrent_docs: 8          # docs em paralelo; páginas serial intra-doc (carry)
```

### 11.3 Prompt do structurer (por página)

Fonte-agnóstico — fala de estrutura de documento, não de fomento. Fontes
**não** sobrescrevem (a estrutura é universal). Placeholders:
`{doc_name}`, `{page_num}`, `{page_text}`, `{carry_section_path}`.

```yaml
structurer_prompt: |
  Você segmenta uma página de um documento oficial em blocos estruturais.
  NÃO resuma, NÃO interprete, NÃO invente. Preserve o texto VERBATIM.

  Documento: {doc_name} — página {page_num}
  Seção aberta na página anterior (herde se a página começa no meio dela):
  {carry_section_path}

  ---
  TEXTO DA PÁGINA:
  {page_text}
  ---

  Produza APENAS um JSON array de blocos, na ordem em que aparecem:

  [
    {{
      "section_path": ["3. Critérios", "3.2 Pontuação"],
      "kind": "heading|paragraph|table|list|signature|boilerplate",
      "text": "texto exato do bloco, sem reescrever"
    }}
  ]

  Regras:
  - Todo bloco PERTENCE a uma seção. section_path = [] só se NÃO houver
    nenhum título antes dele no documento (raríssimo — capa pura sem texto).
  - Um bloco kind=heading ANCORA sua própria seção: o section_path dele
    INCLUI ele mesmo. Ex.: o heading "3. Critérios" tem
    section_path ["3. Critérios"]; o sub-heading "3.2 Pontuação" tem
    ["3. Critérios", "3.2 Pontuação"]. Os blocos seguintes herdam o
    section_path do último heading até aparecer o próximo.
  - Título de documento/anexo (ex.: "ANEXO 1", "Formulário para Inscrição")
    É um heading e vira a RAIZ do section_path de tudo naquele anexo. Rótulos
    de campo de formulário (ex.: "Título do Projeto", "Contatos") são heading
    e entram como nível seguinte sob a raiz do anexo.
  - Se a página começa no meio de uma seção sem repetir o título, herde de
    carry_section_path.
  - kind=boilerplate: cabeçalho/rodapé/numeração de página repetida.
    kind=signature: bloco de assinatura.
  - text: VERBATIM. Tabela → preserve como texto (markdown simples). Não
    concatene blocos de seções diferentes.
  - Não emita campo nenhum além de section_path, kind, text.
```

### 11.4 Versionamento & cache (desacoplamento)

Três chaves de invalidação independentes — é o que mantém A e B desacoplados:

| Camada | Chave de cache | Re-roda quando |
|---|---|---|
| Silver | `hash(pdf_text + prompt_version + model)` | PDF muda OU structurer versiona |
| Ramo A | `hash(silver_id + silver_version + wiki_prompt + metadata)` | schema da wiki (§4) muda |
| Ramo B | `hash(silver_id + silver_version + chunk_policy + embed_model)` | política de chunk muda |

Consequência: mexer no schema da wiki re-roda **só a Knowledge gold**
(transform barato, sem re-ler a fonte). Mexer na política de chunk re-roda
**só a Retrieval gold** (re-segmenta + re-embeda, **zero LLM**). Mexer no
structurer re-roda ambas (esperado — é a raiz compartilhada). Sem conteúdo ou
falha LLM: silver vazio → Knowledge cai no `_save_minimal_wiki_page`,
Retrieval não indexa (comportamento atual preservado).

> Nomenclatura: "Ramo A" = **Knowledge gold** (§12, L3a); "Ramo B" =
> **Retrieval gold** (§12, L3b). Ver §12 para o stack completo.

---

## 12. Arquitetura de camadas (vocabulário oficial)

Este é o modelo canônico. Substitui termos ad-hoc ("Ramo A/B"). O sistema é
**multi-fonte**: a estratégia de RAG e síntese é **agnóstica à fonte**; só a
extração e disponibilização do conteúdo é **individualizada por fonte**.

### 12.1 A fronteira

```
   INDIVIDUALIZADO          ┃  AGNÓSTICO À FONTE
   (muda por fonte)         ┃  (nunca sabe qual é a fonte)
                            ┃
   L0 raw → L1 SourceAdapter ┃→ L2 structured → L3a Knowledge gold
                            ┃                 → L3b Retrieval gold
              └─ fronteira: CONTRATO DocumentoCanônico ─┘
```

**Invariante:** nada à direita da fronteira abre arquivo de fonte específica
nem ramifica por fonte. PDF, HTML, API — tudo já chega como Documento
Canônico. Code review barra `pdfplumber`, paths de fonte ou `if source ==`
em qualquer camada ≥ L2.

### 12.2 Stack

| Camada | Nome oficial | Escopo | Conteúdo |
|---|---|---|---|
| L0 | `raw` (bronze) | **por fonte** | dump cru do scraper em `data/bronze/{source}_raw/` |
| L1 | **Source Adapter** | **por fonte** | converte raw → Documento Canônico. FINEP: lê PDFs. FAPESP: `texto_cru` |
| — | **Documento Canônico** | **fronteira** | contrato agnóstico (§12.3), persistido em `edital_source_docs` com fallback local |
| L2 | `structured` (silver) | agnóstico | blocos do structurer — detalhe em §11 |
| L3a | **Catálogo gold relacional** | agnóstico | `entities` + `entity_relationships` |
| L3b | **Índices de recuperação** | agnóstico | `match_chunks` para Radar; `edital_chunks` para WritingSession |

### 12.3 Contrato do Documento Canônico

A saída do Source Adapter e a única entrada do structurer (L2). Texto já
extraído por unidade lógica ("página") — o structurer **não** abre arquivo.

```yaml
canonical_doc:
  shape: "list[{ doc_name: str, units: list[str] }]"
  notes:
    - "doc_name: rótulo do documento (ex: Edital.pdf, pagina_chamada). Vira `doc` no bloco silver."
    - "units: texto por unidade lógica. PDF → 1 unit/página. HTML → 1 unit (ou split por âncora)."
    - "Vazio (sem conteúdo) → silver vazio → fallback preservado (§11.4)."
```

### 12.4 Registro de Source Adapters

Mapeamento fonte → adapter, schema-driven (como `bronze_mapping`). Adicionar
fonte = escrever o adapter + registrar aqui; nada em L2/L3 muda.

```yaml
source_adapters:
  finep:
    module: "radar.pipeline.adapters.finep"
    raw_dir: "finep_raw"
    strategy: "pdf"          # lê FINEP_PDFS_DIR/{id}/*.pdf, 1 unit/página
  fapesp:
    module: "radar.pipeline.adapters.fapesp"
    raw_dir: "fapesp_raw"
    strategy: "html_body"    # texto_cru do bronze como 1 doc
  fapesc:
    module: "radar.pipeline.adapters.fapesc"
    raw_dir: "fapesc_raw"
    strategy: "pdf"          # texto vem do PDF anexo (extraído no scraper) → texto_cru
  web:
    module: "radar.pipeline.adapters.web"
    raw_dir: "web_raw"
    strategy: "html_clean"   # HTML cru do bronze → html_to_text → split em units
```

> Fonte `web` (genérica): a seed list de URLs é curada na tabela operacional
> `web_sources` (migration 018), não numa listagem de portal. O bronze guarda
> HTML **cru**; o adapter re-limpa por run (`base.html_to_text`), o que permite
> trocar o extrator sem re-fetch. Cada URL = 1 edital `web:<url_hash>`.

### 12.5 Fronteira implementada

Os adapters resolvem PDF/HTML e entregam o Documento Canônico; `source_docs`
persiste esse conteúdo de forma durável, e o structurer e o chunker operam sobre
o mesmo contrato sem selecionar a fonte. `src/radar/core/tasks.py` descobre as fontes pelo
registry, encaminha o silver ao ingest gold e aquece `edital_chunks` às 05:00 UTC.
Os pontos de entrada de brief/escrita também garantem o chunking sob demanda,
como fallback de disponibilidade.

## 13. Vocabulários gold v3 (spec `docs/specs/v3-unified.md`)

Blocos lidos por `src/radar/core/kg/schema.py` (accessors `setores_taxonomia()`,
`tag_normalization()`, `match_sections()`, `constraint_vocab_v3()`) e aplicados
no ingest gold (`src/radar/core/kg/gold.py`). **Mudou a regra → edite estes blocos, não o
código.** Estes são os vocabulários do pipeline ativo. Blocos anteriores
permanecem apenas quando um accessor de compatibilidade ainda os consome.

### 13.1 Taxonomia de setores (`setores_taxonomia`)

Taxonomia FECHADA de 16 setores. 1-3 por entidade; fallback `Multissetorial`.
Nunca é hard filter no match — só facet de catálogo e boost opcional de ranking.
`labels` = valores canônicos. `tese_theme_map` mapeia `tese_themes` (estilo
`tema_vocab`, usados por investidores/programas) → labels. `alias_map` normaliza
qualquer string crua (setores curados, slugs `setor_vocab`, sinônimos comuns) →
label. Validação (1-3, vocabulário fechado, fallback) é em aplicação, não em CHECK SQL.

```yaml
setores_taxonomia:
  labels:
    - Agro
    - Saúde
    - Energia
    - TIC
    - Bioeconomia
    - Defesa
    - Mobilidade
    - Urbano
    - Educação
    - Química
    - Materiais
    - Sustentabilidade
    - Marítimo
    - Social
    - Finanças
    - Multissetorial
  fallback: Multissetorial
  max_por_entidade: 3
  # tese_themes (tema_vocab / macro_temas) → labels
  tese_theme_map:
    "agro - bioeconomia e alimentos": [Agro, Bioeconomia]
    "tecnologias digitais e conectividade": [TIC]
    "saúde e ciências da vida": [Saúde]
    "energia e transição sustentável": [Energia]
    "materiais, química e manufatura avançada": [Materiais, Química]
    "mobilidade e logística": [Mobilidade]
    "espaço - defesa e segurança": [Defesa]
    "defesa e soberania nacional": [Defesa]
    "meio ambiente, água e saneamento": [Sustentabilidade]
    "petróleo, gás e mineração": [Energia]
    "construção e cidades inteligentes": [Urbano]
  # string crua (lowercase+sem acento no lookup) → label
  alias_map:
    multissetorial: Multissetorial
    multissetoriais: Multissetorial
    transversal: Multissetorial
    agro: Agro
    agronegocio: Agro
    agropecuaria: Agro
    saude: Saúde
    "ciencias da vida": Saúde
    healthtech: Saúde
    energia: Energia
    "oleo-gas": Energia
    "oleo e gas": Energia
    "petroleo e gas": Energia
    tic: TIC
    ti: TIC
    "ti-software": TIC
    "ti e software": TIC
    software: TIC
    "tecnologia da informacao": TIC
    "tecnologias digitais": TIC
    bioeconomia: Bioeconomia
    biotecnologia: Bioeconomia
    defesa: Defesa
    seguranca: Defesa
    espacial: Defesa
    espaco: Defesa
    mobilidade: Mobilidade
    logistica: Mobilidade
    transporte: Mobilidade
    urbano: Urbano
    "cidades inteligentes": Urbano
    "smart cities": Urbano
    construcao: Urbano
    educacao: Educação
    edtech: Educação
    quimica: Química
    materiais: Materiais
    "manufatura avancada": Materiais
    industria: Materiais
    industrial: Materiais
    sustentabilidade: Sustentabilidade
    "meio-ambiente": Sustentabilidade
    "meio ambiente": Sustentabilidade
    ambiental: Sustentabilidade
    "transicao energetica": Sustentabilidade
    saneamento: Sustentabilidade
    maritimo: Marítimo
    oceano: Marítimo
    "economia azul": Marítimo
    naval: Marítimo
    social: Social
    "impacto social": Social
    financas: Finanças
    financeiro: Finanças
    fintech: Finanças
```

### 13.2 Normalização de tags (`tag_normalization`)

Passe DETERMINÍSTICO aplicado a TODA `tecnologias_tags` de TODA fonte
(`gold.normalize_tags`): lowercase, trim, singular simples, mapa de sinônimos.
Depois do mapa, `anti_class_verdict()` (`src/radar/core/kg/canonicalize.py`) filtra o que
sobrou de genérico/legal/métrica. Seed do mapa = curadoria do `concept_canon`
(vazio no disco local — mora no PG); o mapa abaixo é a semente inicial e cresce
com a curadoria. `synonyms`: variante (lowercase+trim) → forma canônica.

```yaml
tag_normalization:
  lowercase: true
  strip_accents: false     # folksonomia pt mantém acento (legível no card)
  singularize: true        # -s final (len>4), pulando irregulares -ais/-eis/-ois/-uis/-ns
  min_len: 2
  synonyms:
    ia: inteligência artificial
    ai: inteligência artificial
    "a.i.": inteligência artificial
    "inteligencia artificial": inteligência artificial
    ml: aprendizado de máquina
    "machine learning": aprendizado de máquina
    "aprendizado de maquina": aprendizado de máquina
    iot: internet das coisas
    "internet of things": internet das coisas
    "visao computacional": visão computacional
    "computer vision": visão computacional
    nlp: processamento de linguagem natural
    pln: processamento de linguagem natural
    "big data": big data
    "data science": ciência de dados
    "ciencia de dados": ciência de dados
    saas: saas
    "software as a service": saas
    b2b: b2b
    "deep tech": deep-tech
    deeptech: deep-tech
    biotech: biotecnologia
    agritech: agtech
    agtech: agtech
    fintech: fintech
    edtech: edtech
    "energias renovaveis": energia renovável
    "energia renovavel": energia renovável
    "digital twin": gêmeo digital
    "gemeos digitais": gêmeo digital
    "realidade aumentada": realidade aumentada
    "realidade virtual": realidade virtual
    blockchain: blockchain
```

### 13.3 Seções de match (`match_sections`, §5.3)

Classifica cada bloco silver `{section_path, kind}` em `thematic` (→ tagger e
`match_chunks`), `eligibility` (→ `constraints_producer.produce_from_text`) ou
`boilerplate` (descartado). `drop_kinds` derruba o bloco pelo `kind`. Os padrões
são regex (aplicados sobre o `section_path` deburrado/lowercase, qualquer nível).
Precedência: `eligibility` > `boilerplate` > (default) `thematic`.

```yaml
match_sections:
  drop_kinds: [signature, boilerplate]
  eligibility_patterns:
    - elegibilidade
    - eligibilidade
    - quem pode (participar|concorrer|se inscrever)
    - publico[- ]?alvo
    - participantes
    - particip(es|antes|acao)
    - condicoes de participacao
    - requisitos
    - beneficiarios
    - admissibilidade
    - habilita
  boilerplate_patterns:
    - cronograma
    - "^prazos?$"
    - documentacao
    - documentos exigidos
    - disposicoes (finais|gerais)
    - "^anexo"
    - contatos?
    - pontos de contato
    - publicacao dos resultados
    - divulgacao
    - recursos? administrativ
    - impugnac
    - penalidades
    - formulario
    - declaracao
    - certidao
    - vigencia
    - propriedade (intelectual|dos resultados|industrial)
    - responsabilidades
    - "^organizacao$"
    - execucao do projeto
    - "^financiamento$"
    - "^montante"
    - orcament
    - assinatura
```

### 13.4 Vocabulário de constraints v3 (`constraint_vocab`, §4.4)

Elegibilidade DURA do match v3 (o que editais BR de fato declaram: porte,
faturamento-teto, idade de CNPJ, sede, natureza jurídica, TRL, CNAE, parceria,
vínculo de incubação, investidor privado). Produzido por
`constraints_producer.produce_from_text` a partir das seções de elegibilidade do
silver; avaliado por `eligibility.py` (`unsat` elimina, `unknown` nunca elimina).
Superconjunto do `constraint_enums` (§6.4), o subconjunto de tipos/ops que o
produtor valida em `_valid` antes de gravar.

```yaml
constraint_vocab:
  tipos:
    - porte                 # in/not_in [mei, me, epp, media, grande]
    - faturamento           # lte/gte  R$/ano
    - idade_empresa_meses   # lte/gte  meses desde a abertura do CNPJ
    - sede_uf               # in/not_in [SC, SP, ...]
    - forma_juridica        # in/not_in [empresa, startup, ict, universidade, cooperativa, associacao]
    - trl                   # gte/lte/in  1-9
    - cnae                  # in/not_in  lista de CNAE (prefixo)
    - parceria              # exige  [agencia, fap, ict, corporate, aceleradora, investidor]
    - vinculo_incubacao     # exige  (incubadora/aceleradora credenciada)
    - investidor_privado    # exige  (aporte privado — ex. PIPE Invest)
  ops: [in, not_in, lte, gte, exige]
  # Enums fechados para os tipos categóricos — validados em `produce_from_text`
  # (achado do bake-off Fase 1.5: sem isso o produtor vazava valores fora do
  # vocabulário, ex. forma_juridica="sociedade limitada"/"fundacao", porte="250",
  # sede_uf="BR"/"Brasil"/"nacional" num edital NACIONAL — não é sigla de UF).
  valores:
    porte: [mei, me, epp, media, grande]
    forma_juridica: [empresa, startup, ict, universidade, cooperativa, associacao]
    sede_uf: [AC, AL, AP, AM, BA, CE, DF, ES, GO, MA, MT, MS, MG, PA, PB, PR,
              PE, PI, RJ, RN, RS, RO, RR, SC, SP, SE, TO]
  # Hierarquia de comparação (NÃO de emissão): valores do PERFIL da empresa que
  # satisfazem um valor EXIGIDO pelo constraint, além do próprio valor (ex.
  # constraint forma_juridica=[empresa] também é satisfeito por uma empresa
  # startup/ltda/sa/eireli/me/epp — achado do bake-off Fase 1.5: o produtor
  # tratava "startup" e "empresa" como categorias mutuamente exclusivas quando,
  # na prática, toda startup registrada já é uma empresa). Lido por
  # `eligibility.py` na comparação — não altera o que `produce_from_text` emite.
  satisfies:
    forma_juridica:
      empresa: [empresa, startup, ltda, sa, eireli, me, epp]
```
