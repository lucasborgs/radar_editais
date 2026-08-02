# KG-P1A — Projeção de produção da Fase 1 do grafo

> **Task:** KG-P1A — projeção de produção da Fase 1 do grafo (aproveitando a
> spike `kg-structure-aware`).
> **Status:** concluída (sem deploy, sem merge, sem push). Auditoria Codex: pendente.

## Identificação

| Campo | Valor |
|---|---|
| Branch | `codex/kg-phase1-production-a` |
| Base | `eb0a4e46b` (origin/main, `Merge pull request #76`) |
| Worktree | `/private/tmp/radar-editais-kg-phase1a` |
| Commit funcional (v1) | `5dd7182bf` (`feat(kg): projeção de produção da Fase 1 do grafo (KG-P1A)`) |
| Commit funcional corretivo | `12c3f3dbc` (`fix(kg): determinismo total, falha segura por categoria e prova real local`) |
| Commit documental | este commit (tip da branch — contém este relatório) |

## Correção KG-P1A (determinismo, sanitização, dependência, prova local)

### 1. Determinismo real

Garantia: a mesma entrada produz EXATAMENTE os mesmos nós, arestas (na mesma
ordem), comunidades (mesmos IDs) e `source_hash` — independente da ordem do
Postgres, da ordem das listas de entrada, da iteração de `set` e do
`PYTHONHASHSEED`.

Correções mínimas aplicadas em `src/radar/core/kg/phase1/`:

- **leitura ordenada** — `_load_gold` com `order by kind, native_id`
  (`public.entities`) e `order by source_id, target_id, type`
  (`entity_relationships`);
- **ordem canônica total** — `_node_rows`/`_quality_rows` ordenados por `id`;
  arestas ordenadas por **JSON canônico** (`sort_keys=True`, separadores
  `","`/`":"` via `_json`) **antes** do dedup (1ª ocorrência), em `_dedup_edges`;
- **iteração determinística** — `_similarity_edges` ordena as entradas por id e
  usa o id como desempate do score; `_partnership_edges` itera
  `sorted(editais)` × `sorted(icts)` (nunca `set` cru);
- **JSON canônico no hash** — `_source_hash` usa `_json` em toda
  lista/dict (setores, tecnologias, estagio_alvo, constraints, trl_range);
- **comunidades** — IDs `com_<idx>` atribuídos APÓS ordenar as comunidades pelo
  conjunto ORDENADO de membros (`features.detect_communities`); membros de cada
  comunidade ordenados; a ordem incidental devolvida pelo Louvain nunca decide
  o ID;
- **leitores** — `store.load_communities` com `order by community_id, node_id`.

Semântica final: empates NUNCA são resolvidos por nome/ordem incidental — só por
chave canônica determinística.

### 2. Sanitização de falhas (categorias canônicas)

Nem o ledger nem os logs contêm `str(exc)`. Persiste-se apenas
`<categoria>:<TipoSeguro>` em `generations.error`:

| Categoria | Exemplos |
|---|---|
| `database_error` | `psycopg.*` / `psycopg_pool.*` |
| `dependency_error` | `ImportError` / `ModuleNotFoundError` |
| `contract_error` | `ValueError` / `KeyError` / `TypeError` / `DatabaseTargetError` |
| `unexpected_error` | demais |

- Removidos: regex de DSN (`_DSN_RE`), `_sanitize_error`, `logger.exception` e
  qualquer traceback no fallback de `_record_failure`;
- o fallback do registro `failed` loga apenas `categoria` + `tipo` do fallback;
- testes adversariais (exceção com DSN c/ senha, URL, trecho documental, SQL e
  marcador `SEGREDO_BRUTO`) confirmam que NENHUM desses vaza para a persistência
  nem para o `caplog`.

### 3. Dependência `networkx`

- `networkx>=3.2` permanece no extra **funcional** `[graph]`;
- adicionado também ao extra **`[dev]`** — o CI instala `.[dev]` e executa o
  teste real de comunidades (Louvain);
- **não** movido para as dependências principais (prod não carrega grafo pesado);
- confirmado que o worker JÁ recebe `networkx==3.6.1` pelo lock atual
  (`requirements.worker.lock.txt`, via `alphashape`) — **locks não regenerados**;
- degradação explícita testada: sem networkx → `detect_communities()==[]`,
  `node_stats()=={}`, build segue `healthy` com `counts.communities=0`.

### 4. Validação real com Postgres/Supabase local

Exclusivamente local (nenhuma credencial real, nenhum Supabase remoto, nenhuma
rede/LLM/Langfuse). Antes de qualquer escrita: `ENVIRONMENT=test`, banco
`127.0.0.1:54322` (Supabase local), sentinela `public.environment_metadata`
validada por `assert_database_target`.

- `supabase migration up` aplicou **048_kg_phase1.sql** (única pendente);
- `tests/integration/test_kg_phase1_projection.py` (marcado `integration`,
  gated em `INTEGRATION_TARGET=local`) prova com psycopg real os 10 itens:
  migration executável; primeiro build `healthy`+`is_current`; skip sem mudança;
  build forçado com nova geração; exatamente UMA corrente; leitores só veem a
  corrente; falha controlada pós-insert com rollback; saudável anterior
  corrente; ledger `failed` categórico e sanitizado; e `vector(1536)`/JSONB/FK/
  índice parcial/swap funcionando. **7 passed**; teardown deixa o banco limpo.

## Arquivos alterados

| Arquivo | Tipo |
|---|---|
| `supabase/migrations/048_kg_phase1.sql` | nova migration (schema `kg_phase1`) |
| `src/radar/core/kg/phase1/__init__.py` | módulo de produção (fora de `kg/spike/`) |
| `src/radar/core/kg/phase1/store.py` | geração corrente + consultas (nós/qualidade/arestas/comunidades) |
| `src/radar/core/kg/phase1/ingest.py` | build determinístico + troca atômica + CLI |
| `src/radar/core/kg/phase1/traverse.py` | BFS multi-salto + caminhos (puro) |
| `src/radar/core/kg/phase1/features.py` | Louvain/centralidade (networkx opcional) |
| `src/radar/core/kg/phase1/PROJECTION.md` | autoridade do módulo |
| `tests/unit/test_phase1_projection.py` | testes essenciais (28 casos, hermético) |
| `tests/integration/test_kg_phase1_projection.py` | prova real local (Supabase local) — 7 casos, `integration` |
| `pyproject.toml` | extras `[graph]` + `[dev]` (`networkx>=3.2`) |
| `docs/execution/kg-phase1-production/reports/KG-P1A-projection.md` | este relatório |
| `docs/execution/README.md` | índice de iniciativas ativas |

## Modelo de geração e troca atômica

- **`generations`** (ledger + ponteiro): cada build grava UMA nova geração
  (`nodes`/`quality_nodes`/`edges`/`communities` indexados por
  `generation_id`) dentro de **uma única transação**.
- **Swap atômico:** o `update … set is_current = (id = nova) where is_current
  or id = nova` é a **última operação** da transação; o `status='healthy'` +
  `finished_at` + `counts` são gravados no mesmo commit. Leitores resolvem a
  geração via `is_current = true` → **nunca observam geração incompleta**
  (`building`/`failed` nunca é `is_current`).
- **Falha:** rollback → a última geração saudável permanece corrente; um
  registro `failed` (best-effort, transação separada, erro **sanitizado**) fica
  no ledger para observabilidade.
- **Idempotência:** `source_hash` determinístico do subconjunto do gold
  consumido (sem uuids; embeddings incluídos). Mesma hash da corrente → build
  pula (`--no-skip` força).
- **Sem infraestrutura distribuída:** o volume (~242 nós, ~2.4k arestas) torna a
  reconstrução em uma transação trivial.

## Elementos da spike aproveitados

| Elemento | Como |
|---|---|
| Lógica determinística da Fase 1 (SPEC §8) | reproduzida em funções puras de `ingest.py` (`_quality_edges`, `_structural_edges`, `_similarity_edges`, `_partnership_edges`) |
| `traverse.py` (BFS/`find_paths`/`reachable_within`) | reuso direto (puro, sem referência à spike) |
| `features.py` (Louvain `seed=42`, centralidade) | portado com geração e degradação por networkx |
| Hub `setor:multissetorial` (`weight=0.1`, `min_weight=0.5`) | preservado (reproduz as melhores respostas do Explorar) |
| TRL overlap (schema.md §5.8), `similar_a` (cosseno ≥0.75 top-10), `potencial_parceria` (Jaccard) | idênticos à spike (validados por fixture de equivalência) |
| Vocabulário de predicados e nomenclatura `phase1_*` | `edges.origin` com CHECK fechado |

## Deliberadamente excluídos (da spike)

| Item | Motivo |
|---|---|
| `extractor.py` (Fase 2/LLM) | fora do escopo (KG-P1A é determinística) |
| `extraction_candidates` / promoção de predicados | fora do escopo |
| `match_boost.py` + célula A/B | match fora do escopo (ADR-002: sem lift, exploração-only) |
| `tools.py` (`graph_explore`/`graph_reason`/`graph_community`) | integração com o Explorar fora do escopo |
| `serialize.py` | textualização para token space é uso do Explorar (fora do escopo) |
| DDL auto-criado em runtime (`graph_store.init_schema`) | produção usa migration linear (048) |
| `TRUNCATE`-e-rebuild (`graph_store.reset`) | substituído pelo modelo de gerações com troca atômica |
| eval/scripts/golden da spike | experimentação, não produção |
| nomenclatura `fase1_*` | renomeada para `phase1_*` (origens lógicas do escopo) |

## Testes

`tests/unit/test_phase1_projection.py` — **28 casos** cobrindo os 10 riscos
essenciais + os novos contratos da correção (determinismo sob embaralhamento/
reversão e `PYTHONHASHSEED`, categorias de erro, adversariais, degradação sem
networkx):

1. **reconstrução determinística** — `_build_rows` idêntico entre execuções;
2. **idempotência** — `source_hash` estável/conteúdo-sensível + skip quando corrente já reflete o gold;
3. **IDs estáveis** — `<kind>:<native_id>`, `<family>:<value>` determinísticos;
4. **separação fatos × similaridade × heurística** — `origin` + `properties.derived` por aresta;
5. **última saudável preservada após falha** — swap nunca roda em falha parcial + `failed` registrado;
6. **incompleta invisível aos leitores** — leitores filtram `is_current = true`; sem corrente → vazio;
7. **traversal limitado/sem ciclos** — ciclo `similar_a` reverso não repete; `find_paths`/`reachable_within`;
8. **ausência de LLM/rede** — scan AST: sem imports de llm/network/spike;
9. **migration + RLS** — schema, CHECK de `origin`, índice parcial, RLS `authenticated` (padrão 036);
10. **equivalência com fixture da Fase 1 da spike** — hub, cosseno 0.9759, Jaccard 1/3, estruturais, TRL overlap;
11. **determinismo independente da ordem de entrada** — embaralhamento/reversão com igualdade exata;
12. **determinismo sob `PYTHONHASHSEED`** — prova por subprocess (seeds 0/1/2), sem infraestrutura própria;
13. **IDs de comunidade estáveis** — embaralhar arestas não muda `com_<idx>` nem composição;
14. **categorias de erro** — `database_error`/`dependency_error`/`contract_error`/`unexpected_error`;
15. **adversariais** — DSN c/ senha, URL, SQL, trecho documental e `SEGREDO_BRUTO` não vazam p/ ledger nem `caplog`;
16. **degradação sem networkx** — comunidades `[]`, `node_stats {}`, build saudável.

Execução:

```bash
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit/test_phase1_projection.py      # 28 passed
INTEGRATION_TARGET=local DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/integration/test_kg_phase1_projection.py  # 7 passed
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit                          # 2130 passed, 2 skipped, 0 falhas
/Users/lucasborges/radar_editais/.venv/bin/ruff check src/radar/core/kg/phase1 tests/unit/test_phase1_projection.py tests/integration/test_kg_phase1_projection.py  # limpo
git diff --check eb0a4e46b..HEAD                                                                                         # limpo
```

Regressões diretamente relacionadas (kg/gold/environment): `test_environment_contract`,
`test_kg_store`, `test_gold_mappers`, `test_gold_constraints_parser` — todas verdes.

## Limitações

- **Comunidades exigem networkx** (extra `[graph]`/`[dev]`); sem ele o build
  degrada com `counts.communities = 0` (deliberado — enriquecimento, não
  contrato; testado).
- **Validação real local executada nesta correção** (migration 048 aplicada +
  prova psycopg via teste `integration`) — não há mais gap de Postgres real.
  A prova exige `supabase start` ativo; sem ele, o teste é pulado (gated).
- **Sem endpoint HTTP / sem integração** — a projeção está pronta, mas nenhum
  consumidor (Explorar) foi conectado nesta task.
- **`source_hash` não é reversível** e não identifica o commit de origem do
  gold (só o conteúdo) — suficiente para idempotência, não para auditoria fina.
- **Comunidade determinística por construção de topologia**: o Louvain usa
  `seed=42` e arestas canônicas; o ID `com_<idx>` é ordenado pelos membros.
  O fixture validado garante estabilidade sob `PYTHONHASHSEED`/embaralhamento;
  grafos muito maiores (> dezenas de milhares de nós) não foram estressados.

## Não-alterados (garantia de impacto mínimo)

Confirmado explicitamente para a task KG-P1A:

- **Explorar**: intocado (nenhuma tool/flag/router novo; `explore_agent` e
  `explore_tools` não foram tocados);
- **Match/`match_v3`**: intocado (`match_chunks` e o funil não foram alterados);
- **RAG/Writing**: intocado;
- **Memória/checkpointer**: intocado;
- **Fase 2 (extração LLM)**: intocada (fora do escopo);
- **Gold** (`public.entities`/`entity_relationships`/`match_chunks`): intocado;
- **Cadeia de migrations**: apenas ADITIVA (048, schema `kg_phase1` isolado);
- Sem deploy, sem merge, sem push; sem cron/worker; sem flags de produção.

## Auditoria Codex

**Pendente.** Esta task não iniciou a integração com o Explorar, não fez merge e
não fez push.
