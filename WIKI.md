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

Implicação de design: o pipeline preserva wiki pages de editais encerrados mesmo após deleção dos PDFs originais; o grafo inclui dimensão temporal (ver §6, nó `ano`) para permitir consultas longitudinais.

---

## 2. Artefatos

| Artefato | Path | Descrição |
|---|---|---|
| Índice vigente | `knowledge_graph/index.json` | Editais com prazo futuro (usado para matching) |
| Índice histórico | `knowledge_graph/index_historico.json` | Todos os editais já scraped |
| Wiki page | `knowledge_graph/wiki/{id}.json` | Uma por edital, gerada pela LLM |
| Cache de ingestão | `knowledge_graph/wiki/.etl_process_cache.json` | Hash MD5 por edital (evita reprocessar) |
| Vault Obsidian | (externo) | Espelho Markdown do grafo, exportado sob demanda |

---

## 3. Fontes

Cada fonte tem um schema específico em `wikis/<fonte>.md` que **estende** este doc global. Regras definidas aqui valem para todas; regras em `wikis/<fonte>.md` sobrescrevem quando aplicável.

| Fonte | Schema | Status |
|---|---|---|
| FINEP | [wikis/finep.md](wikis/finep.md) | ativo (v1) |
| BNDES | `wikis/bndes.md` | planejado |
| FAPESP | `wikis/fapesp.md` | planejado |
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

Programas específicos e fundos setoriais que não são fontes financiadoras em si, mas agrupam editais de uma linha temática ou regime regulatório próprio. Nó de grafo separado de fonte (§5.4) e de mechanism (§5.1).

```yaml
subprogramas_canonicos:
  ct-infra:   CT-Infra
  ctinfra:    CT-Infra
  ct-hidro:   CT-Hidro
  ct-agro:    CT-Agro
  funttel:    Funttel
  rota 2030:  Rota 2030
  mover:      MOVER
```

Extração na mesma string de `fonte_recurso` bruta, após §5.4. Case-insensitive, regex `\b{alias}\b`. Múltiplas matches na mesma string são permitidas.

### 5.7 Drop-list de modalidades

Termos que aparecem no campo `fonte_recurso` do portal mas **não são fontes** — são modalidades financeiras (já cobertas por `mechanism` em §5.1 e pelo nó `mechanism` em §6.1). Descartados silenciosamente pelo normalizador.

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

O campo `trl_range: {min, max}` (§4.2) é numérico (1-9). Para navegação no grafo, colapsa-se em 3 faixas semânticas. Um edital conecta a todas as faixas que sobrepõem seu range.

```yaml
trl_faixas:
  trl_pesquisa:   { min: 1, max: 3, label: "TRL 1-3 (Pesquisa)" }
  trl_prototipo:  { min: 4, max: 6, label: "TRL 4-6 (Protótipo)" }
  trl_industrial: { min: 7, max: 9, label: "TRL 7-9 (Industrial)" }
```

Regra de mapeamento: edital com `trl_range = {min: a, max: b}` conecta à faixa `f` se `max(a, f.min) <= min(b, f.max)`. Se `trl_range` é `{null, null}`, não há link.

---

## 6. Schema do grafo

### 6.1 Tipos de nó

Cada tipo de nó vira uma subpasta no vault Obsidian e um grupo no `graph.json`.

```yaml
node_types:
  edital:
    folder: editais
    tags: [finep, edital, "<status_tag>", "mecanismo/<mechanism>", "tema/<slug>", "ano/<pub_year>"]
    emoji: "<status_emoji>"
  tema:
    folder: temas
    tags: [finep, tema]
    emoji: "🏷️"
  fonte:
    folder: fontes
    tags: [finep, fonte-recurso]
    emoji: "💰"
  publico:
    folder: publicos
    tags: [finep, publico-alvo]
    emoji: "👥"
  ano:
    folder: anos
    tags: [finep, ano]
    emoji: "📅"
    unknown_label: desconhecido   # slug/label quando pub_year não pôde ser derivado
  mechanism:
    folder: mecanismos
    tags: [finep, mecanismo]
    emoji: "⚙️"
  subprograma:
    folder: subprogramas
    tags: [finep, subprograma]
    emoji: "🏛️"
  trl_faixa:
    folder: trl
    tags: [finep, trl]
    emoji: "📈"
  home:
    folder: ""
    tags: [finep, home]
    emoji: "📡"
```

Tags fonte-específicas (ex.: `finep`) vêm de `wikis/<fonte>.md`.

### 6.2 Tipos de link

Expressos como wikilinks Markdown. Formato: `[[{subfolder}/{node_folder}/{slug}|{label}]]`.

```yaml
link_types:
  edital_has_theme:
    from: edital
    to: tema
    section: "## Temas"
  edital_has_funding_source:
    from: edital
    to: fonte
    section: "## Fonte de Recurso"
  edital_has_target_audience:
    from: edital
    to: publico
    section: "## Público-Alvo"
  edital_published_in_year:
    from: edital
    to: ano
    section: "## Ano de Publicação"
  edital_has_mechanism:
    from: edital
    to: mechanism
    section: "## Mecanismo"
  edital_has_subprograma:
    from: edital
    to: subprograma
    section: "## Subprograma"
  edital_has_trl_faixa:
    from: edital
    to: trl_faixa
    section: "## Faixa TRL"
  aggregator_lists_edital:
    from: [tema, fonte, publico, ano, mechanism, subprograma, trl_faixa, home]
    to: edital
    section: "## Editais"
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
