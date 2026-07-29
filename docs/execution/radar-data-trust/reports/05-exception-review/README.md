# RT05 — Relatorio consolidado: revisao humana de excecoes

**Spec:** [`radar-data-trust-05-exception-review.md`](../../../../specs/radar-data-trust-05-exception-review.md)
**Status:** vigente · fechado localmente em 2026-07-29
**Branch de fechamento:** `codex/radar-data-trust-05-t09`
**Base:** `5968136c6`
**Worktree:** `/private/tmp/radar-editais-rt05-t09`
**Escopo:** fila unica de excecoes, revisao append-only, read model temporal
canonico, surfaces administrativas e comunicacao conservadora; sem OCR, visao,
LLM, crawler novo, backfill integral, merge ou push.

## Resultado

A Spec 05 ficou reconciliada localmente: contrato, persistencia, detector em
shadow, revisao humana, enforcement temporal, consumidores e documentacao agora
descrevem a mesma regra. `deadline=null` deixou de implicar fluxo continuo,
`continuous` exige evidencia explicita e recuperavel, e `needs_review`/`closed`
nao entram no Radar ativo. Finep/Eureka permanece nao ativo ate revisao valida.

## Tasks e commits reais

| Task | Resultado | Commits reais |
|---|---|---|
| T01 | contrato temporal, fixture Finep/Eureka e reconciliacao `deadline >= hoje` | `7a6abbe9b`, `fa048494c`, `519f3085f`, `388aae58b`, `7650544ad`, `6f35e7f53`, `89a909935` |
| T02 | migration 046, repositorio idempotente e revisao append-only | `f38bd1b80`, `c6ee6a435`, `04a2f5031`, `a78566a75`, `4c6c4b558`, `06e8c4900`, `88bf5d190`, `9b130d3c8`, `d877f2aae`, `08a16fd63` |
| T03 | detector temporal em shadow e integracao best-effort no gold | `ac5b43da0`, `263d0f94b`, `e833879d8`, `2febd97a5` |
| T04 | projecao temporal unica, override humano e fail-closed | `7abc6549e`, `bd9f9349b`, `0229b996f`, `463b8be41` |
| T05 | API administrativa autenticada da fila | `f23c6dbfe`, `dbcf450cb`, `d8a22934b`, `b7f436dd9` |
| T06 | UI administrativa em Descoberta | `2c59ea335`, `f05b0561a`, `6316c1128`, `0e2798d36`, `138ab38cf` |
| T07 | enforcement temporal e read model em lote | `db9bfa83e`, `768e5d5aa` |
| T08 | comunicacao de validade em Ecossistema, Radar, Explorar, Escrita e Aplicacoes | `2a3bcdc02`, `f09580fd3`, `5968136c6` |
| T09 | metricas locais, reconciliacao documental e fechamento | `9426efc98`, `96e548d53`, `606ecba3d`, `9dabe877a` |

Esta tabela foi reconstruida a partir da historia Git ate `9dabe877a`. A
correcao documental pos-auditoria deste relatorio fica fora da propria tabela
para evitar autorreferencia; `Auditoria Codex` permanece pendente.

## Resultado por task

| Task | Estado final |
|---|---|
| T01 | concluida e auditada |
| T02 | concluida e auditada |
| T03 | concluida e auditada |
| T04 | concluida e auditada |
| T05 | concluida e auditada |
| T06 | concluida e auditada |
| T07 | concluida e auditada |
| T08 | concluida e auditada em 2026-07-29 |
| T09 | concluida localmente; auditoria deste fechamento permanece pendente |

## Contrato final e invariantes reconciliados

- `temporal_mode` permanece `fixed | continuous | unknown`; `validity_state`
  permanece `active | closed | needs_review`.
- `deadline >= hoje` em `America/Sao_Paulo`; o dia de encerramento continua
  ativo ate o fim do dia local.
- Ausencia de prazo nunca prova continuidade. `continuous` exige evidencia
  oficial explicita e recuperavel.
- `needs_review` e `closed` nao entram no Radar ativo.
- Finep/Eureka (`ABERTA`, sem prazo, sem evidencia) permanece
  `unknown/needs_review` ate revisao valida.
- Revisao continua append-only e idempotente por `review_id`.
- Colisao material de `review_id` continua conflito (`409`), nao sucesso nem
  falha silenciosa de storage.
- API e UI de excecoes permanecem administrativas; revisao temporal nao promove
  nem rejeita oportunidades da Descoberta.
- Investidores permanecem fora das regras temporais de oportunidades.
- Ecossistema, Radar, Explorar, Escrita e Aplicacoes consomem o mesmo payload
  canonico: `temporal_mode`, `validity_state`, `temporal_value`,
  `decision_source`, `last_verified_at`.
- Logs e payloads permanecem sanitizados: sem segredo, URL privada,
  justificativa interna, traceback cru ou documento bruto.

## Metricas diagnosticas implementadas

Modulo puro: `src/radar/core/services/data_quality_metrics.py`

As metricas operam apenas sobre listas ja carregadas e nao leem producao, nao
criam API, dashboard, persistencia, SLA, prioridade, threshold, alerta ou
autoaprendizado.

Formulas:

- `exceptions_by_status`: contagem por `status`.
- `exceptions_by_issue_code`: contagem por `issue_code`.
- `exceptions_by_source`: contagem por fonte distinta encontrada em
  `evidence_refs`; ausencia observada cai em `unknown`.
- `exceptions_by_field_path`: contagem por `field_path`.
- `open_exception_age_days`: para excecoes `open`, dia civil de `as_of` menos o
  dia civil de `detected_at` em `America/Sao_Paulo`.
- `mean_open_exception_age_days`: media simples das idades abertas; sem abertas
  observadas -> `None`.
- `review_latency_days`: para revisoes observadas, `reviewed_at - detected_at`
  por `exception_id`; revisoes nao observadas -> `None`.
- `mean_review_latency_days`: media simples das latencias validas; sem pares
  validos -> `None`.
- `reopened_exceptions`: novas fingerprints depois de uma fingerprint resolvida
  no mesmo grupo logico
  `(subject_kind, subject_id, field_path, issue_code)`.
- `review_decisions`: contagem por `decision`; revisoes nao observadas -> `None`.
- `cases_prevented_from_active`: casos cujo valor legado aparentaria atividade
  (`ABERTA` ou prazo futuro), mas cuja projecao final nao e `active`.

Baseline local das fixtures T09:

- distribuicao de exemplo: `open=1`, `resolved=1`, `superseded=1`;
- fontes: `finep=1`, `fapesc=1`, `unknown=1`;
- campos: `deadline=2`, `status=1`;
- idade aberta Finep/Eureka no baseline: `14` dias;
- latencias de revisao no baseline: `1.0` e `4.0` dias; media `2.5`;
- reaberturas no baseline: `1`.

Denominadores ausentes:

- `review_latency_days`, `mean_review_latency_days` e `review_decisions`
  retornam `None` quando a rodada nao observou revisoes.
- Listas observadas vazias retornam `{}` e media `None`, nunca zero como
  sucesso implicito.

## Reconciliacao funcional

Confirmado por testes e inspecao local:

- `deadline=null` nao implica fluxo continuo.
- Apenas evidencia explicita confirma continuidade.
- Finep/Eureka nao e apresentado como ativo sem revisao valida.
- `needs_review` e `closed` ficam fora do Radar ativo.
- Revisao continua append-only, idempotente e fail-closed.
- API e UI de excecoes continuam administrativas.
- Revisao temporal nao chama `promote` nem `reject`; o estado editorial da
  Descoberta permanece independente.
- Aplicacoes, Explorar, Ecossistema, Radar e Escrita leem o contrato temporal
  canonicamente reconciliado.
- Investidores nao recebem payload temporal.
- Sanitizacao de payload/log segue coberta por testes de API, repositorio,
  projecao e read model.

## Sinais transferidos para a Spec 06

Buckets diagnosticos expostos pelo modulo:

- `temporal_missing_or_conflicting`: excecoes de prazo/status ausente ou
  conflitante (`temporal_status_without_basis`, `temporal_status_conflict`,
  `critical_fact_missing` em `deadline`/`status`).
- `document_incomplete`: casos sem evidencia suficiente ou com locator
  `unresolved` para sustentar o campo.
- `layout_or_ocr_candidates`: casos com documento recuperado mas sem quote ou
  apenas `document_only`/`unresolved`, indicando possivel dificuldade de
  layout, OCR ou visao futura.
- `insufficient_for_any_decision`: ausencia simultanea de evidencia e valor
  util, sem base para qualquer decisao automatica.

Esses sinais sao apenas insumo de priorizacao para a Spec 06. Nenhum parser,
OCR, visao, LLM, retry ou cascata adaptativa foi implementado aqui.

## Divergencias encontradas e resolucao

- A spec e a documentacao principal ainda marcavam a RT05 como "aprovada; nao
  iniciada" ou "00-04 concluidas". Foram reconciliadas para refletir o runtime
  local entregue.
- `docs/architecture.md` ainda descrevia o Stage 0 como prazo/continuo direto.
  Foi ajustado para explicitar o read model temporal unico.
- O relatorio T08 ainda estava com `Auditoria Codex: pendente`. Foi marcado
  como aprovado em `2026-07-29`, conforme a instrucao deste fechamento.

## Validacao executada

Ambiente: sempre `ENVIRONMENT=test`, sem `.env`, sem producao, sem rede de
aplicacao, sem credenciais reais, sem merge, sem push e sem inicio da Spec 06.

Focais RT05:

- `pytest -q` nas suites RT05 direcionadas -> `363 passed, 5 skipped`.

Gate completo:

- `ENVIRONMENT=test PYTHONPATH=src .../pytest -q` -> `2076 passed, 77 skipped`.
- `ruff check $(git ls-files '*.py')` -> aprovado.
- `cd frontend && npx tsc --noEmit` -> aprovado.
- `cd frontend && npm run lint` -> aprovado com warnings preexistentes.
- `git diff --check 5968136c6..HEAD` -> aprovado apos remover whitespace.

Correcao pos-auditoria:

- `tests/unit/test_data_quality_metrics.py` -> `5 passed`, incluindo a
  fronteira `2026-07-29T01:00:00Z`, que pertence a `2026-07-28` no dia civil de
  `America/Sao_Paulo` e tem idade de um dia em `as_of=2026-07-29`.
- `ruff check src/radar/core/services/data_quality_metrics.py
  tests/unit/test_data_quality_metrics.py` -> aprovado.

Suíte diagnostica `provenance`:

- `ENVIRONMENT=test PYTHONPATH=src python -m radar.core.eval run provenance`
  executou em fallback local, sem publicacao, com `6` casos e arquivo em
  `eval_results/20260729_194816_provenance.json`.

## Migrations verificadas

- As migrations permanecem lineares de `032` a `046`.
- `046_data_quality_exceptions.sql` permanece na posicao esperada, apos
  `045_writing_turn_idempotency.sql`.
- Nenhuma migration remota foi aplicada.

## Warnings, falhas preexistentes e limitacoes

Falhas preexistentes relevantes no fechamento: nenhuma regressao nova detectada.

Warnings preexistentes observados:

- `pytest -q` completo manteve `StarletteDeprecationWarning` do `TestClient`.
- `pytest -q` completo manteve `DeprecationWarning` de `datetime.utcnow()` em
  `src/radar/core/services/writing_session.py`.
- `npm run lint` manteve warnings antigos de hooks em `frontend/src/app/page.tsx`,
  `frontend/src/app/workspace/[sessionId]/page.tsx` e `frontend/src/lib/auth.tsx`.

Limitacoes assumidas:

- As metricas T09 sao locais e puras; nao existe dashboard ou consulta remota.
- O baseline numerico registrado aqui e apenas de fixtures representativas,
  incluindo Finep/Eureka; nao mede estoque produtivo.
- A Spec 06 continua fora de escopo: nenhum tratamento adaptativo foi iniciado.

## Ambiente e auditoria

- Nao houve acesso a producao, Supabase remoto, rede externa de dados, LLM real
  ou Langfuse.
- Nao houve leitura de `.env` nem uso de credenciais reais.
- O symlink temporario de `frontend/node_modules` foi criado apenas para validar
  `tsc`/`lint` e removido ao final.
- Auditoria Codex: aprovada em 2026-07-29.
