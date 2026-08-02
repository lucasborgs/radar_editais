# KG-P1B-1 — Integração read-only da Fase 1 ao Explorar (graph tools)

> **Task:** KG-P1B-1 — integrar a Fase 1 (grafo `kg_phase1`) ao ExploreAgent,
> read-only, atrás da flag `KG_PHASE1_EXPLORE_ENABLED=false`.
> **Status:** concluída (sem deploy, sem merge, sem push). Auditoria Codex: pendente.

## Identificação

| Campo | Valor |
|---|---|
| Branch | `codex/kg-phase1-production-b1` |
| Base | `32a25af5a` (HEAD aprovado da KG-P1A) |
| Worktree | `/private/tmp/radar-editais-kg-phase1b1` |
| Commit funcional | `feat(kg): tools read-only do grafo da Fase 1 no Explorar (KG-P1B-1)` |
| Commit documental | `docs(kg): relatório KG-P1B-1 e reconciliação de capability-lifecycle` |

## Escopo

Adiciona três tools **read-only** ao ExploreAgent, gated por
`KG_PHASE1_EXPLORE_ENABLED` (default `off`), alimentadas por um **snapshot
consistente** de UMA geração saudável do grafo `kg_phase1` (migration 048,
produzido pela KG-P1A):

- `graph_explore` — vizinhança estrutural de uma entidade (depth ≤ 2);
- `graph_reason` — caminhos entre o perfil da empresa, entidades e atores
  (max_depth ≤ 4, até 3 caminhos), com `profile` como **closure** (nunca
  argumento da LLM);
- `graph_community` — membros (agrupados por `kind`), características
  compartilhadas e tipos de relação de um cluster existente.

Nenhum consumidor fora do Explorar; nenhum caminho de escrita; flag off
devolve as tools do agente exatamente como antes.

## Arquitetura implementada

```
ExploreAgent (explore_stream / _explore_agent)
  └─ _explore_tools(profile=None)          — ADITIVO quando flag on
       └─ build_graph_tools(profile=None)  — 3 tools @tool (read-only)
            └─ store.load_snapshot()       — UMA geração, UMA conexão, UMA
                                             transação, timeout 2.0s
                 ├─ resolve.resolve_entity() — ordem rígida, nunca sufixo
                 └─ payloads puros (explore/reason/community) + observabilidade
```

### Snapshot consistente (`store.py`)

- `Snapshot` frozen: `generation_id`, `nodes`, `quality_nodes`, `edges`,
  `communities` — nunca mistura gerações durante um swap.
- `load_snapshot()` resolve a ÚNICA geração `is_current = true AND status =
  'healthy'` e lê tudo dela em uma conexão e uma transação.
- `SNAPSHOT_TIMEOUT_SECONDS = 2.0` explícito (`statement_timeout` local +
  `connect_timeout`): Postgres pendurado NÃO bloqueia o Explorar.
- `None` sem geração saudável (estado pré-primeiro-build) — nunca dado parcial.
- Sem cache: cada chamada lê a geração corrente (dados sempre frescos).

### Resolução estrita (`resolve.py`)

Ordem rígida, sem adivinhação:

1. id exato (substância ou nó de qualidade);
2. `native_id` exato e ÚNICO;
3. nome normalizado exato e ÚNICO (NFKD sem acento + lowercase);
4. nó de qualidade por valor exato e ÚNICO.

Ambíguo → `ambiguous` + até `MAX_CANDIDATES=5` ids seguros; sem match →
`not_found`. **Nunca** `endswith`/sufixo solto (o comportamento inseguro da
spike `kg-structure-aware/_resolve` fica fora).

### Tools (`tools.py`)

- `graph_tools_enabled()` — única fonte da flag `KG_PHASE1_EXPLORE_ENABLED`.
- Limites rígidos: depth ≤ 2 (`graph_explore`), max_depth ≤ 4 (`graph_reason`),
  `MAX_NODES=60`, `MAX_EDGES=80`, `MAX_PATHS=3`, `MAX_PAYLOAD_BYTES=12_000`,
  `MAX_ACTOR_SEARCH=10`.
- Hub `setor:multissetorial` (weight 0.1 < `MIN_WEIGHT=0.5`) **não expande**.
- `_profile_edges` converte campos do perfil → predicados do catálogo:
  `uf`→`atua_em`, `estagio`→`busca_estagio`, `trl`→`tem_trl_faixa` (faixas),
  `tipos_financiamento_interesse`→`usa_mecanismo` (via `_MECANISMO_MAP`).
- Nó efêmero `empresa:efemera` **apenas em memória** (closure); nunca persiste.
- Payloads `explore_payload`/`reason_payload`/`community_payload` são puros e
  retornam `ToolOutcome` (outcome, generation_id, métricas, payload).

### Observabilidade

Somente métricas estruturais: tool, `generation_id`, duração, counts
(nós/arestas/caminhos/membros), outcome (`hit|not_found|ambiguous|unavailable|
error`), flag de fallback e categoria canônica de falha (`database_error` /
`dependency_error` / `contract_error` / `unexpected_error`). **Nunca** conteúdo
do grafo, lixo, DSN ou bruto nos traces.

### Fallback e degradação

Sem geração saudável → `unavailable` com mensagem sanitizada apontando para as
tools do catálogo (get_edital, search_entities, get_node_neighborhood). Falha
de conexão/contrato/inesperada → `error` com a mesma orientação. A tool nunca
lança exceção para fora — o agente continua vivo com as tools factuais.

### Instruções do agente

`KG_PHASE1_GRAPH_INSTRUCTION` anexada ao system prompt **apenas** quando a flag
está on (off = byte a byte idêntico, regressão zero). Preferir as graph tools
para RELAÇÕES ESTRUTURAIS, caminhos, atores, comunidades e análise de
estratégia; tools factuais para detalhes/evidências documentais; arestas
derivadas (`similar_a`, `potencial_parceria`) nunca apresentadas como fato.

## Fora do escopo (confirmado)

- Match/`match_v3` e `match_chunks`: intocados.
- Writing/RAG e o índice `edital_chunks`: intocados.
- Memória/checkpointer: intocado.
- Fase 2 (extração LLM) e ferramentas de vocabulário: intocadas.
- Migration 048 e o build `kg_phase1`: intocados (aqui só consumo read-only).
- Frontend/API: nenhum endpoint novo.
- Worker/cron/gold: intocados.
- Sem cache (deliberado nesta task).

## Validação

```bash
# Suíte nova — 34 testes (snapshot, resolução, tools, observabilidade, limites):
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_kg_phase1_explore_tools.py
# → 34 passed

# Regressões do Explorar + Fase 1:
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -q \
  tests/unit/test_phase1_projection.py tests/unit/test_explore_agent.py \
  tests/unit/test_agent_tools_registry.py
# → 57 passed

# Lint e diff hygiene:
.venv/bin/ruff check src/radar/core/kg/phase1/{store,resolve,tools}.py \
  src/radar/core/services/explore_agent.py tests/unit/test_kg_phase1_explore_tools.py
# → All checks passed!
git diff --check 32a25af5a..HEAD
# → silencioso
```

## Não-alterados (garantia de impacto mínimo)

- **Explorar off por padrão**: `KG_PHASE1_EXPLORE_ENABLED=false` → tools e
  system prompt idênticos ao estado anterior.
- **Gold** (`public.entities`/`entity_relationships`/`match_chunks`): intocado.
- **Cadeia de migrations**: intocada (048 já existia da KG-P1A).
- Sem deploy, sem merge, sem push; sem cron/worker; sem flags de produção
  ligadas.

## Auditoria Codex

**Pendente.** Sem merge, sem push e sem deploy; a task termina aqui (não inicia
a KG-P1B-2).
