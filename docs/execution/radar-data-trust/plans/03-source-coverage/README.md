# Plano executável — Radar Data Trust 03 (Descoberta e cobertura)

**Spec:** [`../../../../specs/radar-data-trust-03-source-coverage.md`](../../../../specs/radar-data-trust-03-source-coverage.md)
**Spec-mãe:** [`../../../../specs/radar-data-trust.md`](../../../../specs/radar-data-trust.md)
**Status:** concluído (RT03-T01 a T07 entregues e auditadas)

## Resultado

Tornar observável o funil de oportunidades: as quatro âncoras conhecidas
(`finep`, `fapesp`, `fapesc`, `web_curated`) e a Descoberta aberta
(`open_search`, `dou`, `hub_expansion`). O resultado é atribuição de novos
candidatos, saúde conservadora dos canais, rendimento editorial por canal e
família de busca, domínios emergentes e painel administrativo de leitura.

Catálogos de atores estão fora deste plano, assim como nova fonte, scraper
automático, eval, gate de recall, mudança de relevância/promoção/gold/RAG ou
promessa de cobertura exaustiva.

## Ordem e dependências

| Task | Plano | Resultado | Depende de |
|---|---|---|---|
| `RT03-T01` | [`channels-query-families.md`](RT03-T01-channels-query-families.md) | contrato de canais e famílias | aprovação da spec |
| `RT03-T02` | [`source-runs-staging-attribution.md`](RT03-T02-source-runs-staging-attribution.md) | tabela única + colunas nullable | T01 |
| `RT03-T03` | [`dedicated-curated-health.md`](RT03-T03-dedicated-curated-health.md) | saúde das fontes dedicadas/Web | T01, T02 |
| `RT03-T04` | [`open-discovery-observability.md`](RT03-T04-open-discovery-observability.md) | atribuição open-search/DOU/hub | T01, T02 |
| `RT03-T05` | [`editorial-funnel-emerging-domains.md`](RT03-T05-editorial-funnel-emerging-domains.md) | métricas, lacunas e domínios | T04 |
| `RT03-T06` | [`admin-api-panel.md`](RT03-T06-admin-api-panel.md) | API e painel somente leitura | T03–T05 |
| `RT03-T07` | [`final-validation.md`](RT03-T07-final-validation.md) | baseline e reconciliação | T01–T06 |

## Ondas seguras e sobreposição

- **Onda A:** T01, depois T02. A migration e a atribuição dependem do vocabulário
  documental estável; não executar em paralelo.
- **Onda B:** T03 e T04 podem avançar em paralelo depois de T02. T03 pousa no
  loop de `src/radar/core/tasks.py`; T04 deve manter a instrumentação na
  Descoberta e preservar sua assinatura. Se o implementador optar por criar o
  `batch_id` no wrapper do cron, aterrar o único bloco de `tasks.py`
  serialmente.
- **Onda C:** T05 consome a atribuição já gravada por T04 e produz somente o
  read model. T06 vem depois de T03–T05 e é o único autor de router, app e UI.
- **Onda D:** T07 fecha testes, relatórios e documentação; não atualizar status
  da spec em tarefas intermediárias.

Pontos de pouso a serializar: `docs/domain/sources/_coverage.md` e
`_discovery.md` (T01); migration/staging (T02); eventual `tasks.py` (T03/T04);
e o contrato do repositório de runs (T02 antes de seus consumidores). T06 é o
único autor de API/frontend. Não incluir catálogos de atores nesta spec.

## Invariantes transversais

- O registry documental é a única lista normativa de canais, e famílias/queries
  continuam configuradas em `_discovery.md`; `open_search` é lógico, não Tavily.
- A única tabela nova é `source_runs`. Staging recebe apenas quatro colunas
  nullable aditivas; linhas legadas, dedup, status e promoção permanecem válidos.
- Telemetria é best-effort. `0` ambíguo não vira sucesso/saúde e denominador
  ausente retorna `null`.
- Não persistir query completa, URL com path/query, conteúdo, traceback, prompt,
  resposta LLM, segredo ou credencial. `origin_domain` é só hostname normalizado.
- Estados são derivados em leitura; domínio emergente é candidato visível, nunca
  fonte/scraper/promoção automática.
- Não acessar produção/rede/LLM durante testes, nem criar eval, threshold, gate,
  backfill fictício, alerta, retry ou ação de operador nova.

## Gate proporcional e relatório

- Por task: testes direcionados, `ruff check` no escopo e `git diff --check`.
- Migration/RLS: somente banco local/fake; reexecução e transições idempotentes.
- Uma fixture por canal relevante basta. Mocks substituem DB, busca, DOU e LLM;
  não carregar `.env` nem executar coleta real.
- T06 inclui `cd frontend && npx tsc --noEmit` e `npm run lint`; T07 roda a suíte
  Python completa e compara falhas com a branch-base.

Cada task entrega um relatório em
`docs/execution/radar-data-trust/reports/03-source-coverage/` com commits,
testes, limitações e confirmação explícita de ambiente hermético. T07 consolida
o `README.md` desse diretório.
