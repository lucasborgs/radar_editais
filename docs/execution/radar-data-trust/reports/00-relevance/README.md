# Relatório consolidado — Radar Data Trust 00

**Spec:** [`../../../../specs/radar-data-trust-00-relevance-contract.md`](../../../../specs/radar-data-trust-00-relevance-contract.md)
**Status:** vigente · **Data de fechamento:** 2026-07-23
**Branch base:** `codex/radar-data-trust-00-t06` / `f805f4ef8`
**Branch de fechamento:** `codex/radar-data-trust-00-t07`

## Tasks implementadas

| Task | Resultado | Relatório |
|---|---|---|
| `RT00-T01` — Contrato de domínio | `passed` | [`RT00-T01-domain-contract.md`](RT00-T01-domain-contract.md) |
| `RT00-T02` — Goldens representativos | `approved` (owner) | [`RT00-T02-representative-goldens.md`](RT00-T02-representative-goldens.md) |
| `RT00-T03` — Classificadores em shadow | `completed` | [`RT00-T03-shadow-classifiers.md`](RT00-T03-shadow-classifiers.md) |
| `RT00-T04` — Contrato de staging | `completed` | [`RT00-T04-staging-contract.md`](RT00-T04-staging-contract.md) |
| `RT00-T05` — Revisão do operador | `completed` | [`RT00-T05-operator-review.md`](RT00-T05-operator-review.md) |
| `RT00-T06` — Métricas diagnósticas | `completed` | [`RT00-T06-diagnostic-metrics.md`](RT00-T06-diagnostic-metrics.md) |
| `RT00-T07` — Fechamento e reconciliação | `completed` | este documento |

## Invariantes confirmados

| Invariante | Status | Evidência |
|---|---|---|
| Gate humano permanece obrigatório | Confirmado | T03: classificador isolado em shadow, sem conexão com promote/reject. T04: staging mantém `status` editorial intocado. T05: promote/reject ignoram colunas de relevância. |
| `in_scope` não promove automaticamente | Confirmado | Nenhuma task conecta classificação a promoção automática. Promote/reject são exclusivamente editoriais. |
| Erro/None nunca vira `out_of_scope` | Confirmado | T03: 5 categorias de erro sanitizadas, nenhuma fabrica `out_of_scope`. T04: `validate_opportunity_result` rejeita erro não categorizado; erro preserva `pending`. T05: normalizador nunca deriva `out_of_scope` de erro. |
| `promote`/`reject` permanecem editoriais e independentes | Confirmado | T04: testes de inspeção estrutural confirmam que `_stage_records` não menciona promote/reject. T05: endpoints de promote/reject não referenciam colunas de relevância. |
| Cache negativo legado não é reinterpretado | Confirmado | T04: `_row_with_relevance` não chama `_record_rejection`, não toca o ledger, não altera cache. Cache continua por candidato/URL, nunca por instituição. |
| Gold/KG não alterados diretamente pela Descoberta | Confirmado | T04: apenas `discovered_opportunities` é escrita. Nenhuma gold table (`entities`, `entity_relationships`, `match_chunks`) é tocada pelo fluxo de staging. |

## Métricas da run diagnóstica (RT00-T06)

Executada em 2026-07-23 com OpenAI `gpt-4o-mini`, suíte `relevance_shadow` v2,
14/14 cases carregados, manifesto `relevance_shadow-20260723_031659`.

| Métrica | Resultado | Interpretação |
|---|---|---|
| Casos processados | 14/14 | Cobertura total do corpus |
| Decisões avaliáveis | 11/14 | 3 erros operacionais excluídos |
| Concordância de decisão | 9/11 (`0.8182`) | 2 divergências (FN conservadores) |
| Recall `in_scope` (semântico e E2E) | 5/7 (`0.7143`) | 2 FN: `programa:pipe-fapesp`, `agencia:fapesp` |
| Precisão `out_of_scope` | 1/1 (`1.0000`) | Suporte insuficiente (1 caso) |
| Falsos positivos `out_of_scope` | 0 | Nenhum |
| Taxa `needs_review` | 5/11 (`0.4545`) | Incerteza conservadora elevada |
| Erros operacionais | 3/14 (`0.2143`) | 1 contract_violation, 2 grounding_error |
| Grounding médio de evidência | `0.6364` | Diagnóstico sem threshold aceito |

### Casos auditáveis

| Categoria | IDs |
|---|---|
| Divergências/FN | `programa:pipe-fapesp`, `agencia:fapesp` |
| Erros operacionais | `triage-dou-000` (contract_violation), `triage-tavily-118` (grounding_error), `triage-tavily-098` (grounding_error) |
| Divergência de códigos | `triage-tavily-082`, `triage-tavily-079`, `investidor:kptl`, `programa:pipe-fapesp`, `agencia:fapesp` |

### Notas

- Nenhum erro operacional foi convertido em `out_of_scope`.
- Falsos negativos: ambos `needs_review` em vez de `in_scope` — conservadores, não silenciosos.
- Nenhum falso positivo `out_of_scope` foi produzido.

## Artefatos produzidos

### Código versionado (Python)

| Arquivo | Task | Finalidade |
|---|---|---|
| `src/radar/domain/relevance.py` | T01 | Tipos, enums, validadores do contrato de relevância |
| `src/radar/core/eval/relevance_goldens.py` | T02 | Loader hermético dos 14 goldens |
| `src/radar/core/ingestion/relevance_classifier.py` | T03, T04 | 5 classificadores shadow + `validate_opportunity_result` |
| `src/radar/core/eval/relevance_shadow.py` | T03, T06 | Suite diagnóstica + 7 run_evaluators T06 |
| — | T04 | Migration 041 (4 colunas aditivas) |
| `src/radar/api/routers/discovered.py` | T05 | `DiscoveredItem`/`DiscoveredListResponse`, `_normalize_row` |
| — | T05 | Integration call in `opportunity_discovery.py` (`_row_with_relevance`) |

### Dados versionados (`data/evaluation/golden/relevance/`)

| Dataset | Casos | Decisões |
|---|---|---|
| `opportunities.json` | 7 | 1 in_scope, 3 out_of_scope, 3 needs_review |
| `investors.json` | 2 | 1 in_scope, 1 needs_review |
| `icts.json` | 1 | 1 in_scope |
| `programs.json` | 2 | 2 in_scope |
| `agencies.json` | 2 | 2 in_scope |
| `actor_sources.json` | 7 snapshots | Hashes SHA-256 verificados |
| `manifest.json` | 14 | Revisão do proprietário aprovada |

### Testes

| Suíte | Testes | Status |
|---|---|---|
| `test_relevance.py` | 107 | all passed |
| `test_relevance_goldens.py` | 39 | all passed |
| `test_relevance_shadow.py` | 149 | all passed |
| `test_relevance_staging.py` | 27 | all passed |
| `test_discovery_api_contract.py` | 29 | all passed |
| `test_hardening_pr4.py` | 16 | all passed |
| `test_opportunity_discovery_cache.py` | 4 | all passed |
| `test_discovery_promotion.py` | 3 | all passed |
| **Subtotal selecionado** | **374** | **all passed** |

### Validação da RT00-T07

| Gate | Resultado |
|---|---|
| `pytest -q` completo | `1143 passed, 64 skipped, 4 warnings` |
| Ruff sobre Python versionado | `All checks passed` |
| `npx tsc --noEmit` | sem erros |
| `npm run lint` | sem erros; 4 warnings preexistentes em `src/lib/auth.tsx` |
| `git diff --check` | limpo |
| Migrations | 41 arquivos, sequência linear `001–041`, sem lacuna ou duplicata |
| Run diagnóstica | reutilizada a run T06; nenhuma nova chamada LLM |
| `pending → promote/reject` em Supabase local | pendente: portas locais 54321/54322 indisponíveis |

Na primeira execução do `pytest` completo, `test_suites_registered` revelou que
`relevance_shadow` estava no registry desde T03, mas ausente do conjunto esperado
pelo teste. A falha também existia na base `f805f4ef8`; a T07 atualizou a
enumeração e a suíte completa passou.

### Documentos reconciliados

| Documento | Alteração |
|---|---|
| `docs/domain/schema.md` | §2: colunas `relevance_*` em discovered_opportunities. §5.12: classificação de relevância no staging. |
| `docs/architecture.md` | §5: classificação v1 em shadow durante staging. |
| `docs/domain/sources/_discovery.md` | Menção à classificação v1 em shadow e contrato de relevância. |
| `docs/specs/discovery-operations.md` | §2: colunas de relevância no staging (migration 041). |
| `docs/specs/evaluation-operations.md` | Crescimento posterior do registry de 10 para 12 suítes; `explore` e `relevance_shadow` permanecem diagnósticas. |
| `AGENTS.md` | Lista operacional das 12 suítes reconciliada. |
| `docs/specs/radar-data-trust-00-relevance-contract.md` | Status: `proposta para aprovação` → `vigente`. |
| `docs/specs/radar-data-trust.md` | Status: `proposta para aprovação` → `vigente (spec 00 concluída)`. Tabela §9: spec 00 marcada como vigente. |
| `docs/execution/radar-data-trust/reports/00-relevance/README.md` | Este documento. |

## Limitações da Spec 00

1. **Corpus de 14 casos.** Métricas por kind e código são limitadas pela
   cobertura do golden. Kinds com 1–2 casos produzem métricas instáveis.
2. **Ausência de ator `out_of_scope`.** O golden não contém ator `out_of_scope`.
   `out_of_scope_precision` para investidor, ICT, programa e agência permanece
   não medida em casos reais.
3. **Nenhum threshold ou gate foi aceito.** A spec 00 é diagnóstica. Promoção a
   gate oficial exige corpus ampliado, thresholds aceitos e spec 02.
4. **Cobertura de reason codes incompleta.** Códigos sem referência no golden
   nem na predição não geram métrica — correto, mas deixa lacunas visíveis.
5. **QA manual da UI pendente.** A página de descoberta não foi validada em
   navegador durante T05 (ambiente local não disponível). Tipos e lógica foram
   validados via `tsc --noEmit` + `npm run lint` + testes Python.
6. **Frontend sem testes de componente.** Validação TypeScript via type checking
   e lint apenas; sem testes unitários de componente React.
7. **Run externa com baseline diagnóstico.** Os números da T06 são sinal de
   qualidade, não threshold. Devem ser reavaliados com corpus ampliado.

## Pendências para Data Trust 02 (Quality Gates)

| # | Pendência | Prioridade | Task sugerida |
|---|---|---|---|
| 1 | Corpus de goldens insuficiente para thresholds. 14 casos não permitem gates estatísticos. | Alta | RT02-T01: expandir golden para ≥40 casos estratificados |
| 2 | Nenhum threshold aceito pelo proprietário. | Alta | RT02-T02: propor e aceitar thresholds mínimo por métrica |
| 3 | Suíte `relevance_shadow` é `diagnostic`, não `gate`. | Alta | RT02-T03: promover a gate com corpus e thresholds aceitos |
| 4 | Apenas 1 caso `out_of_scope` no golden. | Média | RT02-T04: adicionar exemplos reais de `out_of_scope` |
| 5 | Nenhum ator `out_of_scope` testado. | Média | RT02-T04: incluir atores out_of_scope no golden |
| 6 | Cobertura de reason codes incompleta (códigos não exercitados). | Média | RT02-T05: expandir corpus para cobrir reason codes não testados |
| 7 | Variação entre runs com temperatura zero observada na T03. | Baixa | RT02-T06: avaliar estabilidade com corpus maior |
| 8 | Cobertura de fontes limitada (FINEP, FAPESP, FAPESC, web). | Média | RT03-T01: spec de cobertura (Data Trust 03) |

## Pendências para Data Trust 03 (Source Coverage)

| # | Pendência | Prioridade | Task sugerida |
|---|---|---|---|
| 1 | Cobertura geográfica e por mecanismo não medida. | Alta | RT03-T01: métricas de cobertura por região, mecanismo e fonte |
| 2 | Frescor das fontes não observável. | Média | RT03-T02: métricas de latência entre publicação e descoberta |
| 3 | Novas fontes úteis aprendidas pela Descoberta não são medidas. | Baixa | RT03-T03: registrar rendimento e taxa de aprovação por fonte/query |
| 4 | Monitoramento de saúde de scrapers não implementado. | Média | RT03-T04: health check de fontes prioritárias |

## Pendências operacionais (não bloqueantes)

| # | Pendência | Observação |
|---|---|---|
| 1 | QA manual da UI de descoberta pendente (5 cenários visuais). | T05 não teve ambiente local disponível. Validado por tipo estático + testes. |
| 2 | Fluxo manual `pending → promote/reject` em Supabase local pendente. | T07 confirmou que as portas locais 54321/54322 não estavam disponíveis; nenhum banco remoto foi usado. |
| 3 | Testes da API usam mock de Supabase (não banco real). | Cobertura contratual; integração com Supabase local seria ideal. |
| 4 | Progressive disclosure sem animação (CSS). | Coerente com simplicidade da pré-beta. |
| 5 | `quote` de evidência sem truncamento na UI. | Pode ser longo; aceito como comportamento inicial. |

## Critérios de conclusão da spec

| Critério §14 | Status | Evidência |
|---|---|---|
| Mesmo classificador versionado roda em produção e no harness | OK | `classify_opportunity()` é chamado por `_row_with_relevance` no staging e pela suíte `relevance_shadow` |
| Golden e métricas estratificadas publicados | OK | 14 goldens no repo; métricas por kind, decisão, código |
| Staging preserva decisão, razões e evidência | OK | Migration 041: `relevance_verdict` (jsonb), `relevance_status`, `relevance_error` |
| Falhas e ambiguidades chegam à revisão humana | OK | `error`/`needs_review` preservam `pending` editorial; `unclassified` para legados |
| Nenhuma exclusão institucional introduzida | OK | Cache negativo continua por candidato/URL; T04 confirma que `_row_with_relevance` não altera ledger |
| Documentação autoritativa descreve comportamento comprovado | OK | Schema, arquitetura, runbook e spec reconciliados nesta task |

## Arquivos alterados (RT00-T07)

```
docs/domain/schema.md
docs/domain/sources/_discovery.md
docs/architecture.md
docs/specs/discovery-operations.md
docs/specs/evaluation-operations.md
docs/specs/radar-data-trust.md
docs/specs/radar-data-trust-00-relevance-contract.md
docs/execution/radar-data-trust/reports/00-relevance/README.md
AGENTS.md
tests/unit/test_eval_harness.py
```
