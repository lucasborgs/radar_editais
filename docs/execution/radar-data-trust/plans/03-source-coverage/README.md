# Plano executável — Radar Data Trust 03 (cobertura e saúde das fontes)

**Spec:** [`../../../../specs/radar-data-trust-03-source-coverage.md`](../../../../specs/radar-data-trust-03-source-coverage.md)
**Spec-mãe:** [`../../../../specs/radar-data-trust.md`](../../../../specs/radar-data-trust.md)
**Status:** pronto para aprovação

## Resultado

Medir somente as **Fontes monitoradas pelo Radar**: um registry normativo
versionado, histórico aditivo de `source_runs`, estados operacionais
conservadores, snapshots locais dos três catálogos e uma consulta/painel de
operador apenas para leitura. Não mede cobertura do Brasil, não cria uma suíte
de eval, não altera relevância, gold, ranking, RAG, promoção editorial ou os
produtores de dados.

## Ordem e dependências

| Task | Plano | Resultado | Depende de |
|---|---|---|---|
| `RT03-T01` | [`domain-registry.md`](RT03-T01-domain-registry.md) | registry YAML e contrato puro | aprovação da spec |
| `RT03-T02` | [`source-runs-storage.md`](RT03-T02-source-runs-storage.md) | migration + escrita best-effort | T01 |
| `RT03-T03` | [`daily-etl-observability.md`](RT03-T03-daily-etl-observability.md) | um run por scraper/rodada diária | T01, T02 |
| `RT03-T04` | [`catalog-snapshots.md`](RT03-T04-catalog-snapshots.md) | inspeção determinística dos catálogos | T01, T02 |
| `RT03-T05` | [`discovery-observability.md`](RT03-T05-discovery-observability.md) | Tavily/DOU separados sem quebrar `list[dict]` | T01, T02 |
| `RT03-T06` | [`admin-read-model.md`](RT03-T06-admin-read-model.md) | agregação, `GET /source-coverage` e painel | T03–T05 |
| `RT03-T07` | [`final-validation.md`](RT03-T07-final-validation.md) | baseline, reconciliação e fechamento | T01–T06 |

## Ondas seguras e sobreposição

- **Onda A:** T01 e T02 em sequência curta. T02 consome o formato e as
  invariantes de T01; nenhum dos dois toca produtores existentes.
- **Onda B:** T03 e T04 são independentes no contrato, mas disputam o mesmo
  bloco de `src/radar/core/tasks.py`; aterrar T03 primeiro e T04 em seguida,
  que acrescenta a chamada de snapshot. T05 (Descoberta) pode correr em
  paralelo a essa sequência, pois consome só o contrato estável de T02.
- **Onda C:** T06 só depois que os três escritores produzem linhas reais. Ele
  concentra `src/radar/core/services/source_coverage.py`, router/app,
  `frontend/src/lib/api.ts` e `/discovered`; não concorre com T03–T05.
- **Onda D:** T07 é o único ponto que atualiza relatórios e status/documentação
  de fechamento. Não reconciliar a spec durante tasks intermediárias.

Arquivos de pouso compartilhados a tratar serialmente: `src/radar/core/tasks.py`
(T03/T04), `src/radar/core/services/source_coverage.py` (T02/T03/T06) e
`docs/domain/sources/_coverage.md` (T01 é seu único autor). T04 e T05 usam
módulos próprios para não disputar o repositório de runs. T05 deve manter a
compatibilidade da assinatura pública de Descoberta; T06 é o único autor de
API/UI nesta spec.

## Invariantes transversais

- O registry em `docs/domain/sources/_coverage.md` é a única lista normativa;
  Python só o carrega/valida e nunca duplica canais, cadência ou flags.
- Só existe a tabela nova `source_runs`; ela é aditiva, global, RLS habilitada e
  sem policy de usuário final. `pipeline_errors`, `web_sources`, staging e
  artefatos continuam suas autoridades.
- Telemetria é best-effort: erro de abrir/finalizar um run não muda payload,
  retorno, retry, alerta nem sucesso/falha do coletor.
- `0` sem prova de ausência é ambíguo. Estado público é derivado em leitura,
  nunca materializado, e métricas sem denominador são `null`, não `0`.
- Não persistir URL com query, conteúdo, traceback, prompt, resposta de LLM ou
  segredo. A API expõe só contadores e razões canônicas sanitizadas.
- Não há backfill fictício, alerta, reexecução, edição de registry, nova eval,
  acesso a produção/rede ou mudança de modelo/prompt.

## Gate proporcional

- Cada task: testes direcionados, `ruff check` no escopo e `git diff --check`.
- Migration: aplicar/reaplicar apenas contra banco local de teste e verificar
  schema, RLS e transição idempotente.
- Uma fixture por modalidade basta; mocks/fakes substituem Supabase, Tavily,
  DOU e LLM. Não carregar `.env` nem chamar `supabase db push`.
- T06 adiciona `npx tsc --noEmit` e `npm run lint` do frontend; T07 roda a suíte
  Python completa e compara falhas com a branch-base.

Cada relatório de task deve ser criado em
`docs/execution/radar-data-trust/reports/03-source-coverage/` pelo implementador
da task, com diff/commit, testes executados, limitações observadas e confirmação
explícita de ambiente hermético. T07 consolida o `README.md` daquele diretório.
