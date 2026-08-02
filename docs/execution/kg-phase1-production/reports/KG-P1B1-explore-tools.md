# KG-P1B-1 — Integração read-only da Fase 1 ao Explorar (graph tools)

> **Task:** KG-P1B-1 — integrar a Fase 1 (grafo `kg_phase1`) ao ExploreAgent,
> read-only, atrás da flag `KG_PHASE1_EXPLORE_ENABLED=false`.
> **Status:** concluída (sem deploy, sem merge, sem push). Auditoria Codex: aprovada em 2026-08-02.

## Identificação

| Campo | Valor |
|---|---|
| Branch | `codex/kg-phase1-production-b1` |
| Base | `32a25af5a` (HEAD aprovado da KG-P1A) |
| Worktree | `/private/tmp/radar-editais-kg-phase1b1` |
| Commit funcional | `feat(kg): tools read-only do grafo da Fase 1 no Explorar (KG-P1B-1)` |
| Commit funcional corretivo | `fix(kg): corrige os quatro achados da auditoria (KG-P1B-1)` |
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
  `MAX_NODES=60`, `MAX_EDGES=80`, `MAX_PATHS=3`, `MAX_PAYLOAD_BYTES=12_000`.
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

## Correção da auditoria KG-P1B-1 (4 achados)

### Achado 1 — Snapshot no PostgreSQL real (`store.py`)

**Problema:** `set local statement_timeout = %s` é erro de sintaxe no Postgres
(`syntax error at or near "$1"`) — `SET LOCAL` não aceita parâmetro.

**Correção:** a linha foi trocada por
`select set_config('statement_timeout', %s, true)` com valor NUMÉRICO gerado
por nós (nunca conteúdo não confiável) e escopo só da transação corrente
(`is_local = true` — equivalente ao `SET LOCAL`). Preservado:
`connect_timeout`, `statement_timeout`, UMA conexão, UMA transação, UMA única
geração saudável e nenhuma mistura entre gerações.

**Prova real:** novo teste de integração
`test_load_snapshot_real_pg_no_syntax_error` constrói uma geração local,
chama `store.load_snapshot()` SEM injetar conexão e confirma que a geração e
TODOS os componentes (nós, quality nodes, arestas, comunidades) carregam e que
o timeout não causa erro de sintaxe. Sem mock — Postgres real via `supabase
start`. Resultado: 8 passed na suíte de integração.

### Achado 2 — Todos os atores alcançáveis (`tools.py` + `traverse.py`)

**Problema:** `actors[:MAX_ACTOR_SEARCH]` descartava silenciosamente ICTs/
agências fora dos dez primeiros em ordem alfabética.

**Correção:** o corte foi REMOVIDO. Nova função `find_paths_to_goals`
(`traverse.py`): UMA única BFS não-direcionada (limitada a `max_depth` níveis,
cycle-safe, com `min_weight`) calcula o caminho MAIS CURTO para TODOS os atores
alcançáveis; depois:

- considera TODOS os nós com `kind in {"ict", "agencia"}` alcançáveis (sem
  ordenar e cortar por alfabeto);
- prefere MENOR distância (primeira visita do BFS = caminho mínimo em saltos);
- desempate determinístico por id do alvo;
- `limit` limita SOMENTE a quantidade de caminhos devolvidos, não o universo
  considerado;
- preserva `MIN_WEIGHT`, ciclos seguros e limites de profundidade.

Sem busca combinatória: uma única BFS obtém os caminhos necessários. Teste
obrigatório (`test_actor_after_tenth_alphabetical_is_found`): 11 atores, o
único alcançável DEPOIS da décima posição alfabética → aparece em
`paths_to_actors`.

### Achado 3 — Teto REAL de payload em bytes UTF-8 (`tools.py`)

**Problema:** `_trim_payload` media CARACTERES (`len(dump(...))`) e não limitava
`members_by_kind`, nomes, ids/candidatos, `center`, `entity`, comunidades e
mensagens/notas.

**Correção:** `_trim_payload` reescrita com contrato garantido por construção:

```python
len(serialized.encode("utf-8")) <= MAX_PAYLOAD_BYTES
```

- `_utf8()` mede BYTES UTF-8 (não caracteres);
- `_clip_utf8()` corta string em bytes SEM partir caractere multibyte
  (`decode(errors="ignore")` → JSON sempre válido);
- encolhimento GULOSO e DETERMINÍSTICO em cópia profunda: sempre o alvo de
  MAIOR economia de bytes (lista descarta a cauda; dict descarta a última
  chave — preservando center/entity/community_id primeiro; strings cortam pela
  metade), desempate pelo caminho lexicográfico;
- o teto é re-verificado pela serialização real a cada passo;
- corte sinalizado com `"truncated": true`;
- se nem o envelope mínimo couber, devolve envelope categórico mínimo
  (`{"truncated": true}`);
- garantia final no wrapper `_run`: mesmo payloads categóricos
  (not_found/ambiguous/available_sample) passam por `_trim_payload`.

Testes adversariais obrigatórios (todos com `json.loads(output)` e
`len(output.encode("utf-8")) <= MAX_PAYLOAD_BYTES`):
- `test_payload_cap_multibyte_long_names` (nomes multibyte de 60k chars);
- `test_payload_cap_community_many_kinds` (comunidade com 60 members e nomes
  longos);
- `test_payload_cap_long_ids_and_candidates` (ids/candidatos de 9k chars);
- `test_payload_cap_reason_long_entity` (entidade/centro de 50k chars);
- `test_payload_cap_all_three_tools_adversarial` (as três tools).

### Achado 4 — Relações internas da comunidade (`tools.py`)

**Problema:** `edge_types` usava `source_id in member_set` — incluía arestas de
membro para NÓ EXTERNO (ex.: `tem_setor` → qualidade fora da comunidade).

**Correção:** `edge_types` é calculado EXCLUSIVAMENTE a partir de `internal`
(arestas com AMBOS extremos na comunidade).

Teste (`test_community_edge_types_only_internal`): fixture com aresta entre
dois membros (`potencial_parceria`) e arestas de membro para nó externo
(`tem_setor`); confirma que `n_internal_edges == 1`, `edge_types` NÃO inclui
`tem_setor` e as características compartilhadas permanecem corretas.

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
# Suíte nova (com os testes da correção da auditoria) — 41 testes:
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_kg_phase1_explore_tools.py
# → 41 passed

# Gate do comando da auditoria (suíte nova + projeção):
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -q \
  tests/unit/test_kg_phase1_explore_tools.py tests/unit/test_phase1_projection.py
# → 69 passed

# Regressões do Explorar + registry:
ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -q \
  tests/unit/test_explore_agent.py tests/unit/test_agent_tools_registry.py
# → 29 passed

# Integração REAL (Supabase local + sentinela validada) — inclui a prova do
# snapshot sem erro de sintaxe:
INTEGRATION_TARGET=local \
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
ENVIRONMENT=test PYTHONPATH=src \
/Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/integration/test_kg_phase1_projection.py
# → 8 passed

# Lint e diff hygiene:
.venv/bin/ruff check src/radar/core/kg/phase1 \
  src/radar/core/services/explore_agent.py \
  tests/unit/test_kg_phase1_explore_tools.py \
  tests/integration/test_kg_phase1_projection.py
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

**Aprovada em 2026-08-02.** Revalidação independente: 95 testes unitários e de
regressão passaram; Ruff e `git diff --check` ficaram limpos; os 8 testes de
integração passaram contra o PostgreSQL/Supabase local, incluindo
`load_snapshot()` com conexão própria. Os quatro achados foram encerrados:
timeout compatível com PostgreSQL real, busca sem corte do universo de atores,
teto real de 12.000 bytes UTF-8 e tipos de aresta restritos à comunidade.

Sem merge, sem push e sem deploy; a task termina aqui e não inicia a KG-P1B-2.
