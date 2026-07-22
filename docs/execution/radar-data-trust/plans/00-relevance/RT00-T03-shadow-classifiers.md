# RT00-T03 — Classificadores em shadow

**Objetivo:** aplicar o contrato novo sem mudar promoção, rejeição ou gold.

## Escopo

- adaptar a triagem de oportunidades para três estados;
- criar avaliadores separados por `kind` para atores;
- representar critérios de ator comprovadamente falsos em `failed_codes`,
  separados de critérios satisfeitos e de informação ausente;
- proibir prompt genérico único para todos os tipos;
- falha externa continua erro, nunca `out_of_scope`.

## Entrega

- saída shadow versionada;
- comparação com labels da T02;
- divergências registradas, sem enforcement.

## Validação

- testes direcionados de parser/fallback;
- testes das invariantes `reason_codes` / `failed_codes` /
  `missing_information` para cada `kind`;
- run diagnóstica nos casos da T02 quando houver credencial autorizada.

## Pare

Pare se a mudança elevar falsos negativos de oportunidades ou se um critério de
ator depender de decisão de produto não coberta pela spec.
