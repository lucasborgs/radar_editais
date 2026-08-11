# RT06-T03 — Claims, evidência e RT05

**Status:** `passed`
**Plano:** [RT06-T03](../../plans/06-adaptive-extraction/RT06-T03-claims-evidence-exceptions.md)

## Realizado

- A família inicial aceita somente `{tipo, op, valor}` completo, usando os
  accessors do schema autoritativo e falhando fechado quando o schema não
  carrega.
- O formato legado `{type, description}` é rejeitado; enums, operadores por
  tipo e valores numéricos são validados.
- Somente `stated` com evidência resolvida pode alimentar uma projeção pública.
  `absent` explícito é preservado como ausência conhecida, sem valor publicável;
  `inferred`, `unknown` e `conflicting` continuam lacunas.
- Requisitos, exclusões e público não são derivados de campos de contexto sem
  estado/evidência próprios.
- Conflitos sem precedência são compostos pela RT04 e chegam ao estado
  `conflicting`, com observação RT05 best-effort.
- A ordem efetiva é claims por documento → composição/precedência RT04 →
  conflitos → projeção corrente RT05 → read model. Bundle ausente, composição
  vazia ou exceção aberta mantêm o campo em lacuna/`unknown`.

## Validação

| Verificação | Resultado |
|---|---|
| Legacy/canonical constraints, schema indisponível e estados não-stated | passou |
| RT04 com dois documentos conflitantes | passou |
| Falha de projeção RT05 e correção estruturada inválida | passou |
| Múltiplas exceções do mesmo campo, incluindo uma aberta | passou |
| Omissão versus `absent`, e composição de ausência sem valor fabricado | passou |
