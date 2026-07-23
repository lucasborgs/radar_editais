# RT00-T04 — Contrato de staging

**Objetivo:** persistir classificação e justificativa sem alterar o gate humano.

## Escopo

- migration aditiva/default-safe quando necessária;
- decisão, reason codes, versão, evidência e informações faltantes;
- legado aparece como `unclassified`;
- cache negativo continua por candidato/versão, nunca por instituição.

## Entrega

- migration e acesso idempotente;
- dual-write compatível;
- testes de promoção/rejeição existentes preservados.

## Validação

- migration local, rollback lógico e RLS/service-role;
- testes direcionados de staging e promotion runs.

## Pare

Pare diante de reclassificação automática de decisões humanas, perda de
histórico ou necessidade de migration destrutiva.
