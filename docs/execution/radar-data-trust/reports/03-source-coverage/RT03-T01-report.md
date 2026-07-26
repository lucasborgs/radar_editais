# RT03-T01 — Relatório

## Resultado

Contrato de canais e famílias de busca implementado via registry YAML versionado.

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

## Arquivos alterados

- `docs/domain/sources/_coverage.md` (novo)
- `docs/domain/sources/_discovery.md` (adicionado `query_families` YAML block)
- `src/radar/core/kg/schema.py` (adicionado `coverage_config`, `coverage_channels`, `coverage_channel`, `query_families`, `clear_cache` estendido)
- `tests/unit/test_source_coverage_registry.py` (novo)
- `docs/execution/radar-data-trust/reports/03-source-coverage/RT03-T01-report.md` (este)

## Testes e validações

- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_registry.py`: **28 passed**
- `ruff check src/radar/core/kg/schema.py tests/unit/test_source_coverage_registry.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**
- `open_search` não referencia Tavily como canal normativo
- `discovery_config()` mantém compatibilidade (queries planas, caps intactos)
- Validação rejeita: source_key duplicado, modo inválido, uppercase key, family key duplicado

## Divergências e limitações

- Nenhuma divergência do plano documental.
- `open_search` permanece canal lógico; Tavily é detalhe de implementação de `web_search`, não registro normativo.
- As queries planas continuam funcionando para o consumidor atual (`opportunity_discovery.py`).
- Não foi alterado comportamento da Descoberta, runtime ou staging.

## Ambiente

- Repositório isolado em `/private/tmp/radar-editais-rt03-t01-t02`
- `ENVIRONMENT=test` em toda execução
- Sem `.env`, produção, rede, Tavily, DOU, LLM ou merge/push
