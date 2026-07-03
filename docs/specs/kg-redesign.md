# Spec — KG v2: Oportunidade/Ator/Conceito + funil de match

Status: **aprovada** · 2026-07-03 · escopo: consolidar os 12 tipos de nó em 3 entidades + propriedades + constraints; canonicalizar descritores; funil de match com filtro duro de elegibilidade, agregação MaxSim e veredito LLM no top-K; proveniência/URL ponta a ponta; ficha por oportunidade no frontend

> Origem: exploração de redesign de 2026-07-03 (segunda opinião independente + levantamento empírico do corpus + pesquisa de literatura). Supersede a parte de match de `match-evolution.md` (já superada pelos sprints do hipergrafo) e revisa premissas da `hypergraph-architecture.md` (o schema 12 nós/10 arestas e a premissa "elegibilidade é raciocínio do agente, sem estágio separado").
>
> Contexto de teste: o sistema é pré-beta, sem usuários reais. Gates comparativos exaustivos NÃO são exigidos — a suíte `matching` roda como sanity check, não como gate estatístico. Em troca, cada decisão está documentada com o porquê, para ser re-avaliável depois.

---

## Motivação

O schema atual (12 tipos de nó, 10 de aresta) nasceu com viés de origem: tudo tem o formato de edital FINEP. O levantamento empírico de 2026-07-03 (35 arquivos, 4.379 nós, 4.552 arestas) quantificou os problemas:

| Sintoma | Medida |
|---|---|
| Catch-all vazando | `Entidade` = 1.005 nós (23% do grafo, 2º maior tipo) |
| Vocabulário não compartilhado | 91% dos names de `Tema`, 97% de `Tecnologia`, 98% de `Aplicação` aparecem em **1 arquivo só** (fan-in ≈ 1 — não são nós de verdade, são keyphrases por documento) |
| Ruído de extração | ~30% em Tema, ~20% em Tecnologia, ~27% em Aplicação ("Brasil", "LDO 2026", "PROPOSTA", "Gelirrecis ND *") |
| Conexão cross-fonte pelo lixo | dos 15 names mais repetidos entre arquivos, 11 são geografia/boilerplate ("norte" em 12 arquivos, "LDO" em 5, TRL como **duas** strings distintas) |
| Faceta fingindo ser nó | `Mecanismo` = 15 nós no corpus inteiro (14 "Subvenção" + 1 "Bolsa") — constante, zero poder discriminativo |
| Arestas mortas | 6 dos 10 tipos (`financia`, `exige`, `destina_a`, `exclui`, `pertence_a`, `resolve`) nunca são lidos por nenhum consumidor |
| Identidade frouxa | arestas ligam nós por **name-string lowercased**, sem IDs — renomear um nó quebra membership silenciosamente |
| Metadado perdido | nenhum hipergrafo carrega URL oficial, embora **todo** bronze a tenha (finep `link`, fapesp/fapesc `url`, discovery `link`, ict_raw `url`, curados `site`/`source_urls`) |
| Curadoria degradada | `data/knowledge_graph/investidores.json` e `programas.json` (curados, única fonte dessas frentes) já têm facetas estruturadas, elegibilidade, vocabulário controlado e URLs — e o ingest os **achata** para o nó formato-edital, jogando tudo fora |

O princípio de produto que o redesign entrega: **formato novo de oportunidade = dado novo, não schema novo** (valor novo no enum `kind` + propriedades opcionais; zero tipos de nó/aresta novos, zero migração, zero extrator novo).

## Fundamento conceitual (o porquê do desenho)

A partição correta do domínio não é "que tipos de nó existem", é **semântica de comparação** — como cada natureza de informação se compara entre demanda e oferta. Isso sobrevive a qualquer troca de motor de match:

| Natureza | Exemplos | Como se compara | Representação |
|---|---|---|---|
| Identidade referenciável | agência, ICT, fundo, edital, desafio | referência/dedup, nunca similaridade | **nó** (`Oportunidade`, `Ator`) |
| Dimensão graduada (vocab. aberto, estrutura métrica) | temas, tecnologias, aplicações | similaridade graduada (cosseno/LLM) | **nó `Conceito`** — se e só se canonicalizado |
| Dimensão categórica/ordinal (vocab. fechado) | mecanismo, TRL, porte, estágio | predicado exato/ordem — subvenção NÃO é "0.7 similar" a equity | **propriedade/enum** |
| Constraint avaliável | "faturamento ≤ 16M", "exige parceria com ICT" | avaliação (sat/unsat/unknown) contra o perfil | **objeto estruturado tipado** |

Justificativas registradas:

- **Categórico fora do cosseno é fato do domínio, não limitação do motor** — evidência interna: o comentário em `hypergraph_match.py:52-56` registra que uma agtech casava com 100% dos editais via subvenção↔subvenção antes de `Mecanismo` sair da afinidade. Um LLM juiz também quer mecanismo como campo, não como texto a similarizar.
- **Descritores só são nós com canonicalização.** O critério da literatura para promover algo a nó (Wikidata "structural need") é fan-in — ser referenciado por múltiplas entidades. Hoje o fan-in é ≈1. A canonicalização é a decisão load-bearing: com ela, `Conceito` vira o espaço compartilhado que conecta demanda e oferta (e as travessias do explore passam a funcionar cross-fonte de verdade); sem ela, descritor deveria ser array embedado, não nó. Escolhemos canonicalizar porque o produto depende de travessia (explore, expansão via catálogo, ICT no radar).
- **Embedar conceitos separadamente continua certo** — é multi-vector/late-interaction retrieval (família ColBERT), superior a vetor único para documentos multi-tópicos. O que muda é a **agregação** (ver funil).
- **Precedentes externos**: OpenAIRE modela tipo de funding como campo estruturado (não classe por formato); schema.org modela `employmentType` como texto/enum (não nó); DINGO modela elegibilidade como `Criterion` estruturado com subtipos — exatamente a direção do produtor de `eligibility_constraints` já iniciado.

## Decisões pinadas (não revisitar sem novo dado)

| # | Decisão | Por quê |
|---|---|---|
| D1 | Unidade de resultado do radar = **sempre `Oportunidade`**. `Ator` nunca é card direto; aparece via suas ofertas | Uniformidade do que o match devolve é decisão de produto que simplifica todo o pipeline (cards, persistência `entry_kind='radar'`, ficha) |
| D2 | `Investidor` se desdobra: o fundo é `Ator(kind=investidor)`; "captar com o fundo X" é `Oportunidade(kind=investimento, aperture=continua)` | O tipo atual funde ator com oferta. O desdobramento permite tese/estágio/ticket como propriedades da oferta sem inventar formato novo |
| D3 | `Programa` vira `Oportunidade(kind=programa, aperture=recorrente)`; chamadas apontam para ele via `pertence_a` | Programa é recomendável entre chamadas ("PIPE abre 3x/ano") — logo é unidade de resultado, logo é Oportunidade (D1) |
| D4 | `Fonte` deixa de ser nó; vira bloco de **proveniência** por oportunidade (`fonte`, `url`, `coletado_em`) | Proveniência é metadado do pipeline, não ontologia. Os 84 nós Fonte atuais são vazamento |
| D5 | `Entidade` (catch-all) morre; reclassificação na migração (maioria → `Ator` ou `Conceito`) | 23% do grafo num tipo "Entity" é o sistema de tipos admitindo que não fecha |
| D6 | `Requisito`/`Exclusão` como nós morrem; viram (a) constraints estruturadas tipadas, (b) arestas relacionais (`exige_parceria` → Ator-kind), (c) resíduo texto (`requisitos_texto`) | Constraint se **avalia**, não se similariza. Amostras mostram que os nós atuais são majoritariamente citação de lei (ruído). Padrão DINGO; converge com a branch `feat/elig-constraints-producer` |
| D7 | `Tema`/`Tecnologia`/`Aplicação` fundem em `Conceito(dim=tema\|tecnologia\|aplicacao)` | O trio é scaffold de prompt, não ontologia: o match já os trata uniformemente e o extrator os confunde (TRL classificado como Tema E como Tecnologia; regiões como Tema). Um tipo só simplifica canonicalização e admite dimensão nova sem schema novo. A `dim` continua guiando o prompt de extração |
| D8 | Vocabulário **two-tier**: macro-temas controlados (~10-15, semeados pelo `themes_index` dos curados) como **propriedade** `macro_temas[]` da Oportunidade; `Conceito` aberto canonicalizado como nó para o match fino | Une os dois mundos que já existem no dado (curados usam vocabulário controlado; editais usam aberto). Macro-tema dá base estável para filtro/UI; conceito aberto preserva granularidade do match. Decisão do usuário 2026-07-03 |
| D9 | Match core continua **sem LLM no ranking**; LLM entra só como **veredito estruturado no top-K** (K≈10–20), produzindo razões, não score | Custo escala com K, não com corpus. Fiel a "AI drafts, humans decide": o LLM explica, o humano decide. Padrão universal em person-job fit de produção (filtro duro → recall por embedding → juiz no topo) |
| D10 | Veredito v1 lê **só KG serializado** (subgrafo + propriedades + constraints + `requisitos_texto`), NÃO chunks do RAG | Mantém a fronteira match/RAG na v1 com menos wiring. Texto do edital no veredito é evolução documentada (v2), não corte. Decisão do usuário 2026-07-03 |
| D11 | **Sem propagação topológica numérica** (PPR/PathSim/spreading activation) nesta fase. A expansão via catálogo existente (damping 0.30) permanece como único metapath curado | Literatura só valida ganho em grafos 10⁴–10⁵+ nós; o nosso tem 4.4k com 20-30% de ruído — propagação amplificaria lixo. Gatilho de revisão: corpus em milhares de oportunidades E higiene concluída |
| D12 | As arestas nativas são consumidas pelo **veredito LLM** (serializadas em linguagem natural, estilo HyperGraphRAG), não por regra numérica | Resolve o débito "hiperarestas subaproveitadas" sem construir motor de propagação. `exige`/`exclui` são justamente o que o juiz de elegibilidade precisa ler |
| D13 | **Sem síntese LLM por oportunidade** no build | Decisão do usuário 2026-07-03: evitar +1 chamada num sistema que já faz muitas. A ficha usa `description` do nó + metadados estruturados |
| D14 | URL oficial é **propriedade obrigatória** da Oportunidade, encanada **por fora do LLM** (bronze → build) | Metadado determinístico não passa por extração — LLM alucina/perde URL. Todo bronze já a tem; a perda atual acontece no build (ponto único compartilhado) |
| D15 | Migração dos dados existentes é **mecânica** (transformação dos JSONs), sem re-extração LLM; o único passe LLM novo é a higiene/canonicalização dos descritores | Re-extrair 35 fontes custaria tempo/dinheiro para reproduzir o que uma transformação determinística faz. A higiene é onde julgamento agrega valor |
| D16 | Nomes de tipos permanecem em **português** (`Oportunidade`, `Ator`, `Conceito`), consistente com o schema atual | Consistência com corpus, prompts e docs existentes |

## Schema v2

### Formato de arquivo (hypergraph JSON v2)

```jsonc
{
  "format_version": 2,
  "source_hash": "…",
  "proveniencia": {                      // D4 — por arquivo/oportunidade
    "fonte": "finep",                    // slug do adapter/scraper
    "url": "https://www.finep.gov.br/…", // obrigatória (D14)
    "urls_documentos": ["…pdf"],         // opcional (pdf_urls da FINEP etc.)
    "coletado_em": "2026-05-12"
  },
  "nodes": [
    {
      "id": "op:finep-602",              // NOVO — estável, prefixado por tipo
      "type": "Oportunidade",
      "kind": "edital",                  // edital|desafio|aceleracao|incubacao|parceria_pd|investimento|programa
      "aperture": "prazo",               // prazo|continua|recorrente|fechada
      "name": "…", "description": "…",
      "prazo": "…", "status": "…", "valor": "…",
      "mecanismo": ["subvencao"],        // ex-nó Mecanismo (D6/D16), slugs de core/skills.py
      "macro_temas": ["agro - bioeconomia e alimentos"],  // D8, vocabulário controlado
      "constraints": [                   // D6 — schema do produtor de eligibility_constraints
        {"tipo": "porte", "op": "in", "valor": ["ME", "EPP", "media"]},
        {"tipo": "sede_uf", "op": "in", "valor": ["SC"]},
        {"tipo": "parceria", "op": "exige", "valor": "ict"}
      ],
      "requisitos_texto": ["…resíduo não estruturável…"]
    },
    {
      "id": "ator:embrapii-senai-sp",
      "type": "Ator",
      "kind": "ict",                     // agencia|fap|ict|corporate|aceleradora|investidor
      "name": "…", "description": "…"
    },
    {
      "id": "con:visao-computacional",
      "type": "Conceito",
      "dim": "tecnologia",               // tema|tecnologia|aplicacao (D7)
      "name": "visão computacional",     // forma canônica pós-higiene
      "description": "…"
    }
  ],
  "edges": [
    {"type": "cobre", "members": ["op:finep-602", "con:visao-computacional"], "description": "…"}
  ]
}
```

Regras:
- **`id` estável e prefixado por tipo** (`op:`/`ator:`/`con:`). Arestas referenciam `members` por `id`, nunca por name. Pré-requisito de qualquer renomeação (a ligação atual por name-string lowercased quebraria silenciosamente).
- IDs de `Conceito` derivam da **forma canônica** (slug do name canonicalizado) — assim o mesmo conceito tem o mesmo `id` em qualquer subgrafo, e a travessia cross-fonte vira lookup direto em vez de casamento fuzzy.
- Campos vazios são omitidos (não `null`-poluir os nós que não têm prazo/valor, ex. Ator).

### Mapa de migração dos 12 tipos

| Tipo v1 | Destino v2 | Regra |
|---|---|---|
| Edital | `Oportunidade(kind=edital)` | direto |
| Programa | `Oportunidade(kind=programa, aperture=recorrente)` | direto (D3) |
| Investidor | `Ator(kind=investidor)` + `Oportunidade(kind=investimento, aperture=continua)` gerada do curado | D2; a oferta herda tese/estágio/ticket do `investidores.json` curado |
| ICT | `Ator(kind=ict)` | direto |
| Fonte | **removido** → bloco `proveniencia` | D4 |
| Tema / Tecnologia / Aplicação | `Conceito(dim=…)` | D7; name canonicalizado no PR de higiene |
| Mecanismo | **removido** → propriedade `mecanismo[]` (slugs de `core/skills.py`) | D6; o normalizador `_normalize_mecanismo_nodes` vira normalizador de propriedade |
| Requisito / Exclusão | **removidos** → `constraints[]` + `requisitos_texto[]` | D6; migração mecânica classifica o que casa com padrões conhecidos (porte, UF, TRL, forma jurídica) e joga o resto em `requisitos_texto` |
| Entidade | reclassificação: heurística mecânica (casa com Ator conhecido → `Ator`; senão → `Conceito(dim=tema)` marcado `origem: "entidade_v1"`) | D5; a marca permite auditar/limpar a cauda depois |

### Arestas v2

| v1 | v2 | Nota |
|---|---|---|
| `abrange_tema`, `aplica_em`, `viabiliza` | mantidas (membros agora `Conceito`/`Oportunidade`/`Ator` por id) | consumidas pela expansão de catálogo e pelo veredito |
| `parceria_com`, `financia`, `destina_a`, `pertence_a`, `resolve` | mantidas | passam a ser **lidas** pelo veredito LLM (D12) |
| `exige`, `exclui` | absorvidas pelas `constraints` quando estruturáveis; mantidas como aresta quando relacionais (`exige` parceria com Ator-kind) | D6 |

### Lado empresa

Mesmo schema v2, mesmo extractor. `_COMPANY_NODE_PROMPT` (hyper_extractor) passa a permitir `Conceito` + propriedades categóricas (mecanismo preferido, estágio, porte, UF — que alimentam o filtro de elegibilidade) e continua proibindo tipos-oferta (`Oportunidade`, `Ator`). Os campos estruturados do perfil (`porte`, `faturamento`, `sede_uf`, `forma_juridica`, `trl`) vêm do `CompanyProfile`/onboarding, não da extração — a extração cobre só o conteúdo (Conceitos).

### Fonte da verdade do schema

`WIKI.md` (bloco YAML lido por `core/kg/schema.py`) é atualizado para o schema v2 **no mesmo PR** da consolidação — a auditoria de 2026-07-03 já tinha flagrado o WIKI desatualizado em relação ao schema real; não repetir o erro.

## Funil de match v2

```
perfil empresa (campos estruturados + Conceitos embedados)
  │
  ├─ Estágio 0 — FILTRO DURO de elegibilidade            [novo]
  │    constraints da Oportunidade × campos do perfil.
  │    Determinístico, zero LLM. Constraint não avaliável (campo faltando
  │    no perfil) NÃO elimina — marca "elegibilidade não verificada" no card.
  │
  ├─ Estágio 1 — AFINIDADE multi-vector                  [mudança de agregação]
  │    cosseno Conceito-empresa × Conceito-oportunidade (threshold 0.55 mantido).
  │    Agregação: MaxSim — para cada Conceito da empresa, o MELHOR
  │    Conceito da oportunidade; score = Σ dos máximos.
  │    Substitui marginsum (Σ de TODOS os pares acima do threshold), que
  │    infla oportunidades com muitos nós redundantes. Expansão via
  │    catálogo mantida como está (damping 0.30).
  │
  └─ Estágio 2 — VEREDITO LLM no top-K                   [novo]
       K≈10–20. Input: subgrafo serializado (nós + arestas em linguagem
       natural + propriedades + constraints + requisitos_texto) + perfil.
       Output estruturado: { racional_afinidade, red_flags_elegibilidade,
       fit_mecanismo, recomendacao }. Reordena SÓ dentro do top-K e
       alimenta a explicação do card. Async + cache por par
       (workspace_id, oportunidade_id, hash dos inputs) — o card renderiza
       sem o veredito e o recebe quando pronto.
```

- Modelo do veredito: o tier 3 já em produção (`OPENAI_MODEL`, hoje gpt-4o-mini) — reuso de capacidade, custo novo ≈ K chamadas por refresh de radar, amortizado pelo cache.
- Piso `MIN_AGGREGATE_SCORE` recalibrado empiricamente após a troca para MaxSim (a escala do score muda; o valor 0.30 é do marginsum).
- Fora do funil (adiado, com gatilho): propagação topológica (D11); texto do edital no veredito (D10 → v2 do veredito).

## Guardrails de travessia (ExploreAgent / Writing)

A consolidação não pode degradar o "mapa" que os agentes navegam:

1. **Serialização completa nas tools**: quando Mecanismo/Requisito saem do grafo, `explore_tools`/`writing_tools` passam a incluir as **propriedades e constraints** do nó Oportunidade em tudo que entregam à LLM (neighborhood, resolve, get_edital). Sem isso, a LLM perde informação que via como nós.
2. **Cap de grau no BFS**: pós-canonicalização, Conceitos populares ("saúde", "IA") viram super-nós de alto fan-in. `neighborhood()` ganha limite de expansão por nó (ex. 20 vizinhos, priorizados por tipo) para não inundar o contexto. Hoje o problema não existe porque nada se conecta — é efeito colateral do sucesso.
3. **Índice por id**: `build_entity_index`/`resolve_entity` passam a resolver por `id` canônico primeiro, name como fallback — travessia cross-fonte vira lookup exato.

---

## PRs

Ordem pensada para: (1) destravar pré-requisitos técnicos antes de qualquer renomeação, (2) fazer a migração mecânica de uma vez, (3) só então rodar o passe LLM de qualidade, (4) empilhar as capacidades novas.

### PR1 — Formato v2: IDs estáveis + migrador
**O quê:** script de migração `scripts/migrate_hypergraphs_v2.py` que reescreve os 63 arquivos: gera `id` por nó, converte `members` de name→id, cria bloco `proveniencia` (vazio por ora), grava `format_version: 2`. Leitores (`kg_store`, `hypergraph_match`, `explore_tools`, `hypergraph_catalog`) passam a exigir v2.
**Por quê primeiro:** arestas ligadas por name-string quebram silenciosamente com qualquer renomeação — nada dos PRs seguintes é seguro sem isso.
**Pronto quando:** todos os arquivos migrados validam (todo member resolve para um id existente); suíte de testes verde; `find_matching_editais` devolve os mesmos resultados de antes (transformação é isomórfica).

### PR2 — Consolidação de schema (migração mecânica dos tipos)
**O quê:** aplicar o mapa de migração acima nos dados (mesmo script, fase 2) e no código: prompts do `hyper_extractor` (edital + empresa) emitem schema v2; grep-and-fix nos consumidores tipados — `AFFINITY_TYPES` → `{"Conceito"}`, `find_matching_entities` (kinds de Ator/Oportunidade), `explore_tools`, `graph_service`/router, exibição de kind no frontend, goldens da suíte matching (rename mecânico dos `frozen_nodes`), `WIKI.md` + `core/kg/schema.py`. Inclui o fix do bug de colisão em `core/skills.py` (sinônimo `"investimento"→credito` × display `equity→"Investimento"`): "investimento" passa a mapear para `equity`, e o display de equity vira "Equity/Investimento".
**Por quê antes da higiene:** a higiene deve escrever formas canônicas uma vez só, já no schema final — não em Tema/Tecnologia/Aplicação que serão renomeados depois.
**Pronto quando:** grep por tipos v1 em `core/`/`backend/` retorna zero (fora do migrador); suíte matching roda (sanity — resultado pode variar marginalmente pela reclassificação de Entidade, documentar o delta); ExploreAgent navega um subgrafo v2 ao vivo.

### PR3 — Higiene + canonicalização de descritores (o passe LLM)
**O quê:** passe build-time `scripts/canonicalize_concepts.py` sobre os nós `Conceito`:
1. **Validação** (LLM, batch): descartar não-conceito (geografia, boilerplate legal/administrativo, nomes de sistema/formulário, siglas orçamentárias) e corrigir `dim` errada. Descartes viram log auditável, não deleção cega.
2. **Canonicalização** (embedding-cluster + adjudicação LLM): fundir duplicatas semânticas cross-arquivo (TRL ×2 formas, ML em duas línguas) para forma canônica única → mesmo `id` em todos os subgrafos. Arestas re-apontadas via id (seguro pós-PR1).
3. **Macro-temas (D8)**: vocabulário controlado semeado pelo `themes_index` dos curados, versionado em `WIKI.md`; cada Oportunidade ganha `macro_temas[]` por mapeamento embedding+LLM dos seus Conceitos.
O passe roda também no fluxo de ingest (oportunidade nova entra canonicalizada), como etapa do build — não em runtime.
**Por quê:** ruído de 20-30% é o teto de qualidade do cosseno, da travessia e do veredito ao mesmo tempo — maior alavanca isolada do sistema. Guardrail: registrar merges com score, amostrar manualmente ~30 antes de aplicar (risco de over-merge).
**Pronto quando:** taxa de ruído amostrada cai para <5%; fan-in médio de Conceito sobe (medir antes/depois — é a métrica de que o "mapa" ganhou estradas); suíte matching como sanity.

### PR4 — Proveniência/URL ponta a ponta
**O quê:** encanar URL determinística do bronze até a ficha: builder preenche `proveniencia` a partir do registro bronze (`link`/`url`/`pdf_urls` — todas as fontes já têm); `ingest_curadoria_investidores/programas` param de degradar (preenchem propriedades v2 direto do curado: tese, estágio, ticket, `site`/`source_urls`, elegibilidade); o promote da Descoberta carrega a URL da staging para o build; catálogos curados são o template — nenhum campo estruturado deles se perde na conversão.
**Pronto quando:** 100% das Oportunidades têm `proveniencia.url` (exceto entradas curadas legadas sem URL, listadas para completar na curadoria); endpoint de detalhe expõe o campo.

### PR5 — Elegibilidade: constraints + filtro duro (Estágio 0)
**O quê:** convergir com a branch `feat/elig-constraints-producer`: schema de constraint tipada (formato acima), produtor na extração (LLM estrutura `Requisito`/`Exclusão` residuais em constraints no build), avaliador determinístico `core/services/eligibility.py` (constraint × perfil → sat/unsat/unknown), wired como Estágio 0 do `find_matching_editais`. Campos do perfil necessários (`porte`, `faturamento`, `sede_uf`, `forma_juridica`, `trl`) expostos no `CompanyProfile` + onboarding (parte já existe; completar o que faltar).
**Por quê "unknown não elimina":** perfil incompleto é o estado normal pré-beta; eliminar por dado faltante esconderia oportunidades boas. O card mostra "elegibilidade não verificada — complete X".
**Pronto quando:** edital com constraint de porte incompatível não aparece no radar de empresa com porte declarado; empresa sem porte declarado vê o card com flag.

### PR6 — MaxSim (Estágio 1)
**O quê:** trocar a agregação em `find_matching_editais`/`find_matching_entities`: marginsum → MaxSim por Conceito-empresa; recalibrar `MIN_AGGREGATE_SCORE`/`MIN_AGGREGATE_ENTITY` empiricamente; documentar a escala nova no código.
**Por quê separado e pequeno:** é a única mudança de comportamento do ranking core — isolá-la torna trivial reverter ou comparar. Rodar a suíte matching antes/depois e registrar o delta na spec (seção Previsto→Realizado).

### PR7 — Veredito LLM top-K (Estágio 2)
**O quê:** `core/services/match_verdict.py`: serializador do subgrafo (nós + arestas em linguagem natural + propriedades + constraints + `requisitos_texto`), prompt de veredito com output estruturado, task procrastinate + cache por par (tabela `match_verdicts`: workspace_id, oportunidade_id, input_hash, verdict jsonb), invalidação por mudança de perfil ou de oportunidade. Backend expõe o veredito no payload do match (nullable); frontend renderiza racional + red flags no card quando disponível.
**Pronto quando:** card do radar exibe racional e red flags para o top-K; segunda visita usa cache (zero chamadas novas); custo por refresh ≤ K chamadas do tier 3.

### PR8 — Ficha por oportunidade (frontend)
**O quê:** rota `/oportunidades/{id}` (aba Editais): título, kind/aperture, prazo/status/valor, mecanismo, macro-temas, chips de constraints ("até média empresa", "sede em SC"), Conceitos cobertos, atores relacionados (via arestas), **link oficial** (proveniência), veredito quando existir para o workspace. Endpoint de detalhe já existente (`hypergraph_catalog.get_edital`) estendido para o payload v2.
**Por quê por último:** consome tudo que os PRs anteriores produzem; fazê-la antes renderizaria campos vazios.
**Pronto quando:** navegar da lista para a ficha e da ficha para a página oficial funciona para FINEP, FAPESP, FAPESC, curados e descoberta promovida.

### Guardrails transversais dos PRs 2–3 (tools dos agentes)
Os itens da seção "Guardrails de travessia" entram junto: serialização de propriedades/constraints nas tools (PR2, quando os nós morrem) e cap de grau no BFS (PR3, quando os super-nós nascem).

## Riscos e governança

| Risco | Mitigação |
|---|---|
| Soft typing degenera (`kind` vira zona franca) | enums de `kind`/`aperture`/`dim` validados no build (rejeita valor fora da lista); adicionar valor = editar WIKI.md (fonte da verdade) conscientemente. Quando um kind ganhar topologia própria (não só campos opcionais), promover a split formal — não empilhar campos |
| Over-merge na canonicalização | merges logados com score + amostragem manual antes de aplicar (PR3) |
| Super-nós inundam contexto dos agentes | cap de grau no BFS (PR3) |
| Latência do veredito no radar | async + cache por par; card renderiza sem ele (PR7) |
| Migração quebra consumidor esquecido | PR1/PR2 exigem grep-zero por tipos/campos v1 e teste ao vivo do ExploreAgent + WritingSession sobre dados v2 |
| Curados sem URL (legado) | lista de pendência gerada no PR4; completar na curadoria, não bloquear o PR |

## Fora de escopo (explícito)

- **Propagação topológica numérica** — adiada (D11), gatilho documentado.
- **Texto do edital no veredito** — v2 do veredito (D10).
- **Síntese LLM por oportunidade** — cortada (D13).
- **Storage dos hipergrafos em Postgres (kg_artifacts JSONB)** — débito ortogonal já rastreado; o formato v2 passa pelo mesmo seam (`kg_store`), então a migração de storage não é acoplada a esta spec.
- **Camada declarativa Template/Method do hyperextract** — o schema continua definido no código/WIKI; re-avaliar adoção da camada declarativa só se a lib evoluir (auditoria 2026-07-03).

## Previsto → Realizado

### PR1 — Formato v2: IDs estáveis + migrador (2026-07-03)

**Pronto quando (todos atingidos):**
- ✓ todos os arquivos migrados validam — `migrate_hypergraphs_v2.py` confirma "todo membro resolve para um id existente" (0 não resolvidos);
- ✓ suíte de testes verde — 636 passed, 35 skipped (3 testes de Store Postgres deselecionados: gated em `DATABASE_URL`, ortogonais ao KG);
- ✓ `find_matching_editais` isomórfico — eval matching **recall@8 0.881 / ruído 3.625**, idêntico ao baseline documentado em `hypergraph_match.py` (recall 0.88 / ruído 3.6).

**Divergências do plano:**

1. **Contagem de arquivos: 35, não 63.** O corpus real são 35 hipergrados em `data/knowledge_graph/hypergraphs/` (32 editais `*__*` + 3 catálogos `ict`/`investidores`/`programas`). O "63" da spec estava desatualizado. (`investidores.json`/`programas.json` na raiz do `knowledge_graph/` são os CURADOS, formato diferente — PR4.)

2. **Prefixo de id deriva do tipo v1** (`ed:`/`tema:`/`mec:`/`ict:`/…), não `op/ator/con`. PR1 precede a consolidação de tipos (PR2); o id é `{'{prefixo}'}:{'{slug(name)}'}`, determinístico a partir de (tipo, name). Consequência boa e não prevista: o MESMO (tipo, name) recebe o MESMO id em qualquer subgrafo → a resolução cross-fonte (antes por casamento de (type, name)) já é lookup por id, sem esperar o PR3. O PR2 remapeia os prefixos.

3. **`upgrade-on-read` no `kg_store`** (não estava explícito no plano). `load_hypergraph`/`load_all_hypergraphs` aplicam `migrate_to_v2` (idempotente) a cada leitura. Motivo: os arquivos hypergraphs/ são gitignored e o extractor só emite v2 no PR2 — sem isso, blobs/arquivos v1 e extrações frescas quebrariam os leitores. Assim "leitores exigem v2" vira "leitores sempre VÊEM v2".

4. **Funções puras auto-normalizam** (`neighborhood`, `build_entity_index`, `list_entity_catalog` chamam `migrate_to_v2` na entrada). Necessário porque recebem grafos em memória direto (fixtures de teste v1, não via kg_store). API pública de `build_entity_index`/`resolve_entity` mantida em (type, name) — o cross-source deriva (type,name) do nó-id visitado, sem quebrar callers/testes.

5. **Dados dropados na migração:** 171 members dangling (1,2% — já eram no-op em todo leitor) + 19 arestas que sobraram com <2 membros. Logados pelo migrador. `proveniencia` criado vazio (`{}`) — PR4 preenche a URL.

6. **Entrega dos dados:** como hypergraphs/ não é versionado, os 35 arquivos migrados são locais; a publicação p/ prod é via `kg_store`→Postgres pelo build (não por commit git). Backup de `data/knowledge_graph/` feito antes da reescrita (`data/knowledge_graph.bak.<ts>`, fora do git).

**Toca (código):** `core/kg/migrate_v2.py` (novo), `scripts/migrate_hypergraphs_v2.py` (novo), `core/kg/kg_store.py`, `core/kg/hypergraph_catalog.py`, `core/llm/agent_tools/explore_tools.py`, `core/services/hypergraph_match.py`, `tests/test_kg_store.py`.
