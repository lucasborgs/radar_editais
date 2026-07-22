# RT01-T12 — Backfill amostral e shadow metrics

**Objetivo:** medir cobertura do legado sem prometer backfill total no pré-beta.

## Entrega

- backfill idempotente/reiniciável apenas para registros resolvíveis;
- amostra por origem;
- relatório de `exact`, `document_only`, `unresolved` e `legacy`;
- equivalência T02 executada após o backfill.

## Validação

- dry-run e execução em ambiente local/staging autorizado;
- nenhuma evidência fabricada a partir do valor gold;
- reexecução não duplica nem degrada estado melhor.

## Pare

Não execute backfill irreversível ou remoto sem autorização explícita e backup
ou estratégia de reversão validada.
