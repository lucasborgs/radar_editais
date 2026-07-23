# RT00-T06 — Métricas diagnósticas

**Objetivo:** medir o classificador sem declarar qualidade não comprovada.

## Escopo

- recall de `in_scope` e falsos negativos;
- precisão de `out_of_scope`;
- taxa de `needs_review`;
- métricas separadas por `kind` e reason code;
- nenhum threshold bloqueante novo.

## Entrega

- suíte/avaliadores no harness existente;
- manifesto e relatório local reproduzível;
- sem harness paralelo.

## Validação

- testes herméticos dos evaluators;
- uma run completa do corpus pequeno quando prereqs estiverem disponíveis.

## Pare

Não transforme o melhor resultado observado em threshold sem decisão explícita
na spec Data Trust 02.
