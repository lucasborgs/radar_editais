# Radar de Editais — Wiki Schema

Este documento é o **schema autoritativo** do sistema. Ele define o que é uma wiki page, como o grafo é construído, que vocabulários são aceitos, e que workflows a LLM deve seguir ao ingerir fontes, responder perguntas e manter a wiki.

O código consome este doc em runtime via `core.wiki_schema`. Qualquer divergência entre doc e código é detectada pelo validador de consistência (`tests/test_wiki_schema_consistency.py`). **Se você mudar regras aqui, o código se adapta automaticamente.** Se você mudar código sem atualizar o doc, o teste quebra.

---

## 1. Propósito

O Radar de Editais é uma **plataforma de inteligência e automação de escrita para fomento à inovação**. Diferente de sistemas RAG tradicionais, opera como um **Ecossistema de Conhecimento Cumulativo** baseado no padrão Andrej Karpathy LLM Wiki.

Cada edital (vigente ou encerrado) vira uma **wiki page** (JSON estruturado). As wiki pages + metadados do índice compõem um **knowledge graph** navegável (JSON + vault Obsidian). Matching, escrita e análise operam sobre as wiki pages, não sobre texto bruto.

O sistema atende dois propósitos:

**Operação (foco no proponente).** Matching de alta precisão entre perfil de PMEs e editais **vigentes** (inicialmente FINEP), mais assistente de escrita baseado em contextos históricos e técnicos validados.

**Inteligência (foco longitudinal).** Memória de longo prazo sobre a evolução das agências de fomento. Os editais encerrados **não são descartados** — compõem o corpus histórico usado para analisar mudanças de critérios, heurísticas de aprovação "não escritas" (extraídas via feedback loops) e tendências tecnológicas ao longo dos anos.

> **Escopo do corpus histórico:** apenas editais que **algum dia foram do escopo PME/startup** entram no histórico. Editais nunca-elegíveis (pesquisa acadêmica pura, bolsas individuais) são filtrados em L1 antes de virar wiki page — bronze é gravado para audit, mas nem wiki page mínima é criada. Ver `wikis/_pme_filter.md`.

Implicação de design: o pipeline preserva wiki pages de editais encerrados mesmo após deleção dos PDFs originais; a dimensão temporal é mantida como tag `ano/<pub_year>` (§6.1.1) para permitir consultas longitudinais.

> **Arquitetura de camadas:** o vocabulário oficial das camadas, a fronteira
> agnóstico/individualizado e o contrato de Documento Canônico estão em **§12**.
> Termos ad-hoc anteriores ("Ramo A/B") estão depreciados — ver §12.

---

## 2. Artefatos

| Artefato | Path | Descrição |
|---|---|---|
| Índice vigente | `knowledge_graph/index.json` | Editais com prazo futuro (usado para matching) |
| Índice histórico | `knowledge_graph/index_historico.json` | Todos os editais já scraped |
| Wiki page | `knowledge_graph/wiki/{id}.json` | Uma por edital, gerada pela LLM |
| Cache de ingestão | `knowledge_graph/wiki/.etl_process_cache.json` | Hash MD5 por edital (evita reprocessar) |
| Doc estruturado (silver) | `silver_data/structured_docs/{source}/{id}.jsonl` | Linearização limpa + rotulada dos PDFs (1 passada LLM/página). Insumo compartilhado da wiki (Ramo A) e do chunkeamento RAG (Ramo B). Ver §11 |
| Vault Obsidian | `obsidian_vault/` | Espelho Markdown do grafo, unificado no projeto, exportado sob demanda |

---

## 3. Fontes

Cada fonte tem um schema específico em `wikis/<fonte>.md` que **estende** este doc global. Regras definidas aqui valem para todas; regras em `wikis/<fonte>.md` sobrescrevem quando aplicável.

| Fonte | Schema | Status |
|---|---|---|
| FINEP | [wikis/finep.md](wikis/finep.md) | ativo (v1) |
| FAPESP | [wikis/fapesp.md](wikis/fapesp.md) | ativo (v1) |
| FAPERJ | `wikis/faperj.md` | planejado (Fase 2 — portal em migração) |
| EMBRAPII | `wikis/embrapii.md` | planejado |

---

## 4. Schema da wiki page

Uma wiki page é um JSON com três grupos de campos:

### 4.1 Campos herdados do índice

Copiados literalmente de `index.json` / `index_historico.json`. Não são inferidos pela LLM.

```yaml
wiki_page_inherited_fields:
  - id               # string, ID único da chamada
  - title            # string
  - status           # enum (ver §5.3)
  - deadline         # string "dd/mm/yyyy" ou ""
  - pub_date         # string "dd/mm/yyyy" ou ""
  - pub_year         # int (yyyy) ou "desconhecido" se pub_date vazio/inválido — dimensão temporal do grafo (§6)
  - link             # URL do portal
  - themes           # list[str], temas canonicalizados
  - publico_alvo     # list[str]
  - fonte_recurso    # list[str], siglas canônicas (ver §5.4)
```

### 4.2 Campos gerados pela LLM

Extraídos dos PDFs e metadados via prompt (§8.1). Devem aparecer em toda wiki page, mesmo que `null`.

```yaml
wiki_page_synthesized_fields:
  objective:               { type: "str",       nullable: true,  desc: "síntese 2-3 frases" }
  mechanism:               { type: "enum",      nullable: true,  values_ref: "§5.1" }
  eligible_entities:       { type: "list[str]", default: [],     desc: "tipos de proponentes aceitos" }
  value_range:             { type: "obj",       shape: "{min_brl: int|null, max_brl: int|null}" }
  trl_range:               { type: "obj",       shape: "{min: int|null, max: int|null}", range: "[1, 9]" }
  required_certifications: { type: "list[str]", default: [] }
  counterpart_required:    { type: "bool",      default: false }
  key_requirements:        { type: "list[str]", max_items: 5, desc: "requisitos concretos e verificáveis" }
  key_facts:               { type: "list[str]", max_items: 5, desc: "fatos mais relevantes para decisão" }
  proposal_sections:       { type: "list[str]", min_items: 6, max_items: 12, desc: "seções obrigatórias da proposta" }
```

### 4.3 Campos meta

```yaml
wiki_page_meta_fields:
  generated_at: { type: "str",  format: "yyyy-mm-dd" }
  source:       { type: "enum", values: ["etl_process", "metadata_only"], legacy_values: ["facts+metadata"] }
```

`legacy_values`: valores de `source` produzidos por versões anteriores do pipeline. Wiki pages com esses valores podem não ter todos os campos do schema atual. Serão sobrescritas quando o edital correspondente for reprocessado (cache miss por hash).

`source = "metadata_only"` indica wiki page mínima (sem PDFs disponíveis → sem chamada LLM → campos synthesized todos default).

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

Fragmentos que não casam com nenhuma canônica, nem com §5.6, nem com §5.7 são descartados silenciosamente. Contexto regulatório não-financiador (ex.: `"conforme Resolução nº 918/2023 da ANP"`) fica preservado em `key_facts` / `key_requirements` da wiki page, não no grafo.

### 5.5 Públicos canônicos

Lista fechada de tipos de proponente aceitos. Modificadores em parênteses (ex.: `"(Fundações)"`, `"(Pública ou Privada)"`) são descartados — a especificidade fica em `key_requirements` da wiki page.

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

Termos que aparecem no campo `fonte_recurso` do portal mas **não são fontes** — são modalidades financeiras (já cobertas pela tag `mechanism`, §5.1 / §6.1.1). Descartados silenciosamente pelo normalizador.

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

O campo `trl_range: {min, max}` (§4.2) é numérico (1-9). Colapsa-se em 3 faixas semânticas, usadas como **tag** do edital (`trl/<faixa>`) e dimensão de matching/filtro — não como nó de grafo (§6.1.1). Um edital recebe a tag de todas as faixas que sobrepõem seu range.

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
  `domain/vocabulary.canonicalize_themes` (stub) deve, ao evoluir, mapear
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

### 5.10 Exigência de parceria com ICT (`requires_ict_partner`)

Campo booleano **derivado** na entry do edital (`index.json`), indicando se o
edital exige parceria com ICT (§6.1.2) — o gatilho do matchmaking de parceiros
(Fase C, `core/ict_match`). **Heurística por regex** (MVP): aplica os patterns
abaixo sobre o texto do edital (título + descrição + texto integral dos PDFs/
`texto_cru`). Mira **linguagem de obrigatoriedade/arranjo**, não mera menção —
"ICT" aparece em boilerplate de quase todo edital FINEP; o que marca é exigir
ICT como executora/coexecutora ou no arranjo.

**Semântica (não tratar como ground-truth):** o flag é um **hint de
proatividade, não um gate**. A tool `find_ict_partners` funciona para qualquer
edital — o flag só sinaliza ao agente "provavelmente vale sugerir parceiros
proativamente". Ele nunca bloqueia nada. Por isso os erros são de **baixo custo**:
falso-negativo → o agente não sugere proativamente, mas a tool segue disponível;
falso-positivo → um empurrão proativo errado, que é *sugestão*, não compromisso
(ver guard-rail em [docs/spec_ict_phase_c.md](docs/spec_ict_phase_c.md)).

Vieses/limites assumidos: (1) **FINEP-enviesado** — depende de "ICT/coexecutora/
arranjo"; para FAPESP é efetivamente sempre `false`. (2) Heurística, sem
ground-truth — o pattern [1] faz quase todo o trabalho; "falsos-positivos" como a
FAQ "a ICT pode ser coexecutora? Não" não são confirmáveis sem rótulo (o edital
pode exigir ICT como *executora*). (3) Exigência só em anexo não coletado →
falso-negativo estrutural. **Decisão consciente:** não tunar os patterns às cegas
agora; revisitar só quando o flag virar load-bearing (UI filtrar/ordenar por ele,
ou seleção de parceiro virar fluxo primário) — aí rotular amostra + medir
precisão/recall, e considerar classificador LLM. Ver `docs/BACKLOG.md`. Patterns
vivem aqui (regra), não no `.py` — tune sem deploy.

```yaml
ict_requirement_patterns:
  - 'obrigat[óo]ri[ao][^.]{0,60}(ICT|institui[çc][ãa]o de ci[êe]ncia)'
  - '(dever[áa]|exig[ei]\w*|necess[áa]ri[ao])[^.]{0,80}(parceria|coopera[çc][ãa]o|coexecu\w*)[^.]{0,40}(ICT|institui[çc][ãa]o de ci[êe]ncia)'
  - 'em (parceria|coopera[çc][ãa]o) com[^.]{0,30}(uma? )?ICT'
  - '\bICT\b[^.]{0,30}coexecutora?'
  - 'participa[çc][ãa]o (obrigat[óo]ria|m[íi]nima) de[^.]{0,30}(uma? )?ICT'
```

`requires_ict_partner` é **propriedade do edital**, não nó (§6.1.1). Presente em
toda entry do índice (default `false`).

### 5.11 Confiança da fonte (`verificacao`)

Campo do edital indicando o nível de confiança da origem (item 2.2 — descoberta
de oportunidades, ver [docs/spec_descoberta_oportunidades.md](docs/spec_descoberta_oportunidades.md)):

```yaml
verificacao_values: [verificado, provisorio]
```

- **`verificado`** — fonte confiável: FINEP, FAPESP e fontes graduadas a extractor
  próprio (§12.4). **Default** para tudo que vem do `SCRAPER_REGISTRY`.
- **`provisorio`** — descoberta da web aberta, extraída por LLM e ainda não
  verificada por humano. Entra no KG (matchável/writable) mas **rotulada**; a
  verificação humana sobe para `verificado` ou remove (não-bloqueante).

Propriedade do edital, não nó (§6.1.1). Presente em toda entry do índice (default
`verificado`). Match e escrita devem distinguir provisório de verificado (rótulo/
bucket) — ver spec 2.2.

---

## 6. Schema do grafo

### 6.1 Tipos de nó

Cada tipo de nó vira uma subpasta no vault Obsidian e um grupo no `graph.json`.

**Critério nó vs tag:** um tipo é **nó** só se for hub de navegação (pivota-se por ele
para *descobrir* editais) **e** tiver identidade própria. Enums de baixa cardinalidade
(2-3 valores) que apenas *filtram* — `mechanism`, `ano`, `trl_faixa` — são **tags** no
frontmatter do edital (§6.1.1), não nós, para não poluir o grafo com mega-hubs sem
semântica. Política vale para todas as fontes.

```yaml
node_types:
  edital:
    folder: editais
    tags: [finep, edital, "<status_tag>", "mecanismo/<mechanism>", "tema/<slug>", "subprograma/<slug>", "trl/<faixa>", "ano/<pub_year>"]
    emoji: "<status_emoji>"
  tema:
    folder: temas
    tags: [finep, tema]
    emoji: "🏷️"
  publico:
    folder: publicos
    tags: [finep, publico-alvo]
    emoji: "👥"
  subprograma:
    folder: subprogramas
    tags: [finep, subprograma]
    emoji: "🏛️"
  fonte:
    folder: fontes
    tags: [fonte]
    emoji: "💰"
  ict:
    folder: icts
    tags: [ict, "kind/<kind>", "tema/<slug>"]
    emoji: "🔬"
  home:
    folder: ""
    tags: [finep, home]
    emoji: "📡"
```

`fonte` = **agência/instituição de origem** do edital (FINEP, FAPESP, …),
derivada do `source` do id prefixado (`finep:589` → FINEP). Todo edital pertence
a exatamente uma → é o eixo de agrupamento de um grafo multi-fonte e garante que
nenhum edital fique órfão. NÃO confundir com `fonte_recurso` (quem paga: FNDCT,
Petrobras, BNDES…), que é outro eixo, frequentemente vazio, e não vira nó.

Tags fonte-específicas (ex.: `finep`) vêm de `wikis/<fonte>.md`.

#### 6.1.1 Dimensões rebaixadas a tag

`mechanism` (§5.1), `ano` (derivado de `pub_date`) e `trl_faixa` (§5.8) **não são
nós**. São propriedades do edital, expressas como tag no frontmatter
(`mecanismo/<key>`, `ano/<pub_year>`, `trl/<faixa>`) e consumidas pelo matching/filtro —
nunca como wikilink/aresta. `ano` sem `pub_date` parseável → tag `ano/desconhecido`.

#### 6.1.2 Nó `ict` (fora do ciclo de edital)

ICTs (Instituições de Ciência e Tecnologia) são **parceiras** que muitos editais
FINEP/FAPESP exigem para viabilizar a candidatura. **Uma ICT não lança edital** —
apenas participa de projetos. Logo, o nó `ict` **não** flui pelo ETL de edital
(sem PDF, status, mechanism, vigência) e **não** entra no `SCRAPER_REGISTRY`.
Tem pipeline de ingestão e artefato próprios (`icts.json`), e se liga ao grafo de
editais **pela ponte do nó `tema`**: `edital --edital_has_theme--> tema
<--ict_has_expertise-- ict`. **Não há aresta direta edital↔ICT** — o casamento
"este edital pede parceiro; estas ICTs têm a expertise" é computado por interseção
de `tema` (slugs compartilhados, §6.3). Editais exigem *uma* ICT, não uma nomeada;
por isso a obrigatoriedade vira tag `requires_ict_partner` no edital (derivada na
extração), nunca aresta.

A definição legal de "ICT" é ampla; o campo `kind` absorve a variação
(unidade EMBRAPII, laboratório PNIPE, instituto, universidade) sem exigir
taxonomia perfeita.

```yaml
ict_schema:
  artifact: "knowledge_graph/icts.json"
  id_format: "<source>:<slug>"          # ex.: embrapii:inteligencia-artificial-ceia-ufg
  node_fields: [id, name, kind, source, url, about, address, contact, areas_raw, themes, themes_proposed, summary]
  required_fields: [id, name, kind, source, themes]
  kinds: [embrapii_unit, laboratorio, instituto, universidade]
  sources: [embrapii, pnipe]
  notes:
    - "themes: temas CANÔNICOS de edital (mesma representação de edital.themes — rótulos, não slugs) que a ICT cobre. É a ponte: edital.themes ∩ ict.themes."
    - "O alvo do mapeamento é o vocabulário de temas EMERGENTE do index.json (não há vocab fixo; canonicalize_themes é stub). Mapeamento fino→macro é LLM."
    - "areas_raw: rótulos de expertise crus da fonte (EMBRAPII: action_lines + tech_skills). Display + matching fino futuro."
    - "themes_proposed: áreas que não casaram com nenhum tema de edital — candidatas à expansão do vocabulário (não entram em themes nem na ponte)."
    - "contact: dict {responsavel, email, telefone, site, ...} (campos opcionais)."
    - "icts.json espelha index.json: {icts: [...], total_icts, themes_index, themes_proposed_index, last_updated}."
```

### 6.2 Tipos de link

Expressos como wikilinks Markdown. Formato: `[[{subfolder}/{node_folder}/{slug}|{label}]]`.

```yaml
link_types:
  edital_has_theme:
    from: edital
    to: tema
    section: "## Temas"
  edital_has_target_audience:
    from: edital
    to: publico
    section: "## Público-Alvo"
  edital_has_subprograma:
    from: edital
    to: subprograma
    section: "## Subprograma"
  edital_has_fonte:
    from: edital
    to: fonte
    section: "## Fonte"
  aggregator_lists_edital:
    from: [tema, publico, subprograma, fonte, home]
    to: edital
    section: "## Editais"
  ict_has_expertise:
    from: ict
    to: tema
    section: "## Áreas de Atuação"
  aggregator_lists_ict:
    from: [tema]
    to: ict
    section: "## ICTs"
```

### 6.3 Slugify

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

## 8. Workflows

### 8.1 Ingestão (prompt de extração)

Prompt base usado por `pipeline/etl_process.py`. Fontes podem sobrescrever em `wikis/<fonte>.md`.

```yaml
extraction_prompt: |
  Você é um especialista em editais de fomento à inovação no Brasil.

  Leia os documentos abaixo de uma chamada pública e produza a wiki page estruturada em JSON.

  ---
  METADADOS (do portal web):
  {metadata}

  ---
  DOCUMENTOS (PDFs da chamada):
  {documents}

  ---
  Produza APENAS o JSON abaixo, sem markdown ou texto extra:

  {{
    "objective": "síntese em 2-3 frases do que este edital financia e para quem",
    "mechanism": "subvencao|reembolsavel|investimento|misto|null",
    "eligible_entities": ["empresas", "startups", "ICTs"],
    "value_range": {{"min_brl": null, "max_brl": null}},
    "trl_range": {{"min": null, "max": null}},
    "required_certifications": [],
    "counterpart_required": false,
    "key_requirements": ["máx 5 requisitos concretos e objetivos"],
    "key_facts": ["5 fatos mais relevantes para uma empresa decidir se candidatar"],
    "proposal_sections": [
      "1. Título e identificação do projeto",
      "2. ..."
    ]
  }}

  Regras:
  - mechanism: classifique pelo mecanismo financeiro principal
  - trl_range: inteiros 1-9, null se não mencionado
  - value_range: valores em reais como inteiros, null se não mencionado
  - key_requirements: máx 5 itens, cada um autocontido e verificável
  - key_facts: os 5 fatos que uma empresa usaria para decidir se vale candidatar
  - proposal_sections: seções obrigatórias da proposta na ordem exigida pelo edital,
    extraídas das instruções de inscrição e formulários. Se o edital não especificar
    estrutura, derive do objeto e dos critérios de avaliação. Entre 6 e 12 seções.
```

Parâmetros da chamada LLM:

```yaml
llm_params:
  temperature: 0.1
  max_tokens: 1500
  model_char_budgets:
    gemini-2.5-flash: 3600000   # 900k tokens × 4 chars
    gpt-4o-mini:      320000    # 80k tokens × 4 chars
```

### 8.2 Atualização

- **Cache** por hash MD5 de `(metadata_do_index + conteúdo_concatenado_dos_PDFs)`. Reprocessa quando hash muda (retificação, aditivo).
- **Modo histórico**: processa apenas editais sem wiki page existente.
- **Flag `--skip-cache`**: força reprocessamento.

### 8.3 Feedback loop

Ver [docs/wiki_feedback_loop.md](docs/wiki_feedback_loop.md). Lições aprendidas de propostas finalizadas entram em `wiki_page.lessons_learned` com `confidence`.

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
2. Escrever scraper em `pipeline/extractors/<fonte>.py` produzindo JSON bronze em `bronze_data/<fonte>_raw/`.
3. Se o scraper retorna campos que mapeiam 1:1 para o schema comum, não precisa tocar em `build_knowledge_graph.py`. Caso contrário, adicionar adaptador na seção `bronze_mapping` do doc da fonte.
4. Rodar validador: `pytest tests/test_wiki_schema_consistency.py`.
5. Rodar pipeline: campos do schema comum são produzidos automaticamente; campos fonte-específicos vêm do doc da fonte.

---

## 11. Documento estruturado (camada silver)

Artefato intermediário entre o bronze (PDFs crus) e os dois consumidores: a
síntese da wiki page (**Ramo A**, `etl_process`) e o chunkeamento RAG
(**Ramo B**, `chunker`). Uma única passada LLM sobre os PDFs serve aos dois.

**Invariante — a camada é "burra":** ela só lineariza, limpa e rotula
estrutura. **Não** sintetiza (não infere `objective`/`mechanism` — isso é §4.2)
e **não** fatia (não decide tamanho de chunk — isso é política do Ramo B). Todo
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
  meta_sidecar:                       # silver_data/structured_docs/{source}/{id}.meta.json
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
| L0 | `raw` (bronze) | **por fonte** | dump cru do scraper em `bronze_data/{source}_raw/` |
| L1 | **Source Adapter** | **por fonte** | converte raw → Documento Canônico. FINEP: lê PDFs. FAPESP: `texto_cru` |
| — | **Documento Canônico** | **fronteira** | contrato agnóstico (§12.3) |
| L2 | `structured` (silver) | agnóstico | blocos do structurer — detalhe em §11 |
| L3a | **Knowledge gold** | agnóstico | wiki page (§4) + grafo (§6) |
| L3b | **Retrieval gold** | agnóstico | `edital_chunks` (chunk + embedding) |

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
    module: "pipeline.adapters.finep"
    raw_dir: "finep_raw"
    strategy: "pdf"          # lê FINEP_PDFS_DIR/{id}/*.pdf, 1 unit/página
  fapesp:
    module: "pipeline.adapters.fapesp"
    raw_dir: "fapesp_raw"
    strategy: "html_body"    # texto_cru do bronze como 1 doc
```

### 12.5 Onde o código viola isto hoje

Débito conhecido, a ser pago no rename (Fase 2). O structurer faz `pdfplumber`
— extração é trabalho de L1, não de L2. `_SOURCE = "finep"` e
`FINEP_PDFS_DIR` em etl_process/tasks fixam a fonte. O alvo: extração migra
pro Source Adapter; L2/L3 passam a consumir Documento Canônico, source-neutro.
