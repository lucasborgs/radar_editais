# RT06-T02 — Rota textual em shadow

**Status:** `passed`
**Plano:** [RT06-T02](../../plans/06-adaptive-extraction/RT06-T02-textual-route-production.md)

## Realizado

- A rota textual canônica é dedicada à família inicial, usa a factory LLM e
  retorna envelopes próprios por campo (`value`, `state`, `evidence`), com
  constraints `{tipo, op, valor}`. O extrator legado não é chamado nem
  adaptado.
- A rota textual é executada por documento, não por edital agregado.
- O shadow só é chamado com `RADAR_ADAPTIVE_EXTRACTION_SHADOW=1` (ou valor
  booleano equivalente) e persistência durável configurada, salvo seam injetado
  em teste.
- O gate incremental é avaliado antes do shadow; cache saudável não chama LLM.

## Limitações

- O produto legado continua sendo o consumidor do gold; T07 não foi promovida.

## Validação

| Verificação | Resultado |
|---|---|
| Wiring off/on com factory injetada | passou |
| Rota textual com fixtures locais | passou |
| Cliente LLM fakeado somente na fronteira e payload efetivo validado | passou |
