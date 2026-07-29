# RT05-T08 — Comunicação de validade nos consumidores

**Data:** 2026-07-29
**Base:** `768e5d5aa`
**Branch:** `codex/radar-data-trust-05-t08`
**Worktree:** `/private/tmp/radar-editais-rt05-t08`
**Commit funcional:** `2a3bcdc02`
Auditoria Codex: pendente

## Superfícies alteradas

- Frontend do Ecossistema: `frontend/src/app/oportunidades/page.tsx`,
  `frontend/src/app/oportunidades/[id]/page.tsx`
- Frontend do Radar/Explorar: `frontend/src/app/radar/page.tsx`,
  `frontend/src/components/frontdoor/MatchedEditalCard.tsx`,
  `frontend/src/lib/radar-utils.ts`,
  `frontend/src/lib/opportunity-temporal.ts`
- Tipos públicos do frontend: `frontend/src/lib/api.ts`,
  `frontend/src/types/edital.ts`, `frontend/src/types/oportunidade.ts`
- Contexto de Explorar e Escrita: `src/radar/core/llm/agent_tools/explore_tools.py`,
  `src/radar/core/kg/temporal.py`, `src/radar/core/kg/planning_node.py`,
  `src/radar/core/services/writing_session.py`
- Aplicações: `src/radar/api/routers/applications.py`,
  `src/radar/core/services/temporal_read_model.py`

## Mapeamento final dos estados para texto visível

| Estado canônico | Texto / comportamento final |
|---|---|
| `active` + `fixed` | status aberto e prazo datado normal |
| `active` + `continuous` | **Fluxo contínuo** / **Fluxo contínuo confirmado** |
| `closed` | **Encerrada**; não aparece como aberta |
| `needs_review` | **Validade a confirmar**; sem prazo inventado e sem fluxo contínuo |
| payload ausente/legado | degrada para **Validade a confirmar** ou mantém encerrada quando o bruto já é fechado; nunca cria aberto/contínuo por ausência |

## Comportamento final por superfície

### Ecossistema

- Lista e ficha usam o payload temporal canônico para status e prazo.
- `needs_review` aparece como item consultável com **Validade a confirmar**.
- A ficha detalhada mostra apenas metadados seguros de verificação:
  `decision_source` e `last_verified_at`.
- Investidores continuam sem payload temporal.

### Radar

- O frontend não reimplementa o filtro ativo do read model.
- O texto de prazo deixou de tratar `prazo` ausente como contínuo.
- O filtro “Contínuo / sem prazo” virou apenas **Fluxo contínuo** e depende de
  `temporal_mode=continuous`.
- Payload legado/ausente cai em mensagem conservadora (**Prazo a confirmar** ou
  **Validade a confirmar**) sem quebrar os cards.

### Explorar

- `list_editais`, `get_edital` e `explore_opportunity` passaram a exibir
  **Validade a confirmar** quando `validity_state=needs_review`.
- O agente não recebe prazo inventado nem “aberto” para casos incertos.
- A saída textual só expõe origem e data seguras da última verificação.

### Escrita

- O bloco temporal canônico agora avisa explicitamente quando a validade está a
  confirmar ou quando o item deve ser tratado como encerrado.
- O `CARD DA FONTE` e o contexto de planning repetem a mesma semântica, sem
  prompt decisório paralelo.
- Fluxo contínuo só é afirmado quando já veio confirmado pelo payload.

## Legado, ausência e ausência de duplicação de regra

- Não há regra de liveness ou de fluxo contínuo em JavaScript.
- O helper novo de frontend apenas traduz `validity_state`/`temporal_mode` para
  texto e calcula urgência de prazo fixo.
- Ausência de `deadline` ou de payload nunca vira “Fluxo contínuo”.
- O fallback de legado só preserva a tela utilizável; ele não reabre
  oportunidades nem concede vigência.

## Correção de `days_left`

- `days_left` passou a usar `today_sao_paulo()` exportado pelo read model
  temporal canônico.
- O cálculo continua derivado do prazo do card canônico, mas respeita o mesmo
  dia civil do produto em `America/Sao_Paulo`, inclusive na fronteira UTC.

## Testes e resultados reais

- `ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit/test_temporal_read_model.py tests/unit/test_temporal_consumers_batch.py tests/unit/test_match_v3.py tests/unit/test_temporal.py tests/unit/test_applications_pipeline.py tests/unit/test_explore_agent.py tests/unit/test_radar_router.py tests/integration/test_entity_catalog.py`
  → `88 passed, 5 skipped`
- `/Users/lucasborges/radar_editais/.venv/bin/ruff check ...`
  → aprovado

Contratos cobertos:

- Finep/Eureka e demais `needs_review` aparecem como **Validade a confirmar**
  nos consumidores textuais;
- `needs_review` e `closed` continuam fora do Radar ativo;
- prazo ausente não gera fluxo contínuo;
- continuidade explicitamente confirmada continua visível;
- Escrita e Explorar recebem aviso conservador;
- investidores seguem sem temporalidade;
- `days_left` usa o dia de São Paulo.

## Warnings preexistentes

- O worktree não contém `frontend/node_modules`.
- `cd frontend && npx tsc --noEmit` não ficou executável localmente sem as
  dependências instaladas.
- `cd frontend && npm run lint` falha com `sh: next: command not found`.

Esses warnings são de ambiente local do worktree, não de uma alteração da T08.

## Limitações e QA manual

- Sem `node_modules`, a validação do frontend ficou restrita à revisão do diff e
  à tipagem estrutural dos payloads alterados.
- QA manual sugerido após instalar dependências: conferir `/oportunidades`,
  `/oportunidades/{id}`, `/radar` e a sessão de escrita para um caso
  `needs_review`, um `continuous/active` confirmado e um investidor.

## Fora de escopo preservado

- Nenhum endpoint, migration, worker, cron, ranking, match, extração,
  promoção/rejeição ou T09 foi iniciado.
- Não houve fetch adicional, backfill, LLM nova nem redesign geral.
