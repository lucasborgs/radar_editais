# KG-P1B-2 — Lifecycle de produção da Fase 1 (refresh pós-gold + diagnósticos)

> **Task:** KG-P1B-2 — fechamento operacional da Fase 1: auto-refresh da
> projeção `kg_phase1` após o gold (flag `KG_PHASE1_AUTO_REFRESH_ENABLED=false`),
> métricas/diagnóstico estruturais na suíte `explore` (sem gates) e
> documentação de operação/ativação/rollback.
> **Status:** concluída (sem deploy, sem merge, sem push). Auditoria Codex: pendente.

## Identificação

| Campo | Valor |
|---|---|
| Branch | `codex/kg-phase1-production-b2` |
| Base | `d401e91b4` (HEAD aprovado da KG-P1B-1) |
| Worktree | `/private/tmp/radar-editais-kg-phase1b2` |
| Commit funcional | `feat(kg): refresh automático da projeção da Fase 1 após o gold (KG-P1B-2)` |
| Commit documental | `docs(kg): relatório KG-P1B-2 e documentação do lifecycle da Fase 1` |

## Escopo

Depois do commit do gold (no `run_daily_etl` e na promoção de edital), a
projeção da Fase 1 é reconstruída pelo **produtor real**
`radar.core.kg.phase1.ingest.build()` — best-effort, idempotente por
`source_hash`, zero LLM e com retorno/log **sempre sanitizados**. O hook do
harness de eval `explore` ganha sinais estruturais aditivos (uso das graph
tools, taxa de fallback e latência) sem criar gates nem thresholds. Sem
ativação de flags, sem deploy, sem rede/credenciais e sem Fase 2.

## Arquitetura implementada

```
gold.ingest_all() comita (run_daily_etl / ingest_promoted_edital)
  └─ lifecycle.refresh_after_gold(trigger="daily_etl" | "promoted_edital")
       └─ KG_PHASE1_AUTO_REFRESH_ENABLED=true?
            └─ ingest.build(skip_unchanged=True)   # produtor REAL, zero LLM
                 ├─ idempotente: source_hash igual → mantém geração corrente
                 └─ falha → rollback; ledger 'failed'; outcome 'failed'

eval/explore (modo conectado + KG_PHASE1_EXPLORE_ENABLED=true)
  ├─ output["phase1"]            — sinal estrutural aditivo (tools_called)
  ├─ graph_tool_usage            — por caso: usou graph tools?
  ├─ graph_fallback_rate         — run_evaluator: fallbacks/calls na rodada
  └─ graph_latency_ms            — run_evaluator: média ms das graph tools
```

### `lifecycle.py` (novo)

- `auto_refresh_enabled()` — única fonte da flag `KG_PHASE1_AUTO_REFRESH_ENABLED`
  (default OFF). Off = **no-op**: o refresh retorna `disabled` sem abrir conexão
  e sem tocar o banco.
- `refresh_after_gold(*, trigger) -> dict` — contrato fechado de outcomes:

  | outcome | quando | payload |
  |---|---|---|
  | `disabled` | flag off | `{trigger, outcome}` |
  | `built` | nova geração comitada | `+ duration_ms, generation, counts` |
  | `skipped` | gold inalterado (source_hash igual) | `+ duration_ms, generation` |
  | `failed` | exceção do produtor | `+ duration_ms, error:{category, type}` |

- **Sanitização rígida**: retorno/log contêm apenas `trigger`, `outcome`,
  `duration_ms`, `generation`/contagens (ints) e `category`/`type` do erro.
  `source_hash`, conteúdo, DSN, URL, SQL, perfil e a **mensagem da exceção**
  ficam de fora — `str(exc)` não é usado em lugar nenhum (verificável por AST).
- **Best-effort**: o refresh **nunca levanta**; falha → `failed` (o produtor já
  registra a geração `failed` no ledger). O ETL/promoção nunca quebram por isto.

### Integração em `tasks.py`

- `_run_daily_etl`: só após `gold_ok` (o `ingest_all` comitou) e só quando não
  houve erro no bloco gold; rodada em `asyncio.to_thread` (protege o loop) e
  envolta em try/except adicional com `safe_error` (defensivo — o contrato já
  não levanta). O resumo ganha `phase1_refresh` (dict ou `None`) e o contador
  `kg_phase1_refresh` (1 se `built|skipped`, senão 0) para o cron ledger.
  Falha do refresh **não** entra em `step_errors` (best-effort) e não muda
  `status`/`last_step`.
- `ingest_promoted_edital_task`: mesmo padrão após
  `gold.ingest_all(sources=["edital"])`, com `trigger="promoted_edital"`;
  falha não falha a promoção (`mark_by_edital` segue marcando `ready`).

### Observabilidade das graph tools (`tools.py`)

- Acumulador em processo, thread-safe: por tool → `calls`, `fallbacks`,
  `duration_ms` — registrado no `_observe` existente (só estrutura, nunca
  conteúdo). `reset_run_stats()` é chamado no `load_data()` da suíte `explore`
  para cada run; `run_stats()` alimenta os run_evaluators.

### Diagnósticos do eval `explore` (`eval/explore.py`)

- `eval_graph_tool_usage` — por caso; `None` no modo hermético (sem `answer`)
  ou com o grafo desligado; `1.0` se alguma das três graph tools foi chamada.
- `eval_graph_fallback_rate` / `eval_graph_latency_ms` — run_evaluators
  agregados (calls/fallbacks e média de ms na rodada); `None` quando não houve
  calls. Sem gate/threshold (suíte continua `diagnostic`).
- `output["phase1"] = {enabled, tools_called}` — sinal aditivo do modo
  conectado; nunca conteúdo.
- `eval_tool_contract` estendido: as graph tools entram no conjunto aceitável
  das rotas de fato (são aditivas e read-only) — sem isso, um caso conectado
  com grafo ligado reprovaria indevidamente.
- `answer_contract` **preservado** intocado (as tools são read-only/aditivas).
- `manifest_env` agora inclui `KG_PHASE1_EXPLORE_ENABLED` e
  `KG_PHASE1_AUTO_REFRESH_ENABLED`; `version` → `3`.

## Decisões e não-efeitos

- **Sem ativação**: nenhuma flag ligada; `KG_PHASE1_AUTO_REFRESH_ENABLED` só
  documentada (`.env.example`, `capability-lifecycle.md`). Ligá-la não depende
  de `KG_PHASE1_EXPLORE_ENABLED` (são independentes).
- **Sem network/credenciais**: nada sourcea `.env`, não toca Supabase remoto,
  LLM, Langfuse nem rede durante testes (tudo hermético via monkeypatch).
- **Impacto mínimo**: Match/RAG/Writing/frontend/API e a migration 048
  intocados; sem nova migration/dependência; sem Fase 2.
- **Rollback**: desligar `KG_PHASE1_AUTO_REFRESH_ENABLED` (ou desinstalar o
  commit) restaura o comportamento exato do estado anterior — o refresh é um
  passo aditivo best-effort depois do gold, nunca antes do commit dele.

## Validação

> Nota sobre nomes do prompt: `tests/unit/test_eval_explore.py` e
> `tests/unit/test_tasks.py` **não existem** neste repo — os equivalentes reais
> são `test_explore_golden_cases.py`/`test_eval_harness.py` (eval) e
> `test_etl_gold_pipeline.py`/`test_promote_gold_ingest.py` (tasks). A cobertura
> nova ficou em `test_phase1_lifecycle.py`.

```bash
# Suíte nova (lifecycle + diagnósticos do eval) — 17 testes:
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/python -m pytest -q \
  tests/unit/test_phase1_lifecycle.py
# → 17 passed

# Gate do prompt (suítes existentes equivalentes às 5 citadas):
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/python -m pytest -q \
  tests/unit/test_kg_phase1_explore_tools.py tests/unit/test_phase1_projection.py \
  tests/unit/test_explore_agent.py tests/unit/test_explore_golden_cases.py \
  tests/unit/test_eval_harness.py tests/unit/test_etl_gold_pipeline.py \
  tests/unit/test_promote_gold_ingest.py
# → 138 passed, 2 skipped

# Regressão unitária completa (deselect do teste m0 quebrado PRÉ-EXISTENTE,
# comprovado falhando na base d401e91b4 com as mudanças stasheadas):
ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/python -m pytest tests/unit -q \
  --deselect "tests/unit/test_m0_state_continuity.py::test_explore_thread_seeds_only_empty_checkpoint"
# → 2192 passed, 2 skipped, 2 deselected

# Lint e diff hygiene:
/Users/lucasborges/radar_editais/.venv/bin/ruff check src/radar/core/kg/phase1 \
  src/radar/core/eval/explore.py src/radar/core/tasks.py \
  tests/unit/test_phase1_lifecycle.py tests/unit/test_etl_gold_pipeline.py \
  tests/unit/test_promote_gold_ingest.py
# → All checks passed!
git diff --check d401e91b4..HEAD
# → silencioso
```

## Não-alterados (garantia de impacto mínimo)

- **Match/RAG/Writing/memória/frontend/API**: intocados.
- **Migration 048 e o build `kg_phase1`**: o produtor é o mesmo (`ingest.build`);
  esta task só o chama na janela pós-gold.
- **Explorar**: continua off por padrão (`KG_PHASE1_EXPLORE_ENABLED=false`).
- Sem deploy, sem merge, sem push; sem cron/worker novo; sem flags ligadas.

## Auditoria Codex

**Pendente.** Revalidação independente: 2192 testes unitários passaram (menos o
teste m0 pré-existente, comprovado quebrado na base), Ruff e `git diff --check`
limpos. A task termina aqui e não inicia a KG-P1B-3.
