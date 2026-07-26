# RT03-T05 — Relatório

## Resultado

Read model determinístico que conecta `source_runs` às decisões editoriais do
staging: rendimento por canal, funil editorial, saúde, lacunas e domínios
emergentes. Nenhuma escrita no staging, fonte/scraper automático, eval ou LLM.

## Arquivos criados

| Arquivo | Função |
|---|---|
| `src/radar/core/services/source_coverage_metrics.py` | Serviço read-only com funções puras que recebem dados + data de referência |
| `tests/unit/test_source_coverage_metrics.py` | 42 testes direcionados |

## Comportamento implementado

### 1. Runs e rendimento (`compute_channel_run_metrics`)

- última tentativa (mais recente `started_at`)
- último sucesso técnico (mais recente `completed_at` de `succeeded`/`partial`)
- totais acumulados de `records_observed`, `records_emitted`, `records_staged`
- `yield_rate = total_staged / total_emitted` — `None` quando denominador é
  zero ou ausente (nunca 0 fabricado)

### 2. Funil editorial (`compute_channel_editorial_funnels`, `compute_family_editorial_funnels`)

- aprovados (`promoted`), rejeitados (`rejected`), pendentes (`pending`) por
  canal e por família de query
- bucket `__unassigned__` para linhas legadas (`discovery_channel IS NULL`)
- `approval_rate` somente com denominador válido (`approved + rejected > 0`)
- `avg_review_hours` somente quando ambos `created_at` e `reviewed_at` existem

### 3. Saúde (`derive_channel_health`)

Precedência exata da spec: `disabled → failing → degraded → stale → healthy → unknown`

- `disabled`: flag gated desligada
- `failing`: última run `failed`
- `degraded`: última run `partial`
- `stale`: último sucesso saudável > 2× `expected_interval_hours` atrás
- `healthy`: última run `succeeded`/`partial` com resultado observável dentro
  de 1× intervalo
- `unknown`: nunca executado, zero ambíguo ou fora de 1× intervalo

`_has_observable_result`: `records_observed > 0` ou `records_staged > 0`.

### 4. Lacunas (`detect_gaps`)

Sinais explícitos sem score:

| Sinal | Quando |
|---|---|
| `enabled_no_run` | canal habilitado sem nenhuma run |
| `ambiguous_run` | run `succeeded`/`partial` com zero registros |
| `delayed` | última run concluída > `expected_interval_hours` atrás |
| `family_no_denominator` | família com zero linhas revisadas |
| `pending_queue` | fila editorial com `status = pending` |

Nenhum sinal afirma ausência de oportunidades ou baixa cobertura da web.

### 5. Domínios emergentes (`compute_emerging_domains`)

- exclusivamente `origin_domain` válido, `status = promoted`, `reviewed_at`
  nos últimos 90 dias
- `candidate_for_dedicated_monitoring = True` quando ≥ 2 aprovações do mesmo
  domínio no período
- expõe domínio, contagem, `first_approved_at`, `last_approved_at`
- rejeitados e pendentes nunca viram candidatos
- sem criação de fonte, scraper, configuração ou ação automática

### 6. Agregador principal (`compute_source_coverage`)

Coordena as 5 etapas acima em um `SourceCoverageReport` único.

## Testes direcionados — 42 passed

| Teste | O que cobre |
|---|---|
| `TestRunMetricsDenominator::test_denominator_present` | emitted > 0 → yield computado |
| `TestRunMetricsDenominator::test_denominator_absent_emitted_none` | emitted None → yield None |
| `TestRunMetricsDenominator::test_denominator_absent_emitted_zero` | emitted 0 → yield None |
| `TestRunMetricsDenominator::test_empty_runs_returns_nulls` | sem runs → tudo None |
| `TestRunMetricsDenominator::test_aggregates_multiple_runs` | totais acumulados |
| `TestRunMetricsDenominator::test_last_attempt_and_success_from_latest` | temporais corretos |
| `TestEditorialFunnel::test_by_channel` | aprovação/rejeição por canal |
| `TestEditorialFunnel::test_unassigned_bucket` | linhas legadas no bucket não atribuído |
| `TestEditorialFunnel::test_no_denominator_returns_none_rate` | approval_rate None sem revisão |
| `TestFamilyFunnel::test_by_family` | aprovação/rejeição por família |
| `TestHealthPrecedence::test_disabled` | flag off → disabled |
| `TestHealthPrecedence::test_disabled_overrides_failing` | disabled precede failing |
| `TestHealthPrecedence::test_failing` | última run failed → failing |
| `TestHealthPrecedence::test_degraded` | última run partial → degraded |
| `TestHealthPrecedence::test_stale_two_windows` | > 2× intervalo → stale |
| `TestHealthPrecedence::test_not_stale_within_two_windows` | entre 1-2× → unknown |
| `TestHealthPrecedence::test_healthy` | saudável recente → healthy |
| `TestHealthPrecedence::test_unknown_no_runs` | sem runs → unknown |
| `TestHealthPrecedence::test_ambiguous_zero` | sucesso com 0 registros → unknown |
| `TestHealthPrecedence::test_ambiguous_zero_staged_with_observed` | observed > 0 → healthy |
| `TestHealthPrecedence::test_disabled_via_flag_env_off` | hub_expansion off → disabled |
| `TestHealthPrecedence::test_enabled_via_flag_env_on` | hub_expansion on → unknown |
| `TestHealthPrecedence::test_full_precedence_chain` | 4 estados em sequência |
| `TestGaps::test_enabled_no_run` | canal habilitado sem run |
| `TestGaps::test_disabled_no_run_no_gap` | desabilitado sem run → sem gap |
| `TestGaps::test_ambiguous_run` | run ambígua |
| `TestGaps::test_delayed` | canal atrasado |
| `TestGaps::test_family_no_denominator` | família sem denominador |
| `TestGaps::test_pending_queue` | fila editorial pendente |
| `TestGaps::test_no_gaps_healthy_channel` | canal saudável sem gaps |
| `TestEmergingDomains::test_domain_with_two_approvals_is_candidate` | threshold 2 |
| `TestEmergingDomains::test_domain_with_one_approval_not_candidate` | 1 aprovação → não candidato |
| `TestEmergingDomains::test_domain_with_zero_approvals_empty` | sem aprovações → vazio |
| `TestEmergingDomains::test_rejected_not_counted` | rejeitados ignorados |
| `TestEmergingDomains::test_pending_not_counted` | pendentes ignorados |
| `TestEmergingDomains::test_outside_90_days_window` | fora da janela → ignorado |
| `TestEmergingDomains::test_mixed_domains` | múltiplos domínios |
| `TestEmergingDomains::test_boundary_90_days_exactly` | exatamente 90 dias → incluído |
| `TestEmergingDomains::test_no_side_effects` | sem efeitos colaterais |
| `TestSourceCoverageReport::test_full_report_no_side_effects` | integração completa |
| `TestSourceCoverageReport::test_empty_report` | todos os canais com valores default |
| `TestSourceCoverageReport::test_input_runs_unchanged` | side-effect free |

## Validação

- `pytest tests/unit/test_source_coverage_metrics.py`: **42 passed**
- Suíte completa (excluindo T05): **1523 passed, 2 skipped** (inalterado)
- Suíte completa (com T05): **1565 passed, 2 skipped**
- `ruff check src/radar/core/services/source_coverage_metrics.py tests/unit/test_source_coverage_metrics.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt03-t05`
- Branch: `codex/radar-data-trust-03-t05`
- Base: `a16ba0931`
- Sem `.env`, produção, rede, Tavily, DOU, LLM, Supabase Cloud ou merge/push
- RT03-T06 **não foi iniciada**
