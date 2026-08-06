# Spike — KG estrutura-consciente (topologia preservada)

> **Status:** SPEC aprovada em 2026-07-31 (discussão de design registrada na
> sessão). Este documento é a autoridade do spike; o restante do sistema não é
> tocado.

## 1. Objetivo

Validar, em isolamento, um property graph de duas camadas (núcleo tipado +
cauda aberta) onde a **topologia** é o ativo (adjacência, tipo, peso, direção) e o
**vocabulário de predicados** é a governança. A travessia estrutura-consciente
deve sobreviver à viagem ao espaço de tokens (textualização que preserva o
grafo, não bullets planos).

Princípios herdados da discussão:
- **Texto 1:** o valor do KG é a topologia; textualizar para prosa achatada é
  pagar o custo do KG e receber o payoff de document store.
- **Texto 2:** terminologia é o risco; definir o que "KG/reasoning/ontology"
  significa para nós antes de construir.
- **Categorias de Aristóteles** = seed do vocabulário de predicados + guia do
  prompt do extrator (Fase 2). NUNCA CHECK/DDL.
- **Reconciliação Ideia 1 + Ideia 2:** schema vira vocabulário (novo predicado
  por INSERT, sem DDL); a promoção ao núcleo é por evidência (gate ≥3, padrão
  schema.md §5.9). A estrutura-consciente percorre só `core=true`.

## 2. Decisões de escopo

| Pergunta | Decisão |
|---|---|
| Consumidor inicial | Explore apenas |
| Fonte de dados | Produção (Supabase remoto), leitura-only de `public.entities` + `entity_relationships` |
| Onde o spike escreve | Schema `kg_spike` no mesmo Postgres (DDL idempotente auto-criada, droppável) |
| Fase 1 (extração) | Determinística, zero LLM (topologia derivada do que já existe) |
| Fase 2 (extração) | **LLM** de relações semânticas não-deriváveis, prompt guiado pelas Categorias |
| `similar_a` | **Entra na Fase 1** (cosseno dos embeddings existentes, threshold ~0.75) |
| Perfil-empresa | Design B (efêmero, em memória) — Design A fora do escopo |
| Escrita como consumidor | Fora do escopo (camada `graph_store` fica pronta, sem tool na escrita) |
| Dependência nova | `networkx` (optional dep `[spike]`) |

## 3. Branch

```
git checkout -b spike/kg-structure-aware   # a partir de main
```

Nada de migrations no `public`; nenhum router/tool/task existente alterado.

## 4. Definições de domínio (a terminologia pina aqui)

- **Knowledge Graph** = property graph com núcleo tipado de substâncias +
  nós de qualidade + vocabulário controlado de predicados + arestas com
  propriedades. Query por travessia estrutural (BFS) + similaridade semântica
  (pgvector). Explicitamente NÃO é OWL/RDFS, Datalog/SPARQL, SHACL ou
  probabilistic reasoning.
- **Reasoning** = avaliação determinística de constraints (eligibility) +
  projeção temporal + interpretação LLM sobre subgrafos limitados.
- **Predicado** = o "P" da tripla (S, P, O); o valor do campo `type` da aresta.
- **Núcleo tipado** = substâncias + nós de qualidade + predicados `core=true`.
- **Cauda aberta** = predicados `core=false`, coexistindo sem poluir a topologia.

## 5. Topologia / Ontologia

### Substâncias (nós raiz)

| Entidade | Fonte atual | Contagem |
|---|---|---|
| `edital` | `entities(kind=edital)` | 115 |
| `ict` | `entities(kind=ict)` | 90 |
| `investidor` | `entities(kind=investidor)` | 17 |
| `programa` | `entities(kind=programa)` | 10 |
| `agencia` | `entities(kind=agencia)` | 10 |
| `empresa` | perfil do workspace | efêmera (Design B) |

### Nós de qualidade (acidentes viram nós)

| Família | Valores | Coluna atual |
|---|---|---|
| `setor` | 16 (taxonomia fechada) | `entities.setores` |
| `tecnologia` | folksonomia | `entities.tecnologias_tags` |
| `estagio` | pre-seed/seed/serie-a/growth | `metadata.estagio_alvo` |
| `uf` | 27 | `entities.uf` |
| `mecanismo` | 5 | `entities.mecanismo` |
| `faixa_trl` | 3 | `trl_range` |

### Relações (seed aristotélico → predicado → fonte)

| Categoria | Predicado | Fonte atual | `core` |
|---|---|---|---|
| Relação | `operado_por` | `entity_relationships` (94) | true |
| Relação | `subordinado_a` | (4) | true |
| Relação | `credenciada_por` | (90) | true |
| Relação | `exige_parceria_com` | constraint `parceria` | true |
| Relação | `similar_a` | cosseno embeddings (Fase 1) | true |
| Qualidade | `tem_setor` | `setores` | true |
| Qualidade | `tem_tecnologia` | `tecnologias_tags` | true |
| Qualidade | `usa_mecanismo` | `mecanismo` | true |
| Posição | `busca_estagio` | `estagio_alvo` | true |
| Posição | `atua_em` | perfil efêmero | true |
| Lugar | `tem_uf` | `uf` | true |
| Quantidade | `tem_trl_faixa` | `trl_range` | true |
| Ação/Paixão | `potencial_parceria` | tecnologia compartilhada (Fase 1) + dedução (Fase 2) | false |

## 6. Módulo `src/radar/core/kg/spike/`

```
spike/
├── __init__.py
├── SPEC.md          # este documento
├── graph_store.py   # DDL idempotente (CREATE SCHEMA IF NOT EXISTS kg_spike) + acesso SQL
├── ingest.py        # Fase 1: populador determinístico (zero LLM, reusa embeddings existentes)
├── extractor.py     # Fase 2: extração LLM de relações semânticas (prompt guiado pelas Categorias)
├── features.py      # grau, centralidade, comunidades Louvain (networkx)
├── traverse.py      # BFS multi-salto + dedução de caminho
├── serialize.py     # textualização estrutura-consciente (subgrafo JSON)
└── tools.py         # tools graph_explore / graph_reason (flag KG_SPIKE_ENABLED=1)
```

## 7. Schema `kg_spike` (auto-criado, droppável)

| Tabela | Conteúdo |
|---|---|
| `kg_spike.nodes` | Espelho das 242 entidades (id, kind, native_id, name) + embedding |
| `kg_spike.quality_nodes` | Nós de qualidade (setores, tags, estágio, UF, mecanismo, faixa TRL) |
| `kg_spike.edges` | `(source_id, target_id, type, weight, properties)` — type SEM CHECK |
| `kg_spike.communities` | `(community_id, node_id)` — saída do Louvain |
| `kg_spike.predicates` | Vocabulário de predicados com flag `core` (seed aristotélico) |

## 8. Fase 1 — Ingest determinístico (zero LLM)

1. Copia as 242 entidades → `nodes`.
2. Materializa nós de qualidade das colunas existentes (`setores`,
   `tecnologias_tags`, `estagio_alvo`, `uf`, `mecanismo`, `trl_range`).
3. Cria arestas:
   - `tem_setor`, `tem_tecnologia`, `busca_estagio`, `tem_uf`, `usa_mecanismo`,
     `tem_trl_faixa` (entidade → nó de qualidade);
   - copia as 188 estruturais (`operado_por`, `subordinado_a`, `credenciada_por`);
   - **`similar_a`** por cosseno entre embeddings existentes (threshold ~0.75),
     `weight` no `properties`;
   - **`potencial_parceria`** edital↔ICT por TECNOLOGIA compartilhada (opção 1):
     pares que compartilham ≥1 `tem_tecnologia` ganham aresta direta com peso =
     Jaccard dos conjuntos de tecnologia (`source='fase1_tech_bridge'`). Só
     conecta quando há sobreposição REAL — nenhuma indicação temática solta vira
     aresta (postura "resposta honesta").
4. Roda `features.py`: grau, centralidade, Louvain → `communities`.

**Hub `setor:multissetorial` (opção 4):** arestas `tem_setor` → `multissetorial`
recebem `weight=0.1` + `properties={"hub": true}`. O nó existe na topologia, mas
as tools de travessia passam `min_weight=0.5` — o hub não expande vizinhança nem
polui caminhos de dedução. ICTs aparecem só por arestas precisas
(`potencial_parceria`, `credenciada_por`, setores reais). Consequência
deliberada: edital sem tecnologia/setor compartilhado com ICTs responde
honestamente "nenhuma ICT estruturalmente conectada".

Resultado esperado: ~550 nós, ~2.400+ arestas, sem re-extração.

## 9. Fase 2 — Extração LLM de relações semânticas

Objetivo: extrair relações **não deriváveis** das colunas existentes (ex.:
`potencial_parceria` edital↔ICT, `investe_em` investidor↔setor, exigências
textuais não estruturadas), guiada pelas Categorias de Aristóteles.

Contrato:
- **Entrada:** texto já extraído (silver `structured_docs/*.jsonl` +
  `description`/`requisitos_texto` do gold) — NUNCA re-lê a fonte crua.
- **Prompt (guia das Categorias):** "procure relações entre agentes, qualidades
  restritivas, quantidades valoradas, posições de estágio, estados, tempos".
  Cada predicado novo emite tripla `(subject_ref, predicate, object_ref|literal)`.
- **Idempotência:** cache por `source_hash` (padrão §11.4) — só reprocessa
  entidade cujo texto mudou; falha por-entidade não derruba o batch.
- **Gate de evidência:** relação nova só promove a `core=true` com **≥3
  evidências independentes** (regra schema.md §5.9); até lá fica `core=false`.
- **Modelo:** tier barato por default (`OPENAI_MODEL`/`LLM_BACKEND`), mesmo
  padrão do tagger do gold.

## 10. Serialização estrutura-consciente (`serialize.py`)

Subgrafo em JSON preservando adjacência, tipo, direção e peso:

```json
{
  "center": {"id": "finep:589", "kind": "edital", "name": "Chamada X"},
  "nodes": [
    {"id": "setor:agro", "kind": "setor", "name": "Agro"},
    {"id": "finep", "kind": "agencia", "name": "FINEP"},
    {"id": "embrapii:unit-42", "kind": "ict", "name": "CEIA-UFG"}
  ],
  "edges": [
    {"source": "finep:589", "target": "setor:agro", "type": "tem_setor", "weight": 1},
    {"source": "finep:589", "target": "finep", "type": "operado_por", "weight": 1},
    {"source": "embrapii:unit-42", "target": "finep:589", "type": "potencial_parceria", "weight": 0.82}
  ],
  "communities": ["agro-bioeconomia"],
  "paths_to_profile": [["empresa", "atua_em", "setor:agro", "tem_setor", "finep:589"]]
}
```

## 11. Integração Explore (isolada por flag)

- `tools.py` expõe `graph_explore(entity_ref, depth=1)`,
  `graph_reason(entity_ref, query)` e `graph_community(community_ref)`.
  `graph_community` resolve `com_11`/`comunidade:11`/`11` e devolve membros por
  kind + qualidades compartilhadas (a "cola" do cluster).
- Registro no `explore_tools.py` **somente com `KG_SPIKE_ENABLED=1`**; flag off =
  comportamento idêntico ao atual.
- `paths_to_profile`: perfil efêmero ancora `:empresa → atua_em → setor →
  tem_setor → edital` e devolve o caminho (travessia multi-salto).

## 12. Eval diagnóstico (sem gate)

- `src/radar/core/eval/spike_kg.py` + 1 linha no `registry.py`.
- Golden: casos de raciocínio topológico ("quais ICTs credenciadas pela
  EMBRAPII cobrem temas de editais de agro operados pela FINEP?").
- Evaluators: `tool_contract` (exige `graph_explore`/`graph_reason`) +
  `answer_contract` (juiz semântico).
- Rodagem: `python -m radar.core.eval run spike_kg` — **diagnóstica**, sem
  threshold (postura de `provenance`/`e2e_health`).

## 13. Validação

```bash
pip install -e ".[spike]"            # networkx
pytest tests/unit/test_kg_spike.py   # BFS, serialização, comunidades (funções puras)
ruff check .
python -m radar.core.eval run spike_kg   # diagnóstico real
```

## 14. O que NÃO muda (garantia de impacto mínimo)

| Superfície | Status |
|---|---|
| `public.entities` / `entity_relationships` / `match_chunks` / `company_chunks` | Intocadas |
| `match_v3` | Intocada |
| `writing_session` / `edital_chunks` (RAG híbrido) | Intocada |
| Descoberta (staging → promoção) | Intocada |
| Tools existentes de explore | Intocadas (nova é aditiva, flag-gated) |
| Cadeia de migrations | Intocada (`kg_spike` é schema separado, auto-criado) |

## 15. Fora do escopo (backlog)

- **Escrita como 2º consumidor:** tool `graph.reason` disponível ao WritingAgent
  via a mesma camada `spike/graph_store`.
- **Design A (perfil persistido):** camada de tenant com `company_edges` (RLS
  own) se a travessia do Design B provar valor.

## 16. Anexo — Célula A/B: boost estrutural no match v3

**Hipótese pré-registrada (§2):** vizinhança `similar_a` dos seeds (matches ≥
`MIN_AFFINITY`) liftaria candidatos que o texto sozinho perdeu, sem piorar
fp/hardneg. Se estrutural ≥ baseline → grafo agrega ao match; senão → grafo fica
para exploração (decisão documentada), Fase 2 (§9) continua frente separada.

**Medição** (produção, read-only, `scripts/ab_match_structural.py` → suíte
espelho `matching_structural` no registry; harness de eval bloqueia ambiente de
produção, por isso rodada via script standalone):

| métrica | baseline | boosted | delta |
|---|---|---|---|
| mean_mrr | 0.6190 | 0.6190 | 0 |
| mean_recall_at_10 | 0.3690 | 0.3690 | 0 |
| mean_false_positives_at_8 | 0.0000 | 0.0000 | 0 |
| mean_unjudged_at_8 | 2.5000 | 3.3750 | **+0.8750** |
| mean_hardneg_pass | 0.6667 | 0.6667 | 0 |

**Veredito: sem lift, com custo pequeno no janela de julgamento (unjudged@8
subiu).** Diagnóstico do porquê:

- `similar_a` deriva dos **mesmos embeddings** que dirigem o match de texto →
  sinal redundante. O boost só reordena editais já no top-10 (mutual neighbors),
  nunca traz um golden-relevante novo.
- Golden-relevantes que o texto perde (ex. `finep:739/769/762/782/783`) têm
  afinidade ~0 no match — fator multiplicativo `1+0.05w` sobre ~0 não muda nada.
  Pior: vários estão `NEEDS_REVIEW`/`CLOSED` no as_of do golden (drift de corpus
  desde o pin 2026-07-05) — são filtrados no Stage 0, fora do alcance do boost.
- **Conclusão do spike para o match:** topologia `similar_a` **não** agrega ao
  funil atual. O grafo permanece **exploração-only**; integração ao `match_v3`
  fica no backlog. A Fase 2 (extração LLM de relações não-deriváveis) segue
  independente, pois seu sinal é diferente do cosseno dos embeddings.
- O baseline `mean_recall_at_10=0.369 < piso 0.55` do gate `matching` sinaliza
  **drift do golden vs corpus atual** (vários relevantes morreram após o pin) —
  candidato a re-curadoria antes de confiar no gate.

**Leitura de código (para quem retomar):** `match_boost.structural_factors`
exclui seeds do boost (exclusão que entrou após a 1ª célula revelar que o boost
só inflava matches já fortes); `match_v3.find_matching_opportunities` recebe
`structural_boost=False` por default (produção intacta).
