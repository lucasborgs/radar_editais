# RT03 — Relatório consolidado

**Spec:** `radar-data-trust-03-source-coverage.md`
**Spec-mãe:** `radar-data-trust.md`
**Data de fechamento:** 2026-07-27
**Branch:** `codex/radar-data-trust-03-t07`
**Base:** `17dcca615` (main)

---

## Resultado de negócio

Descoberta aberta multicanal, observável e adaptável: 7 canais de aquisição
registrados com saúde derivada, atribuição de candidatos no staging, funil
editorial por canal/família, domínios emergentes como candidatos visuais e
endpoint/painel administrativo read-only. O operador agora distingue canais
saudáveis, degradados, falhando e atrasados; zero ambíguo não vira sucesso;
denominador ausente retorna `null`; painel indisponível não bloqueia a fila
editorial.

Nenhum código novo acessa produção/rede/LLM. Nenhum eval, threshold, gate de
recall, scraper automático ou promessa de cobertura exaustiva foi introduzido.

---

## Commits e entregas T01–T06

### T01 — Contrato de canais e famílias

| Commit | Base |
|---|---|
| RT03-T01 (merge na branch) | main, preparatório para T02 |

**Arquivos:** `_coverage.md`, `_discovery.md`, `schema.py`,
`test_source_coverage_registry.py`
**Resultado:** 7 canais em YAML versionado, 4 famílias de query, zero listas
paralelas em Python. `open_search` como canal lógico (não Tavily).

### T02 — Migration e atribuição

| Commit | Base |
|---|---|
| RT03-T02 (merge na branch) | T01 |

**Arquivos:** `043_source_runs.sql`, `source_runs.py`, `test_source_runs.py`
**Resultado:** Tabela `source_runs` com RLS sem policy de usuário final; staging
recebe 4 colunas nullable; `start_run`/`finish_run` idempotente e best-effort.

### T03 — Saúde das fontes dedicadas/Web curada

| Commit | Base |
|---|---|
| RT03-T03 (merge na branch) | T02 |

**Arquivos:** `tasks.py`, `test_source_coverage_etl.py`
**Resultado:** `run_daily_etl` instrumentado com `source_runs` para
finep/fapesp/fapesc/web_curated. Telemetria best-effort; falha nunca vira
`partial`; zero não infere saúde.

### T04 — Instrumentação multicanal da Descoberta

| Commit | Base |
|---|---|
| RT03-T04 (merge na branch) | T02 |

**Arquivos:** `opportunity_discovery.py`, `web_search.py`,
`test_source_coverage_discovery.py`, `test_opportunity_discovery_cache.py`
**Resultado:** `discover_opportunities` instrumentado para
open_search/dou/hub_expansion com atribuição completa, `search_available()`
provider-neutral, 8 correções de auditoria aplicadas.

### T05 — Métricas do funil, lacunas e domínios emergentes

| Commit | Base |
|---|---|
| RT03-T05 (merge na branch) | main (pós-T04) |

**Arquivos:** `source_coverage_metrics.py`, `test_source_coverage_metrics.py`
**Resultado:** Read model determinístico: rendimento, funil editorial, saúde
(precedência exata da spec), lacunas, domínios emergentes (threshold >= 2 em
90 dias). Nenhuma escrita. Sete correções de auditoria aplicadas.

### T06A — API administrativa

| Commits | Base |
|---|---|
| `ee1f1d5fd`, `298400d9d` | T05 |

**Arquivos:** `routers/source_coverage.py`, `app.py`,
`test_source_coverage_api.py`
**Resultado:** `GET /source-coverage` protegido por `AdminUserId`, projeções
explícitas, erro sanitizado (só nome da classe), sem escrita.

### T06B — Painel frontend

| Base |
|---|
| T06A |

**Arquivos:** `api.ts`, `discovered/page.tsx`
**Resultado:** Painel recolhível em `/discovered` com 6 seções compactas.
`setCoverage(null)` antes de carregar token. Falha da API → "Painel indisponível
no momento." sem bloquear a fila. Zero TypeScript/lint novos.

---

## Canais e famílias

### Sete canais registrados

| `source_key` | `mode` | Gated por |
|---|---|---|
| `finep` | `dedicated` | — |
| `fapesp` | `dedicated` | — |
| `fapesc` | `dedicated` | — |
| `web_curated` | `curated_web` | — |
| `open_search` | `open_search` | — |
| `dou` | `official_feed` | `DISCOVERY_DOU_ENABLED` |
| `hub_expansion` | `hub` | `DISCOVERY_HUB_CRAWL_ENABLED` |

### Quatro famílias de busca

| `key` | Finalidade |
|---|---|
| `state_innovation_funding` | Chamadas estaduais e FAPs |
| `corporate_open_innovation` | Desafios e pilotos de empresas/hubs |
| `startup_acceleration` | Aceleração e incubação |
| `international_brazil_access` | Oportunidades internacionais |

---

## Migration e atribuição

- **Migration:** aditiva `043_source_runs.sql`, após `042_provenance_columns.sql`.
  Nenhuma tabela/dado existente alterado.
- **source_runs:** RLS habilitada, sem policy de usuário final (espelha
  `pipeline_errors`/`discovery_promotion_runs`). Worker escreve via service-role;
  API lê via service-role.
- **4 colunas nullable no staging:** `discovery_run_id` (FK `ON DELETE SET NULL`),
  `discovery_channel`, `query_family`, `origin_domain`. Linhas legadas mantêm
  todos os campos `null` — funil as coloca no bucket `__unassigned__`.
- **Sem backfill fictício:** runs e atribuição legadas não são inventadas.

---

## Saúde, funil, lacunas e domínios emergentes

### Saúde

Precedência: `disabled → failing → degraded → stale → healthy → unknown`.

- `healthy`: última run `succeeded` com resultado observável dentro de 1×
  intervalo esperado.
- `unknown`: nunca executado ou sucesso sem resultado observável (zero ambíguo).
- Flags lidas exclusivamente do dicionário `env` injetado; ambiente do processo
  não participa.

### Funil editorial

- Por canal (`discovery_channel`) e por família de query (`query_family`).
- Linhas legadas (campos `null`) alocadas em `__unassigned__`.
- `approval_rate` = `None` quando denominador (`approved + rejected`) é zero.
- `avg_review_hours` = `None` quando sem pares `created_at`/`reviewed_at`.

### Lacunas

5 sinais explícitos sem score: `enabled_no_run`, `ambiguous_run`, `delayed`,
`family_no_denominator`, `pending_queue`. Nenhum sinal afirma ausência de
oportunidades.

### Domínios emergentes

- Apenas `origin_domain` com `status = promoted`, `reviewed_at` nos últimos 90
  dias.
- `candidate_for_dedicated_monitoring = true` quando ≥ 2 aprovações do mesmo
  domínio.
- Sem criação automática de fonte, scraper ou configuração.

---

## Endpoint e painel

### `GET /source-coverage`

- Protegido por `AdminUserId` (fail-closed).
- Read-only: apenas `.select()` com projeções explícitas (7 campos em
  `source_runs`, 6 em `discovered_opportunities`).
- Erro sanitizado: log registra só `type(exc).__name__`; resposta 503 sem DSN,
  query ou traceback.
- Tabela vazia: canais `enabled_by_default=true` → `unknown`; canais gated sem
  flag → `disabled`.
- Resposta inclui `generated_at`, `channels`, `runs`, `channel_funnel`,
  `family_funnel`, `gaps`, `emerging_domains`, `limitations` (5 textos canônicos).

### Painel (`/discovered`)

- Recolhível: "Fontes e canais monitorados pelo Radar — X saudáveis, Y com problema".
- Expandido: badges de saúde, runs, funil por canal/família, lacunas, domínios
  emergentes, limitações.
- `null` exibido como `—`, nunca zero fabricado.
- Falha/403: "Painel indisponível no momento." — sem bloquear promoção/rejeição.
- Nenhum botão para criar fonte, scraper, retry ou promoção automática.

---

## Testes e CI

### Alvo

```bash
ENVIRONMENT=test PYTHONPATH=src pytest -q \
  tests/unit/test_source_coverage_registry.py \
  tests/unit/test_source_runs.py \
  tests/unit/test_source_coverage_discovery.py \
  tests/unit/test_opportunity_discovery_cache.py \
  tests/unit/test_source_coverage_metrics.py \
  tests/unit/test_source_coverage_api.py \
  tests/unit/test_admin_gate.py
```

**Resultado: 214 passed, 0 failed** (1 warning: StarletteDeprecationWarning)

### Completo

```bash
ENVIRONMENT=test PYTHONPATH=src pytest -q
```

**Resultado: 1627 passed, 64 skipped, 0 failed**

```bash
ENVIRONMENT=test PYTHONPATH=src ruff check $(git ls-files '*.py')
```

**Resultado: All checks passed**

```bash
cd frontend && npx tsc --noEmit
```

**Resultado: 0 erros**

```bash
cd frontend && npm run lint
```

**Resultado: 5 warnings preexistentes** (auth.tsx:4, workspace/[sessionId]/page.tsx:1 — baseline não alterado)

```bash
git diff --check
```

**Resultado: sem whitespace errors**

### CI do merge T06

O CI do merge T06 comprovou backend, frontend, build e Supabase local:
https://github.com/lucasborgs/radar_editais/actions/runs/30260170224

---

## Limitações

- Recall absoluto da web é impossível de provar; o painel não alega completude.
- `search_available()` atualmente só reconhece backend Tavily (expansível via
  `WEB_SEARCH_BACKEND`).
- Métricas de cobertura usam proxies observacionais (canais, funil, domínios
  emergentes) — não há denominador de cobertura total.
- Goldens de relevância (spec 00) não servem como denominador de descoberta:
  corpus retrospectivo exige curadoria representativa futura.
- Razões canônicas de `source_runs` são validadas em Python, não via CHECK no
  SQL (escolha deliberada para evitar falsos positivos em dados legados).
- Testes usam mocks/fakes; comportamento real depende de schema Postgres com
  migration 043 aplicada.

---

## Confirmação de ambiente hermético

- **ENVIRONMENT=test** em toda execução.
- **DB fake/mocks:** `source_runs`, `discovered_opportunities` simulados;
  nenhuma conexão com Supabase Cloud ou Postgres local.
- **Sem `.env`:** nenhuma credencial carregada.
- **Sem rede:** Tavily, DOU, LLM, web search, Tavily — todos mockados ou
  desabilitados por `search_available()` falso.
- **Worktree isolado:** `/private/tmp/radar-editais-rt03-t07`.
- **Branch isolada:** `codex/radar-data-trust-03-t07`.
- **Base fixa:** `17dcca615` (main).
- **Sem merge, push, deploy ou migration remota.**

---

## Auditoria Codex: pendente

Esta task (RT03-T07) executa a auditoria de fechamento. Após confirmação
independente dos 15 itens de auditoria estática, do ambiente hermético e
da não regressão da suíte comparativa, o campo abaixo deve ser preenchido.

| Item | Status |
|---|---|
| 7 canais no registry | ✓ |
| 4 famílias de query | ✓ |
| Lista normativa única em `_coverage.md` | ✓ |
| Queries normativas somente em `_discovery.md` | ✓ |
| Migration linear `001–043` | ✓ |
| `source_runs` com RLS e sem policy de usuário final | ✓ |
| Quatro campos nullable no staging | ✓ |
| Endpoint único `GET /source-coverage`, admin-only e read-only | ✓ |
| Nenhuma exposição de query completa, URL com path/query, erro bruto, traceback ou credencial | ✓ |
| Nenhum botão/endpoint para criar fonte, scraper, retry ou promoção automática | ✓ |
| Domínio emergente permanece apenas candidato visual | ✓ |
| Atores não foram incluídos como canais | ✓ |
| `open_search` é canal lógico, não sinônimo normativo de Tavily | ✓ |
| Linhas legadas continuam no bucket não atribuído | ✓ |
| Zero ambíguo não vira sucesso; denominador ausente retorna `null`; painel indisponível não bloqueia a fila editorial | ✓ |

**Veredito:** spec 03 completa e vigente. Nenhum defeito funcional real
encontrado. Todas as contagens e evidências documentadas acima são reais
(fixtures, mocks e ambiente de teste).
