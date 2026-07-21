# Spec — KG v2: Oportunidade/Ator/Conceito + funil de match

Status: **histórica, substituída** por [`v3-unified.md`](../specs/v3-unified.md) ·
implementada no hipergrado v2 e posteriormente aposentada pela migração gold.

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

`docs/domain/schema.md` (bloco YAML lido por `core/kg/schema.py`) é atualizado para o schema v2 **no mesmo PR** da consolidação — a auditoria de 2026-07-03 já tinha flagrado o WIKI desatualizado em relação ao schema real; não repetir o erro.

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
**O quê:** aplicar o mapa de migração acima nos dados (mesmo script, fase 2) e no código: prompts do `hyper_extractor` (edital + empresa) emitem schema v2; grep-and-fix nos consumidores tipados — `AFFINITY_TYPES` → `{"Conceito"}`, `find_matching_entities` (kinds de Ator/Oportunidade), `explore_tools`, `graph_service`/router, exibição de kind no frontend, goldens da suíte matching (rename mecânico dos `frozen_nodes`), `docs/domain/schema.md` + `core/kg/schema.py`. Inclui o fix do bug de colisão em `core/skills.py` (sinônimo `"investimento"→credito` × display `equity→"Investimento"`): "investimento" passa a mapear para `equity`, e o display de equity vira "Equity/Investimento".
**Por quê antes da higiene:** a higiene deve escrever formas canônicas uma vez só, já no schema final — não em Tema/Tecnologia/Aplicação que serão renomeados depois.
**Pronto quando:** grep por tipos v1 em `core/`/`backend/` retorna zero (fora do migrador); suíte matching roda (sanity — resultado pode variar marginalmente pela reclassificação de Entidade, documentar o delta); ExploreAgent navega um subgrafo v2 ao vivo.

### PR3 — Higiene + canonicalização de descritores (o passe LLM)
**O quê:** passe build-time `scripts/canonicalize_concepts.py` sobre os nós `Conceito`:
1. **Validação** (LLM, batch): descartar não-conceito (geografia, boilerplate legal/administrativo, nomes de sistema/formulário, siglas orçamentárias) e corrigir `dim` errada. Descartes viram log auditável, não deleção cega.
2. **Canonicalização** (embedding-cluster + adjudicação LLM): fundir duplicatas semânticas cross-arquivo (TRL ×2 formas, ML em duas línguas) para forma canônica única → mesmo `id` em todos os subgrafos. Arestas re-apontadas via id (seguro pós-PR1).
3. **Macro-temas (D8)**: vocabulário controlado semeado pelo `themes_index` dos curados, versionado em `docs/domain/schema.md`; cada Oportunidade ganha `macro_temas[]` por mapeamento embedding+LLM dos seus Conceitos.
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
| Soft typing degenera (`kind` vira zona franca) | enums de `kind`/`aperture`/`dim` validados no build (rejeita valor fora da lista); adicionar valor = editar docs/domain/schema.md (fonte da verdade) conscientemente. Quando um kind ganhar topologia própria (não só campos opcionais), promover a split formal — não empilhar campos |
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

**Toca (código):** `core/kg/migrate_v2.py` (novo), `scripts/migrate_hypergraphs_v2.py` (novo), `core/kg/kg_store.py`, `core/kg/hypergraph_catalog.py`, `core/llm/agent_tools/explore_tools.py`, `core/services/hypergraph_match.py`, `tests/unit/test_kg_store.py`.

### PR2 — Consolidação de schema (migração mecânica dos tipos) (2026-07-03)

**Pronto quando (todos atingidos):**
- ✓ grep-zero de tipos v1 em `core/`+`backend/` (fora do migrador) — o único hit restante é uma palavra "Edital" em comentário-prosa do `_collapse_editais` (não referência de tipo). `tipo_entidade` em `profile_extractor`/`profile_tools` é **valor de campo de perfil** (a empresa *é* uma "ICT"), não tipo de KG — fora de escopo; idem os termos de busca de exemplo em `retriever.py`;
- ✓ suíte matching roda (sanity) — **recall@8 0.833 / ruído 3.375** (baseline v1 0.881 / 3.625): recall −0.048, ruído melhorou 0.25. Nenhum caso colapsou (todos ≥0.333, 5/7 em 1.0). O delta é **drift de embedding** do `_node_text` (prefixo mudou de `Tema:`/`Aplicação:` para `tema:`/`aplicacao:` — minúsculo/sem acento), NÃO da reclassificação de Entidade (essas ficam inertes, ver divergência 2). Sanity, não gate;
- ✓ ExploreAgent navega um subgrafo v2 ao vivo — `neighborhood('602', cross_source=True)` sobre o corpus em disco: header `[Oportunidade/edital]`, labels `(Ator/ict)`/`(Conceito)`, e o **guardrail de travessia** serializa `requisitos_texto` foldado (a LLM não perde o que via como nós);
- ✓ smoke do extractor v2 (fork 3): 1 chamada LLM real em `finep/602` → 32 nós **todos v2** (Oportunidade/edital, Ator kinds agencia/ict/fap/corporate, Conceito dims), edges v2 (`exige` sobrevive apontando p/ Ator). Round-trip do Pydantic validado — qualidade só será medida no próximo ETL (D15: corpus migrado mecanicamente, não re-extraído).

**Ajustes de fork do usuário (aplicados):**
1. **`_node_text` usa `dim → kind → type`** como prefixo (não achata p/ `Conceito:`): `tema: X`, `ict: X`, `edital: X`. Preserva o sinal v1 e minimiza drift. (Custo: prefixo minúsculo/sem-acento difere de `Tema:`/`Aplicação:` → o −0.048 de recall acima. Recuperável exatamente mapeando `dim→label` v1, mas isso contraria "usar a dim"; deixado como está.)
2. **Ex-Entidade → `Conceito(origem=entidade_v1)` EXCLUÍDA da afinidade E do entity match** (em v1 Entidade nunca esteve em AFFINITY_TYPES — incluí-la seria mudança de comportamento). `_is_affinity()` filtra `entidade_v1` em todo o motor. A promoção nó-a-nó desses descritores é decisão do **PR3** (higiene). 1005 Entidade reclassificadas, todas inertes.
3. **`exclusoes_texto[]` separado de `requisitos_texto[]`** (fork 5) — o veredito do PR7 precisa distinguir "exige X" de "não pode ser Y".
4. **Fold file-level explícito** (fork 4): Mecanismo/Requisito/Exclusão → propriedades da `Oportunidade(kind=edital)` do arquivo; **catálogo sem nó edital → no-op logado** (as facetas genéricas de `programas.json`/`ict.json` são descartadas — PR4 re-ingere do curado); múltiplos editais → anexa a todos + warn.

**Divergências do plano:**
1. **Extrator v2 emite o backbone (Oportunidade/Ator/Conceito) + `mecanismo`**; `requisitos_texto`/`exclusoes_texto`/`constraints` do **corpus** vêm do fold mecânico (esta PR), e para extrações FRESCAS vêm do produtor do **PR5** — a extração chunk-a-chunk não junta requisitos espalhados num nó só. Edges `exige`/`exclui` só sobrevivem quando relacionais (apontam a Ator), como previa D6/D12.
2. **Bug latente do PR1 corrigido de passagem:** `_entity_attribution` indexava membros de aresta por **name**, mas em v2 são **ids** → a atribuição "ICT casa via tema" estava quebrada na main (sem gate de entidade, passou batido). Reescrita id-based; agora funciona (SENAI/SP volta a carregar temas).
3. **`migrate_to_v2` passou a compor formato + tipos** (`_migrate_format` → `consolidate_to_v2_types`, ambos idempotentes), em vez de duas funções separadas nos call-sites. Zero mudança nos leitores (upgrade-on-read via `migrate_to_v2` já era o ponto único). Detecção de "já consolidado" por inspeção de tipo (`is_types_v2`), não por bump de `format_version` (segue 2).
4. **`EntityMatch.kind` virou slug v2** (`investidor`/`programa`/`ict`, era `Investidor`/…) — mudança de contrato que cruza o stack: `explore.py`, `opportunity_service`, `match_tools`, e o **frontend** (`MatchedEntityCard` com mapa slug→label de display, `api.ts`).
5. **docs/domain/schema.md §6.4 nova** (schema do hipergrado v2 + enums `kind`/`aperture`/`dim`) + `schema.py::validate_v2_node` (sanity de enum no build, loga não bloqueia). O migrador reportou **0 violações de enum**.
6. **Dados:** 35 arquivos reescritos in-place p/ v2 (4379→4003 nós, 376 facetas foldadas; 4552→3859 arestas). Backup fresco `data/knowledge_graph.bak.pr2_<ts>` (31M, fora do git) — MANTER com o do PR1 até o **PR3** terminar.

**Toca (código):** `core/kg/migrate_v2.py`, `core/kg/schema.py`, `core/kg/hypergraph_catalog.py`, `core/kg/temporal.py`, `core/services/hypergraph_match.py`, `core/services/opportunity_service.py`, `core/skills.py`, `core/retrieval/hyper_extractor.py`, `core/llm/agent_tools/explore_tools.py`, `core/llm/agent_tools/match_tools.py`, `backend/routers/explore.py`, `scripts/migrate_hypergraphs_v2.py`, `docs/domain/schema.md`, `frontend/src/components/frontdoor/MatchedEntityCard.tsx`, `frontend/src/lib/api.ts`, `data/evaluation/golden/matching.json` (frozen_nodes), `tests/{test_kg_store,test_get_node_neighborhood,test_find_matching_entities,test_temporal,test_load_skill_tool}.py`.

### PR3 — Higiene + canonicalização de descritores (2026-07-04)

**Pronto quando:**
- ✓ taxa de ruído amostrada <5% — amostra aleatória n=50 pós-higiene: **2/50 lixo claro (4%)** ("Ativo Total", "Aceleração das Startups"), ante ~20-30% medidos no levantamento original. Honestidade: mais ~3 casos *vagos* na amostra ("comércio", "ecossistema de CTI") que não são lixo, são conceitos fracos — contá-los levaria a ~10%;
- ✗→entendido: **fan-in médio de Conceito CAIU** (1.286 → 1.098) em vez de subir. A métrica prevista tinha a direção errada no curto prazo: o fan-in alto do corpus era majoritariamente DO RUÍDO (a própria spec mediu: 11 dos 15 names mais repetidos eram geografia/boilerplate) — a validação demoliu essas estradas falsas, e os 11 merges conservadores não compensam na média. As estradas REAIS aparecem pontualmente e funcionam: "tecnologias de interesse p/ soberania e defesa" conecta 4 arquivos FINEP (era 1), TRL unificado em forma canônica única, live check `neighborhood(cross_source=True)` atravessando 775→768/776/784. A métrica útil daqui pra frente é fan-in dos Conceitos NÃO-ruído;
- ✓ suíte matching sanity — recall@8 **0.833** (idêntico ao pós-PR2), ruído **3.375 → 3.125** (melhorou).

**Números:** 2.442 ids de Conceito julgados → canon: **451 descartes** (geografia/legal/administrativo/genérico/sistema), **245 retipados → Ator**, 28 `dim` corrigidas, 10 promoções ex-Entidade, **34 aliases** (11 grupos de merge LLM + 8 renomeações manuais). Corpus: 4.003→3.117 nós (Conceito 3.140→1.891 instâncias, 2.442→1.722 ids), 3.859→2.645 arestas.

**Divergências do plano:**
1. **Adjudicação de merge exigiu modelo mais forte + guardas mecânicos.** gpt-4o-mini over-mergeava RELACIONADOS (~20/87 grupos: "arranjo em rede"+"arranjo simples", guarda-chuvas inventados tipo "wzl gear toolbox" ← 4 ferramentas). Solução: `CANON_MERGE_MODEL=gpt-4o` só na adjudicação (~10² calls), prompt com teste de intercambialidade + anti-exemplos, e 2 guardas mecânicos (cap 6 membros/grupo; slug do canônico deve derivar de um membro — mata nome inventado). Resultado: 87→11 grupos, todos revisados manualmente (não só ~30), 0 over-merge. Trade-off assumido: under-merge (perdeu p.ex. drug-discovery bilíngue) — direção segura, passe re-rodável.
2. **Ex-Entidade não é conceito: é ator ou lixo (D5 na prática).** O veredicto "ator" (não previsto na spec) foi necessário: dos 594 ids ex-Entidade, 459 saíram "ator" e 123 "descartar" — só **12 viraram Conceito promovido**. Guard de enum: 221 `ator_kind` inválidos devolvidos pelo LLM ("generico", "sistema") são classes genéricas de ator ("startups", "agente público") → viram descarte, não Ator novo.
3. **Curadoria manual é parte do fluxo, não exceção.** A validação LLM deixou passar critérios-de-avaliação de edital ("Proposta" em 5 arquivos, "Grau de Inovação", "Relevância…") e scaffold ("Linha Temática I–VI"). O plano JSON é editável: 30 descartes manuais (marcados `manual: true`) + veredicto novo **"renomear"** (manual-only no `build_canon`) p/ despir prefixo scaffold — "Linha Temática 2: Complexo da Saúde" → "Complexo da Saúde", fundindo com o nó canônico existente.
4. **Canon map como artefato durável + ingest incremental.** `concept_canon` (chave nova no kg_store) carrega `validated[]` (2.445 ids) — o ingest (`canonicalize_fresh_graph` no hyper_extractor) replayia o canon determinístico e só chama LLM p/ Conceitos INÉDITOS + macro-temas de Oportunidades novas (gate `CANON_FRESH_LLM`, fail-open: ETL nunca morre por higiene). Lado empresa: replay determinístico SEM LLM (grafo efêmero, vereditos não persistem). **Pendência de deploy: publicar canon + hipergrafos no PG de prod** (o apply local rodou com SUPABASE_* suprimido de propósito — branch WIP não escreve em prod; rodar `python -m scripts.canonicalize_concepts apply` com creds no deploy, ou deixar o build republicar).
5. **Vocabulário de macro-temas v1 = 10 valores** (6 seeds do `themes_index` dos curados + 4 ancorados no corpus: defesa/soberania, meio ambiente/água, petróleo/gás/mineração, construção/cidades), versionado no WIKI §6.4 (`schema.macro_temas_vocab()`, validado em `validate_v2_node`). 65 nós Oportunidade anotados; 9 editais transversais corretamente SEM macro-tema (GlobalStars, capital semente).
6. **Guardrails de travessia entregues:** cap de grau no BFS (`BFS_NODE_DEGREE_CAP=20` por nó da frontier, arestas com Oportunidade/Ator expandem primeiro) + `macro_temas` serializado no `neighborhood` (propriedade nova visível à LLM).
7. **Fix latente do PR2 foldado no commit do PR2 (#50):** extração fresca (tipos v2 sem ids) ganhava prefixo-fallback `no:` no upgrade-on-read — `_TYPE_PREFIX` ganhou os tipos v2 (`op:/ator:/con:`), senão a resolução cross-fonte por id quebraria em todo edital novo.
8. **Dados/backup:** reescrita in-place dos 35 arquivos; rollback pré-PR3 em `data/knowledge_graph.bak.pr3_20260704_022409` (fora do git). Com o PR3 fechado, os backups do PR1/PR2 podem ser removidos após o merge (critério da spec atendido).

**Toca (código):** `core/kg/canonicalize.py` (novo), `scripts/canonicalize_concepts.py` (novo), `core/kg/schema.py`, `core/kg/kg_store.py`, `core/kg/migrate_v2.py` (PR2, fix 7), `core/retrieval/hyper_extractor.py`, `core/llm/agent_tools/explore_tools.py`, `docs/domain/schema.md` (§6.4 macro_temas), `tests/unit/test_canonicalize.py` (novo), `tests/unit/test_kg_store.py`.

### PR4 — Proveniência/URL ponta a ponta (2026-07-04)

**Pronto quando:**
- ✓ 100% das Oportunidades(edital) com bronze têm `proveniencia.url` — **30/30 editais reais** encanados do bronze (finep/fapesp/fapesc). Os 2 "faltantes" na cobertura bruta (93,8%) são fixtures sintéticos órfãos (`finep__1` "Edital 1", `fapesp__2` "Edital 2") sem silver nem bronze — some no próximo ETL real, não são editais;
- ✓ endpoint de detalhe expõe o campo — `GET /editais/{id}` (`edital_card`) carrega `official_url` (também no resumo, p/ a lista linkar), `document_urls` (PDFs), `collected_at`; live check em `finep:778` → URL oficial + 9 PDFs + `2026-06-13`.

**Decisão de escopo (usuário, 2026-07-04): URL-first agora, desdobramento depois.**
Os catálogos curados (investidores/programas) NÃO foram reconstruídos deterministicamente neste PR — seguem extraídos por LLM, com `proveniencia` de arquivo vazia (documentados como pendência: `ict`, `investidores`, `programas`). Motivo: cada item curado tem URL PRÓPRIA (per-nó, não per-arquivo), e o rebuild determinístico é o desdobramento **D2** (investidor → `Ator` + `Oportunidade(kind=investimento)`), que também exige wiring no match e mudança do contrato de entidades no frontend. Atacar isso agora com name-match fuzzy seria exatamente o "achatamento" que a spec condena.
> **Condição registrada (não-opcional):** o **PR4.1** (desdobramento D2 + rebuild determinístico dos curados a partir do JSON, preservando tese/estágio/ticket/elegibilidade/URL por item) entra na fila **imediatamente após o PR6, antes do PR7/PR8** — as Oportunidades de investimento precisam EXISTIR antes do veredito (PR7) e da ficha (PR8) poderem exibi-las.

**Divergências do plano:**
1. **Proveniência é capacidade do adapter, não do builder.** Cada `SourceAdapter` ganhou `provenance(native_id) -> {fonte, url, urls_documentos, coletado_em}` (default vazio na base). É o lugar certo: o adapter já sabe ler o bronze da sua fonte e casar por id. finep casa por `chamada_id` (url=`link`, docs=`pdf_urls`); fapesp por native_id-da-URL (só `url`, texto inline); fapesc por `native_id` (url + `edital_pdf_url`); web por `url_hash` (cobre a Descoberta promovida). `coletado_em` = data de `data_extracao` (sem hora).
2. **Discovery promote já estava coberto.** O promote (`backend/routers/discovered.py`) grava `url`+`url_hash`+`data_extracao` no bronze web; a URL flui staging → bronze → `web.provenance()` → builder, sem novo wiring — só faltava o adapter expor.
3. **Backfill mecânico (D15), não re-extração.** Os 32 editais migrados carregavam `proveniencia: {}`. `scripts/backfill_proveniencia.py` reescreve in-place a partir do bronze (zero LLM). O builder (`run_hyper_extract`) preenche na extração fresca via `_provenance()` (fail-open — proveniência ausente nunca derruba o build).
4. **Sanity:** eval matching **recall@8 0.833 / ruído 3.125 — idêntico ao PR3** (esperado: proveniência é metadado, o match não a lê). 33 testes KG/adapter/catalog verdes.
5. **Dados/backup:** backup pré-PR4 em `data/knowledge_graph.bak.pr4_20260704_102628` (fora do git). Reescrita in-place dos 30 editais reais.

**Toca (código):** `pipeline/adapters/base.py` (contrato `provenance` + helper `coletado_em`), `pipeline/adapters/{finep,fapesp,fapesc,web}.py`, `core/retrieval/hyper_extractor.py` (`_provenance` + wiring), `core/kg/hypergraph_catalog.py` (card expõe proveniência), `scripts/backfill_proveniencia.py` (novo).

### PR5 — Elegibilidade: constraints + filtro duro (Estágio 0) (2026-07-04)

**Pronto quando (ambos demonstrados end-to-end):**
- ✓ edital com constraint de porte incompatível NÃO aparece no radar de empresa com porte declarado — live: `finep__734` tem `porte in [mei,me,epp,media]`; empresa `GRANDE` → **734 eliminado** do `find_matching_editais`; empresa `ME` → 734 presente, `status=elegivel`;
- ✓ empresa sem porte declarado vê o card com flag — perfil sem porte → 734 presente, `elegibilidade.status=nao_verificada`, `unknown=["porte deve ser um de [...]"]` (o card diz qual campo completar). `unsat` elimina; `unknown` nunca elimina.
- ✓ sanity eval matching **0.833 / 3.125 — idêntico ao PR3/PR4** (Estágio 0 é opt-in: o harness chama `find_matching_editais` sem `profile`, então nada é filtrado).

**Números do produtor:** 32 editais, 30 com texto residual (req/excl), **6 com constraints produzidas → 9 constraints** (conservador por desenho — constraint falso = eliminação falsa). Ex.: `finep__602` faturamento ≤ 16M; `finep__734` porte até média; `finep__778/779/780` faturamento entre 16M e 90M.

**Divergências do plano:**
1. **A branch `feat/elig-constraints-producer` estava obsoleta.** O "PR1 elig" (#31, `44a9d8444`) declarava `eligibility_constraints` como campo *synthesized* da **wiki page** — pipeline REMOVIDO (CLAUDE.md). PR5 nasceu fresco sobre o schema v2 (D6 já reservava `constraints[]`). O campo legado (`§4.2`, shape `region|company_age|revenue|cnae|consortium` + nota "nunca gate §D2") é doc de pipeline morto; a nova §6.4 (`{tipo, op, valor}`, hard-gate com "unknown não elimina") o **supersede** — anotado no WIKI.
2. **Constraint tipada evaluável, não descritiva.** Schema `{tipo, op, valor}`: `tipo ∈ [porte, sede_uf, faturamento, trl, forma_juridica, parceria]`, `op ∈ [in, not_in, lte, gte, exige]` (WIKI §6.4 + `schema.constraint_tipos()/ops()` + `validate_v2_node`). Mapeamento fixo tipo→campo-do-perfil no avaliador (`porte`←tamanho_empresa, `sede_uf`←uf, `faturamento`←faturamento_anual, `trl`←trl, `forma_juridica`←tipo_entidade; `parceria` é relacional → sempre unknown, não há campo de parceria no perfil).
3. **Região × UF (correção no avaliador).** O produtor emite macro-regiões em `sede_uf` ("NE"/"CO"). O perfil guarda a UF (2 letras). O avaliador expande região→conjunto de UFs (mapa determinístico) antes de testar pertinência — `sede_uf in [NE]` casa "BA". Guarda de colisão: `SE` nu = **Sergipe** (UF), não Sudeste (só a forma escrita "SUDESTE" expande).
4. **Estágio 0 é opt-in por `profile`.** `find_matching_editais(..., profile=None)` = comportamento idêntico ao anterior (nada filtrado, `elegibilidade=None` no match). Wired no **router de explore** (`backend/routers/explore.py`, a superfície que produz os cards `matched_editais`), NÃO na tool do agente (`match_tools` só tem `profile_text`, sem os campos estruturados — threading do perfil estruturado até a tool fica como follow-up se necessário). `EditalMatch` ganhou `elegibilidade` (+ `to_dict`).
5. **PR5.5 (campos de perfil) foi no-op:** todos os 5 campos necessários (`tamanho_empresa`, `uf`, `faturamento_anual`, `trl`, `tipo_entidade`) já existiam em `CompanyProfile` (domain) E `CompanyProfileSchema` (backend). Nada a adicionar. (`company_age`/`ano_fundacao` não virou tipo de constraint — nenhum edital do corpus o exigiu; a enum é extensível no WIKI quando aparecer.)
6. **Produtor build-time, fail-open, gated.** `core/kg/constraints_producer.py` (irmão de `canonicalize.py`): gpt-4o-mini (`CONSTRAINTS_MODEL`), JSON mode, temp 0. No ETL fresco, `run_hyper_extract` chama via gate `ELIG_CONSTRAINTS_LLM` (default on) + fail-open (sem key/erro → sem constraints → "unknown não elimina", ETL nunca morre). Passe de corpus: `scripts/extract_constraints.py`.
7. **Frontend:** `MatchedEdital.elegibilidade?` (tipo novo em `api.ts`) + chip "⚠️ Elegibilidade não verificada — complete o perfil (…)" no `MatchedEditalCard` quando `nao_verificada`. `tsc --noEmit` limpo.
8. **Testes:** `tests/unit/test_eligibility.py` (11 casos: sat/unsat/unknown por tipo, região, agregação, perfil-objeto). Suíte: **657 passed** (+11), 37 skipped; os 3 errors em `test_memory_store_postgres` são pré-existentes (env-gated `DATABASE_URL`, flake de teardown async no full-run — reproduzidos idênticos no base PR3, não são desta PR).
9. **Dados/backup:** backup pré-PR5 em `data/knowledge_graph.bak.pr5_20260704_104523` (fora do git); corpus reescrito in-place com `constraints[]`.

**Toca (código):** `docs/domain/schema.md` (§6.4 constraint schema), `core/kg/schema.py` (accessors + validate), `core/kg/constraints_producer.py` (novo), `core/services/eligibility.py` (novo), `core/services/hypergraph_match.py` (Estágio 0 + `EditalMatch.elegibilidade`), `core/retrieval/hyper_extractor.py` (`_produce_constraints` + wiring), `core/kg/hypergraph_catalog.py` (card expõe `constraints`), `backend/routers/explore.py` (passa `profile`), `scripts/extract_constraints.py` (novo), `tests/unit/test_eligibility.py` (novo), `frontend/src/lib/api.ts`, `frontend/src/components/frontdoor/MatchedEditalCard.tsx`.

### PR6 — MaxSim (Estágio 1) (2026-07-04)

Branch nova a partir da main (com PR2–PR5 já mergeados: #50/#54/#52/#53).

**Antes → Depois (suíte matching, gate = sanity, não estatístico):**

| | agregação | piso | recall@8 | ruído |
|---|---|---|---|---|
| **antes** | marginsum `Σ(cosseno−threshold)` | `MIN_AGGREGATE_SCORE=0.30` | **0.8333** | **3.1250** |
| **depois** | MaxSim `Σ máx por nó-empresa` | `MIN_AGGREGATE_SCORE=1.35` | **0.8334** | **3.0000** |

Recall preservado (Δ≈0, ruído de arredondamento); ruído **−0.125** (3.125→3.0).

**Recalibração (empírica, no golden):** a escala do score muda (MaxSim soma cosseno cru ~1–4, marginsum somava o marginal ~0.05–0.2). Sweep do piso: recall@8 fica no platô 0.8333 até **1.39** e despenca em 1.40 (0.714); o ruído cai a **3.0** a partir de **1.33**. Janela boa `[1.33, 1.39]` → **1.35** (central, com margem do precipício). `MIN_AGGREGATE_ENTITY` reescalado 0.05→**0.60** (≈ um casamento direto a cosseno ≥ 0.60; em MaxSim a afinidade de 1 aresta é o próprio cosseno) — **provisório**, ainda sem golden de entidade.

**Divergências do plano:**
1. **Primeira leitura enganosa, corrigida por sweep fino.** Em pisos grossos MaxSim parecia PIOR (floor 1.2 → ruído 4.0 a recall 0.833; floor 1.4 → recall despenca). O sweep fino 1.2–1.4 revelou o platô real (1.33–1.39: recall 0.833 / ruído 3.0). Lição registrada: recalibrar em passo fino perto do precipício de recall.
2. **Por que o ganho é pequeno neste corpus:** os falsos-positivos do golden (`finep__781`, `fapesp__18203`, `finep__780`) são editais TEMATICAMENTE LARGOS que casam muitos nichos com cosseno alto de verdade — não é inflação por nós redundantes (que o MaxSim mata), é afinidade genuína. Numa oferta pequena (35 arquivos) o problema que o MaxSim resolve é modesto; o ganho principal é forward-looking (corpus maior + late-interaction, família ColBERT) e a robustez de ranking (uma oferta densa não infla). O Estágio 2 (veredito LLM, PR7) é quem vai separar "largo mas raso" de "nichado".
3. **3 sítios de agregação trocados** por um helper único `_maxsim(edges)`: `find_matching_editais`, `find_matching_entities`, e `_expand_match_via_catalog` (expansão de catálogo, paths já com damping) — antes eram três `sum(score−threshold)` duplicados.
4. **Sanity de testes:** 72 passed nos testes de match/hypergraph/eligibility; suíte cheia idem (os 3 errors de `test_memory_store_postgres` seguem pré-existentes/env-gated).

**Toca (código):** `core/services/hypergraph_match.py` (helper `_maxsim` + 3 sítios + `MIN_AGGREGATE_*` recalibrados + docstrings).

### PR4.1 — Desdobramento D2 dos curados + rebuild determinístico + threading do perfil (2026-07-04)

Branch da main. Agendada (janela pinada pelo usuário) **entre PR6 e PR7**: as Oportunidades de investimento precisam EXISTIR antes do veredito (PR7) e da ficha (PR8). Nota de stack: PR6 (MaxSim, #55) e esta saem em paralelo da main; conflito trivial só no append da spec (seções distintas) — sem conflito de código (esta NÃO toca `hypergraph_match.py`).

**Pronto quando:**
- ✓ curados reconstruídos deterministicamente (zero LLM), preservando as facetas — **investidores 25→78 nós** (17 `Ator(investidor)` + **17 `Oportunidade(investimento)`** + Conceitos), **programas** enriquecidos (10 Oportunidades, `mecanismo`/`estagio`/`ticket`/`requisitos_texto`); **100% das Oportunidades curadas com `url`** (17+10);
- ✓ desdobramento **D2** vivo: cada fundo → `Ator(investidor)` (casa como antes) + `Oportunidade(kind=investimento, aperture=continua, mecanismo=[equity])` ligada por `pertence_a`, carregando tese/estágio/ticket/URL — o que o extractor-LLM achatava;
- ✓ threading do perfil até a tool do agente: `GRANDE` no perfil → a tool `find_matching_editais` do ExploreAgent elimina `finep__734` (Estágio 0 ativo DENTRO da tool, não só no match direto do router); sem perfil, não filtra;
- ✓ eval matching **antes 0.8333/3.125 → depois 0.8333/3.125 (idêntico)** — esperado: `find_matching_editais` só rankeia `kind=edital` de arquivos com `__`; catálogos (sem `__`) não entram, então o rebuild não mexe no ranking de editais. É smoke/regressão, não medida de ganho (o ganho é estrutural: as Oportunidades de investimento passam a existir).

**Divergências do plano:**
1. **Offer de investimento é nó IRMÃO, topologia de match inalterada** (decisão registrada e aceita): o fundo segue casando como `Ator(kind=investidor)` via `find_matching_entities`; a `Oportunidade(investimento)` carrega os dados estruturados para o PR7/PR8 consumirem. Transformar o offer no card do radar (D1 pleno) é trabalho do PR8 — evita mexer no contrato de entidades do frontend agora.
2. **Build determinístico substitui o LLM só para investidores/programas**; `ict` (narrativo, de `bronze/ict_raw`) segue no extractor-LLM. `load_investidores_text`/`load_programas_text` ficaram órfãos e foram **removidos**.
3. **Higiene canônica aplicada aos curados** (replay determinístico, `llm_new=False`) alinha os Conceitos curados ao vocabulário do ecossistema. Efeito colateral benigno: 1 keyword-Conceito foi retipada para `Ator/ict` (o canon a conhece como organização) → 1 aresta `viabiliza` liga dois Atores; `_entity_attribution` trata (no-op). Coberto no teste.
4. **URL por-NÓ, não file-level** (divergência do bloco `proveniencia` do PR4): arquivos de catálogo são multi-item, então `url`/`urls_documentos` vão como propriedade de cada Oportunidade. A ficha (PR8) lê do nó.
5. **Threading:** `profile` (dict) encanado `explore.py → explore_agent.{explore,explore_with_meta,_explore_agent} → build_match_tools(profile=) → find_matching_editais(profile=)`. WritingSession NÃO se aplica (usa só `find_matching_entities`, sem filtro de elegibilidade dura).
6. **Testes:** `tests/test_curadoria_build.py` (6 casos: D2, pertence_a, atribuição, programa enriquecido, macro_temas ⊆ vocab, roda sem `OPENAI_API_KEY`). Suíte: **663 passed** (+6), 37 skipped, os 3 errors de `test_memory_store_postgres` seguem pré-existentes.
7. **Dados/backup:** backup pré-PR4.1 em `data/knowledge_graph.bak.pr4_1_20260704_150848` (fora do git); `hypergraphs/{investidores,programas}.json` reescritos.

**Toca (código):** `core/kg/curadoria_build.py` (novo), `core/retrieval/hyper_extractor.py` (CATALOG_LOADERS só ict + build determinístico dos curados; loaders órfãos removidos), `core/services/explore_agent.py` (thread `profile`), `core/llm/agent_tools/match_tools.py` (`build_match_tools(profile=)` → tool), `backend/routers/explore.py` (passa `profile`), `scripts/rebuild_curadoria.py` (novo), `tests/test_curadoria_build.py` (novo).

### PR7 — Veredito LLM top-K (Estágio 2) (2026-07-04)

Branch **empilhada** (divergência de processo): PR6 (#55) e PR4.1 (#56) ainda estavam OPEN quando o PR7 começou — `feat/kg-v2-pr7` nasce de `origin/feat/kg-v2-pr6` + merge de `origin/feat/kg-v2-pr4.1` (conflito só no append desta spec, resolvido mantendo as duas seções). Ordem de merge: **#55 → #56 → este** (o diff no GitHub colapsa sozinho após os dois entrarem).

**Pronto quando (todos demonstrados ao vivo, Supabase local + corpus real + gpt-4o-mini):**
- ✓ card do radar exibe racional e red flags para o top-K — **8/8 vereditos** anexados aos cards (`racional_afinidade`, `red_flags_elegibilidade[]`, `fit_mecanismo`, `recomendacao`); frontend renderiza badge de prioridade + racional + chips de red flag (`tsc --noEmit` limpo);
- ✓ segunda visita usa cache — **misses=0, lookup 0.04s, zero chamadas novas**;
- ✓ custo por refresh ≤ K chamadas tier 3 — só os misses vão à fila (**8 pares = 8 chamadas, 6.8s**); dedup em duas camadas: `queueing_lock` por workspace na fila + re-check de hash DENTRO da task (defer duplicado nunca paga LLM);
- ✓ sanity eval matching **0.8334 / 3.0 — idêntico ao PR6** (o harness chama `find_matching_editais` sem profile/workspace; o Estágio 2 nem liga). Suíte: **675 passed** (+10 de `test_match_verdict`), 35 skipped; os 3 errors de `test_memory_store_postgres` seguem pré-existentes (env-gated).

**Divergências do plano:**
1. **Escopo: veredito só para `matched_editais` (kind=edital).** As `Oportunidade(investimento)` do PR4.1 ainda não são card do radar (divergência 1 do PR4.1) — o veredito delas entra no PR8 junto com a unificação do card. `oportunidade_id` = **file_key** (`finep__602`); a coluna é TEXT e aceita node-id v2 quando o PR8 precisar.
2. **Invalidação por hash composto, não por evento.** `input_hash` = sha256(subgrafo serializado + campos preenchidos do perfil + paths arredondados a 3 casas + `_PROMPT_VERSION`). Perfil/oportunidade/prompt mudou ⇒ miss natural; linha velha é substituída por upsert (PK no par — sem histórico). Mudar o prompt exige bump de `_PROMPT_VERSION` (invalida o cache inteiro, de propósito).
3. **Reordenação só com cache quente (match-time).** O poll do frontend preenche os cards in-place SEM reordenar (card pulando sob o mouse é UX ruim); a reordenação por recomendação (`alta` > pendente/`media` > `baixa`, sort estável por affinity) acontece na visita seguinte, via cache. Anônimo nunca tem veredito (o cache é por workspace).
4. **Snapshot persistido (entry_kind=radar) fica SEM veredito**; conversa restaurada re-hidrata via `POST /match/verdicts` (cache-only, zero LLM). O fetch NÃO revalida o hash — devolve o veredito mais recente do par (o poll acontece segundos após o defer; perfil editado no meio re-hasheia e re-enfileira no próximo refresh).
5. **A task recomputa serialização+hash no worker** (não confia no hash do momento do defer): se o corpus mudou entre defer e execução, o hash gravado reflete o corpus novo — que é o que o próximo request também verá (cache-hit correto, nunca stale servido como fresco).
6. **Qualidade observada (insumo p/ v2 do prompt):** red flags são fiéis ao subgrafo mas nem sempre personalizadas (ex.: "destinada apenas a entidades com finalidade lucrativa" veio como flag para uma empresa lucrativa); recomendação conservadora por desenho (na dúvida, nível mais baixo) — nenhum `alta` no perfil de teste, 4 `media`/4 `baixa`.
7. **Pendência de deploy:** migration 035 aplicada só no local — aplicar no PG de prod (`supabase db push`) junto com a publicação do canon/hipergrafos (pendência do PR3).
8. **`docs/architecture.md` atualizado** para o estado v2 (data plane com pós-processos build-time, funil de 3 estágios, tools por domínio, task `compute_match_verdicts`) — a auditoria de 2026-07-03 tinha flagrado doc desatualizado; não repetir o erro.

**Toca (código):** `core/services/match_verdict.py` (novo: serializador D10/D12 + `input_hash` + `compute_verdict` + cache + reorder), `supabase/migrations/035_match_verdicts.sql` (nova), `core/tasks.py` (task `compute_match_verdicts`), `backend/routers/explore.py` (attach cache + reorder top-K + defer com `queueing_lock` + `POST /match/verdicts`), `frontend/src/lib/api.ts` (`MatchVerdict` + `fetchMatchVerdicts`), `frontend/src/components/frontdoor/MatchedEditalCard.tsx` (`VerdictBlock`), `frontend/src/app/page.tsx` (poll + re-hidratação no resume), `tests/unit/test_match_verdict.py` (novo, 10 casos), `docs/architecture.md`.

### PR8 — Ficha por oportunidade + unificação D1 do card (2026-07-04)

Branch **empilhada** (mesma pilha do PR7): `feat/kg-v2-pr8` nasce de `feat/kg-v2-pr7` — **NÃO da main**. Divergência da instrução ("branch nova da main"): PR6 (#55) / PR4.1 (#56) / PR7 (#57) ainda estavam OPEN, e o PR8 depende de PR4.1 (Oportunidades de investimento curadas) e PR7 (`VerdictBlock`). Sair da main renderizaria campos vazios. Ordem de merge: **#55 → #56 → #57 → este**.

**Escopo (decisão do usuário 2026-07-04):** incluir a unificação do card no PR8; se estourar o tamanho, PR8.1 explícito e não-opcional antes de fechar a spec. → A unificação de **display+navegação** do card entrou aqui; o **veredito LLM para ofertas de investimento** foi carveado no **PR8.1** (registrado abaixo).

**Pronto quando:**
- ✓ navegar da lista → ficha → página oficial funciona para **FINEP, FAPESP, FAPESC, curados (programa/investimento) e descoberta promovida** — live check HTTP (TestClient sobre `radar.api.app:app`, corpus real): `GET /oportunidades/{id}` → **200 + `official_url`** para `finep:778`, `fapesp:18067`, `fapesc:31-2026`, `programa:centelha`, `investidor:indicator-capital` (kind=investimento — D1); id inexistente → **404**. Descoberta promovida: ausente do corpus atual (só finep/fapesp/fapesc + curados), mas resolve como qualquer edital via file_key `web__…` (o adapter web já expõe proveniência — PR4).
- ✓ sanity eval matching **0.8334 / 3.0 — idêntico ao PR7** (a ficha e o card são read-only; `find_matching_editais` intocado). 40 testes KG/match/eligibility/curadoria verdes; `tsc --noEmit`, ESLint e ruff limpos.

**Divergências do plano:**
1. **Rota `/oportunidades/[id]` (spec), com `/editais/[id]` → redirect.** As duas listas (`/editais`, `/oportunidades`) navegam para a ficha unificada; o **modal** de programa/investidor da `/oportunidades` foi **removido** (agora é ficha). O CTA `chat?edital=` segue apontando editais (via redirect). Ids curados no path têm `:` e espaço → `encodeURIComponent` no push, `useParams` decodifica, `getOportunidadeById` re-encoda p/ o fetch, FastAPI (`{opp_id:path}`) decodifica.
2. **Resolver unificado `get_opportunity` (superset de `get_edital`).** Resolve edital (file_key `source__native`) OU curado. Curado casa por: node-id v2 direto (`op:…`/`ator:…`); **reconstrução do id da lista** `{kind}:{sufixo}` → `op:{sufixo}` (programa) / `ator:{sufixo}` (fundo → sua oferta); fallback por nome. **D1 vivo:** `investidor:{x}` → ficha da **OFERTA** `Oportunidade(kind=investimento)`, com o fundo como Ator relacionado (via `pertence_a`); Conceitos da oferta vêm do `viabiliza` do fundo (travessia oferta→fundo→conceito).
3. **Card v2 estendido no radar.api.** `edital_card(full)` passou a surfacar `kind`/`aperture`/`macro_temas`/`mecanismo[]` (vinham no nó, não iam ao card). `_curated_card` monta o payload de programa/investimento a partir das **propriedades do nó** (url/ticket/estágio/lead-follow — por-nó, PR4.1) + arestas.
4. **Parte B — unificação de display+nav do card (D1).** `MatchedEntityCard` agora **linka para a ficha** (`/oportunidades/{entity_id}`): investidor→oferta de investimento, programa→programa; **ICT não é clicável** (sem `entity_id` — é parceiro sugerido, não oportunidade — fiel a D1). Card de investidor mostra framing "Oportunidade de investimento" + **ticket/estágio**, encanados por `hypergraph_catalog.investment_offers_by_fund()` no `explore.py`. `entity_id` **preservado** (a writing session depende dele — não foi tocado).
5. **PR8.1 (carve explícito, NÃO-opcional — condição da spec):** o **veredito LLM para ofertas de investimento** (Estágio 2 em `kind=investimento`) fica no PR8.1. Motivo: estender o pipeline de veredito exige serializador de **sub-grafo de oferta** (a oferta coabita o arquivo `investidores` multi-item — não dá p/ reusar `serialize_opportunity`, que assume 1 arquivo = 1 edital e `opportunity_node` kind=edital), chaveamento do cache por **node-id**, task `compute_match_verdicts` **dual-shape** (edital × oferta) e testes — ~200 linhas na superfície mais arriscada, sobre um PR já de ~380. A ficha **já busca** veredito quando existir (`verdictKeyFor`); hoje só editais têm. PR8.1 entra antes de fechar a spec.
6. **`VerdictBlock` extraído** p/ componente compartilhado (`frontend/src/components/frontdoor/VerdictBlock.tsx`), reusado no `MatchedEditalCard` e na ficha (era função local do card).
7. **`EditalCard` v1 / `getEditalById` mantidos** — `workspace/[sessionId]` e `ConversationSidebar` só leem `card.title` (funcionam com o payload v2). A ficha usa os **novos** `OportunidadeDetail` v2 / `getOportunidadeById`.
8. **Sem reescrita de `data/knowledge_graph/`** — PR8 é backend read-only + frontend; **sem backup** (diferente de PR1–PR5). Débito de deploy herdado (inalterado): migration 035 + publicação de canon/hipergrafos no PG de prod (pendências PR3/PR7).

**Toca (código):** `core/kg/hypergraph_catalog.py` (`get_opportunity` + `_curated_card` + `_resolve_curated_node` + `investment_offers_by_fund` + `kind/aperture/macro_temas` no `edital_card`), `backend/routers/catalog.py` (`GET /oportunidades/{id}`), `backend/routers/explore.py` (facetas da oferta nos matched_entities), `frontend/src/types/edital.ts` (`OportunidadeDetail`/`EligibilityConstraint`/`TicketRange`), `frontend/src/lib/api.ts` (`getOportunidadeById` + `MatchedEntity.offer`), `frontend/src/app/oportunidades/[id]/page.tsx` (nova ficha), `frontend/src/app/editais/[id]/page.tsx` (→ redirect), `frontend/src/app/{editais,oportunidades}/page.tsx` (rewire nav + remoção do modal), `frontend/src/components/frontdoor/VerdictBlock.tsx` (novo, extraído), `frontend/src/components/frontdoor/{MatchedEditalCard,MatchedEntityCard}.tsx`.

### PR8.1 — Veredito LLM para ofertas de investimento (2026-07-04)

Carve registrado do PR8 (div. 5). Estende o Estágio 2 às `Oportunidade(kind=investimento)`. Mesma branch `feat/kg-v2-pr8` (empilhada no #57).

**Pronto quando:**
- ✓ card de investidor exibe racional + red flags quando o veredito existe — `VerdictBlock` (o mesmo do edital) no `MatchedEntityCard`; poll (`page.tsx`) e re-hidratação no resume incluem os `entity_id` das ofertas.
- ✓ pipeline end-to-end no corpus REAL (cliente LLM fake): `attach_cached_verdicts_entities` → miss `{kind:investimento, oportunidade_id:"investidor:indicator-capital", paths}` → a task re-serializa via `serialize_for_verdict` → **oid = `investidor:indicator-capital`** (idêntico à chave que o card/poll/ficha usam) → `compute_verdict` → upsert na mesma chave. **Cache-key consistente** card ↔ poll ↔ ficha (`verdictKeyFor`) ↔ coluna.
- ✓ sanity eval matching **0.8334 / 3.0 — idêntico ao PR7/PR8** (o veredito não toca o ranking). Suíte: **678 passed** (+3 de `test_match_verdict`), 35 skipped, 3 postgres deselecionados. `tsc`/ESLint/ruff limpos.

**Divergências do plano:**
1. **Serializador reusa `serialize_opportunity` sobre um sub-grafo enxuto**, não uma função nova. `investment_offer_subgraph(graphs, oid)` extrai `{oferta + fundo(`pertence_a`) + Conceitos(`viabiliza` do fundo) + essas arestas}` — um mini-grafo que o serializador existente consome como qualquer subgrafo. O outro fundo do arquivo multi-item NÃO vaza (testado). `serialize_opportunity` ganhou 3 campos de oferta (`estagio_alvo`, `lead_follow`, `ticket_range` formatado).
2. **`serialize_for_verdict(item, graphs)` como dispatcher único** (edital × investimento), usado tanto pela task quanto (implicitamente) pelo cache — a task deixou de inlinar a serialização de edital. Item de investimento = `{kind:"investimento", oportunidade_id:entity_id, paths}`; item de edital segue `{file_key, paths}` (sem `kind`, default). Zero mudança no caminho de edital.
3. **Chave do cache = `entity_id`** (`investidor:x`), NÃO node-id. É a chave que o card (`entity.entity_id`), o poll, a ficha (`verdictKeyFor` → `detail.id`) e a coluna TEXT já compartilham — evita um segundo esquema de id. A migration 035 não muda (coluna já TEXT).
4. **`attach_cached_verdicts_entities` é irmão do de editais** (não uma generalização) — mantém o caminho de edital intocado. Só `kind=investidor`; programa/ICT ficam `verdict=None` (fora do escopo — extensão trivial se necessário: programa já é `Oportunidade`, só faltaria resolver o nó).
5. **Um defer só** no `explore.py`: os misses de editais e de ofertas entram na MESMA fila (`compute_match_verdicts`) — o `queueing_lock` por workspace segue valendo, e o dispatcher separa os dois na task.
6. **Escopo estrito a investimento** (fiel ao carve): programa/ICT sem veredito. A ficha e o card já buscam veredito genericamente — quando programa entrar, é só incluir no `attach`/poll.

**Toca (código):** `core/services/match_verdict.py` (`_ticket_label` + facetas de oferta na serialização + `investment_offer_subgraph` + `serialize_for_verdict` + `attach_cached_verdicts_entities`), `core/kg/hypergraph_catalog.py` (`investment_offer` público), `core/tasks.py` (task via `serialize_for_verdict`), `backend/routers/explore.py` (attach+enqueue das ofertas, defer combinado), `frontend/src/lib/api.ts` (`MatchedEntity.verdict`), `frontend/src/components/frontdoor/MatchedEntityCard.tsx` (`VerdictBlock`), `frontend/src/app/page.tsx` (poll + apply + resume por `entity_id`), `tests/unit/test_match_verdict.py` (+3 casos de investimento).

**Deploy:** nenhuma migration nova (035 já cobre); pendências herdadas inalteradas (035 + canon/hipergrafos no PG de prod).
