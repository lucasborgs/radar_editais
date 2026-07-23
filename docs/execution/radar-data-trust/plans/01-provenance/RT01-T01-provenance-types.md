# RT01-T01 — Tipos de proveniência

**Objetivo:** implementar `EvidenceRef`, `FactProvenance`, estados e validação
estrutural sem banco ou mudança produtiva.

## Entrega

- schema versionado;
- compatibilidade com `Extracted.evidence`;
- estados `stated`, `inferred`, `absent`, `conflicting`, `unknown`;
- produtores `adapter`, `deterministic`, `llm`, `human`, `default`, `backfill`.

## Validação

- testes unitários de invariantes e serialização;
- ruff no módulo/testes alterados.

## Pare

Pergunte antes de adicionar score numérico, novo estado factual ou campo que
exponha dado sensível.
