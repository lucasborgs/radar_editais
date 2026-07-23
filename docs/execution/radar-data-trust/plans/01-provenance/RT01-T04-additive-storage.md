# RT01-T04 — Persistência aditiva

**Objetivo:** preparar storage para dual-write sem novo consumidor.

## Entrega

- `entities.provenance` e `entity_relationships.provenance` JSONB default-safe;
- coordenadas aditivas de `match_chunks`;
- upsert/round-trip compatível;
- registros anteriores identificáveis como legado.

## Validação

- migration local e reexecução idempotente;
- RLS/policies preservadas;
- testes direcionados de persistência;
- projeção T02 idêntica sem campos novos.

## Pare

Pare diante de migration destrutiva, alteração de RLS ou mudança de significado
de coluna existente.
