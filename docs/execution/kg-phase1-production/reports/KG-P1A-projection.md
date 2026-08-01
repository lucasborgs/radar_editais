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
| Commit funcional | `5dd7182bf` (`feat(kg): projeção de produção da Fase 1 do grafo (KG-P1A)`) |
| Commit documental | este commit (tip da branch — contém migration/relatório/documento de autoridade) |

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
| `tests/unit/test_phase1_projection.py` | testes essenciais (19 casos, hermético) |
| `pyproject.toml` | extra opcional `[graph]` (`networkx>=3.2`) |
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

`tests/unit/test_phase1_projection.py` — 19 casos cobrindo os 10 riscos:

1. **reconstrução determinística** — `_build_rows` idêntico entre execuções;
2. **idempotência** — `source_hash` estável/conteúdo-sensível + skip quando corrente já reflete o gold;
3. **IDs estáveis** — `<kind>:<native_id>`, `<family>:<value>` determinísticos;
4. **separação fatos × similaridade × heurística** — `origin` + `properties.derived` por aresta;
5. **última saudável preservada após falha** — swap nunca roda em falha parcial + `failed` registrado;
6. **incompleta invisível aos leitores** — leitores filtram `is_current = true`; sem corrente → vazio;
7. **traversal limitado/sem ciclos** — ciclo `similar_a` reverso não repete; `find_paths`/`reachable_within`;
8. **ausência de LLM/rede** — scan AST: sem imports de llm/network/spike;
9. **migration + RLS** — schema, CHECK de `origin`, índice parcial, RLS `authenticated` (padrão 036);
10. **equivalência com fixture da Fase 1 da spike** — hub, cosseno 0.9759, Jaccard 1/3, estruturais, TRL overlap.

Execução:

```bash
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit/test_phase1_projection.py   # 19 passed
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit                          # 2121 passed, 2 skipped, 0 falhas
/Users/lucasborges/radar_editais/.venv/bin/ruff check src/radar/core/kg/phase1/ tests/unit/test_phase1_projection.py        # limpo
git diff --check                                                                                                            # limpo
```

Regressões diretamente relacionadas (kg/gold/environment): `test_environment_contract`,
`test_kg_store`, `test_gold_mappers`, `test_gold_constraints_parser` — todas verdes.

## Limitações

- **Testes sem Postgres real:** a suíte é hermética (mocks/`monkeypatch`); o
  SQL da transação (`::vector`, `::jsonb`, swap, `returning id`) não foi
  executado contra um Postgres real nesta task. Recomenda-se validar o build
  em Supabase local (migration up + `python -m radar.core.kg.phase1.ingest`)
  antes de consumir a projeção.
- **Comunidades exigem networkx** (extra `[graph]`); sem ele o build degrada com
  `counts.communities = 0` (deliberado — enriquecimento, não contrato).
- **Sem endpoint HTTP / sem integração** — a projeção está pronta, mas nenhum
  consumidor (Explorar) foi conectado nesta task.
- **`source_hash` não é reversível** e não identifica o commit de origem do
  gold (só o conteúdo) — suficiente para idempotência, não para auditoria fina.

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
