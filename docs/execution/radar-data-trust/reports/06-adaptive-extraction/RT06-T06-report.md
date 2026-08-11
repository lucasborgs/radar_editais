# RT06-T06 — Convergência dos consumidores

**Status:** `passed`
**Plano:** [RT06-T06](../../plans/06-adaptive-extraction/RT06-T06-consumer-convergence.md)

## Realizado

- O read model filtra sujeito, família inicial e artifact completo/promovido;
  a composição RT04 válida e a projeção corrente RT05 são pré-condições da
  publicação. Sem bundle válido, composição ou revisão corrente, não há claim
  público original.
- A composição ocorre antes da projeção consumida; documentos duplicados por
  conteúdo não são colapsados por hash, e as coordenadas das evidências são
  preservadas.
- Falha da leitura RT05 ou conversão de `corrected_value` estruturado marca
  `needs_review` e não publica o claim original.
- Overrides RT05 são aplicados antes do recálculo dos gaps; uma correção válida
  remove o gap correspondente, enquanto `unknown`, `inferred` e `conflicting`
  continuam bloqueando.
- `absent` explícito atravessa a composição sem virar valor e permanece fora
  dos claims publicáveis.
- Artifacts e revisões possuem leitores em lote para evitar N+1 por card.
- `RelationalKnowledge` usa esses leitores em lote ao listar cards.

## Limitações e escopo

- T07 continua não promovida: gold/Knowledge/caminhos/Writing ainda não usam o
  read model como autoridade produtiva.
- A entrada real de `channel` não está disponível no corpus/runtime atual; a
  cobertura global de RT06 para `channel` permanece pendente e não é declarada.
- Persistência Postgres local e goldens humanos/versionados ainda são necessários
  para validar a promoção.

## Validação

| Verificação | Resultado |
|---|---|
| Read model, RT04 e filtros de família/estado | passou |
| RT04 conflictivo seguido de correção RT05 e recálculo de gaps | passou |
| Ausência explícita preservada sem valor fabricado | passou |
| Wiring shadow com flag e injeção | passou |
