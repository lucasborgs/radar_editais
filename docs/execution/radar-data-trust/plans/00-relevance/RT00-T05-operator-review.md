# RT00-T05 — Revisão do operador

**Objetivo:** mostrar decisão, razões e lacunas sem redesenhar a Descoberta.

## Escopo

- API administrativa compatível;
- estado e reason codes em progressive disclosure;
- `needs_review` com informação faltante explícita;
- atores permanecem em fluxo próprio; não são disfarçados de oportunidade.

## Entrega

- payload tipado;
- ajuste mínimo da fila administrativa;
- estados vazio, erro e legado.

## Validação

- testes de contrato da API;
- lint/typecheck frontend e QA manual direcionado se a UI for tocada.

## Pare

Pergunte antes de introduzir nova ação editorial, papel de usuário ou mudança de
quem pode promover/rejeitar.
