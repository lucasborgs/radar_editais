# RT03-T01 — Relatório

## Resultado

Contrato de canais e famílias de busca implementado via registry YAML versionado.
Modos de coleta (`modes`) lidos exclusivamente do doc autoritativo `_coverage.md`;
não há lista paralela em Python. Queries de Descoberta migradas de flat strings
para objetos `{text, family}`, cada uma vinculada a uma família registrada, com
projeção `discovery_queries_flat()` para compatibilidade retroativa.

## Sete canais registrados

| `source_key` | `mode` | `flag_name` |
|---|---|---|
| `finep` | `dedicated` | — |
| `fapesp` | `dedicated` | — |
| `fapesc` | `dedicated` | — |
| `web_curated` | `curated_web` | — |
| `open_search` | `open_search` | — |
| `dou` | `official_feed` | `DISCOVERY_DOU_ENABLED` |
| `hub_expansion` | `hub` | `DISCOVERY_HUB_CRAWL_ENABLED` |

## Quatro famílias de busca

| `key` | Descrição |
|---|---|
| `state_innovation_funding` | Chamadas estaduais e FAPs fora das fontes dedicadas |
| `corporate_open_innovation` | Desafios e pilotos publicados por empresas/hubs |
| `startup_acceleration` | Aceleração, incubação e programas com benefício concreto |
| `international_brazil_access` | Oportunidades internacionais acessíveis a empresas brasileiras |

## API pública alterada

- `coverage_modes()` — lê `modes:` de `_coverage.md`; sem lista hardcoded
- `_discovery_raw()` — fonte única do YAML (substitui tupla solta)
- `discovery_queries()` — retorna `list[dict]` com `{text, family}`, valida family contra `query_families`
- `discovery_queries_flat()` — projeção `list[str]` para compatibilidade
- `discovery_config()` — dicionário completo, queries em formato plano
- Validações: campos obrigatórios, intervalos positivos, coerência enabled/flag, source_key lowercase sem duplicatas, family keys registradas

## Arquivos alterados

- `docs/domain/sources/_coverage.md` (modes YAML block)
- `docs/domain/sources/_discovery.md` (query_families + queries estruturadas)
- `src/radar/core/kg/schema.py` (loaders, validações, projeção compat)
- `tests/unit/test_source_coverage_registry.py`
- `docs/execution/radar-data-trust/reports/03-source-coverage/RT03-T01-report.md` (este)

## Testes e validações

- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_registry.py`: **42 passed**
- `ruff check src/radar/core/kg/schema.py tests/unit/test_source_coverage_registry.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**
- `open_search` não referencia Tavily como canal normativo
- `discovery_config()` mantém compatibilidade (queries planas, caps intactos)
- Validação rejeita: source_key duplicado, modo inválido, uppercase key, family key duplicado, family ausente, query sem `text` ou `family`

## Divergências e limitações

- Migration não aplicada ao Postgres local (`psql` indisponível no ambiente); validação estrutural via análise SQL.
- Em uma corrida rara entre `select` e `insert` em `start_run`, o escritor perdedor pode retornar `None` (falha de unicidade no DB), mas nunca sobrescreve a run existente e um retry recupera o ID correto.
- Nenhuma divergência do plano documental além do relatado.
- Não foi alterado comportamento da Descoberta, runtime ou staging.
- RT03-T03 **não foi iniciada**.

## Validação independente

- 81 testes direcionados (42 T01 + 39 T02)
- Suíte completa: **1469 passed, 77 skipped**
- Ruff limpo
- `git diff --check` limpo
- Auditoria Codex: **aprovada em 2026-07-26**

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt03-t01-t02`
- Commit: `f78385b6f`
- `ENVIRONMENT=test` em toda execução
- Sem `.env`, produção, rede, Tavily, DOU, LLM ou merge/push
