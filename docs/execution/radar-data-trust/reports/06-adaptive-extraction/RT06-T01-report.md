# RT06-T01 — Contratos e artifact

**Status:** `passed`
**Plano:** [RT06-T01](../../plans/06-adaptive-extraction/RT06-T01-contracts-and-artifact.md)

## Realizado

- Mantido `DocumentAsset`/`ExtractionArtifact` como contrato único, com
  `asset_hash`, `bundle_hash`, documento e papel documental.
- A persistência confirmada é obrigatória para qualquer artifact retornado;
  `save() == False` ou confirmação ausente da própria tentativa falha fechado;
  uma tentativa saudável perdedora retorna o resultado canônico concorrente.
- Tentativas `failed`/`unavailable` são append-only, carregadas como diagnóstico
  e não entram no cache saudável. O retry usa `attempt_id` separado do
  fingerprint material.
- Resultados `complete`/`partial` possuem unicidade parcial por fingerprint;
  a tentativa perdedora retorna o resultado saudável canônico, enquanto falhas
  continuam confirmadas pela identidade própria da tentativa.

## Validação

| Verificação | Resultado |
|---|---|
| Testes focais de contratos, persistência e retry | passou |
| Artifact separado por documento e hashes preservados | passou |
| Falha/unavailable preservados e retry versionado | passou |
| Corrida de attempts saudáveis e confirmação de falha concorrente | passou em memória |

## Pendências

- A confirmação real contra Postgres local continua pendente; a migration de
  tentativas e o índice parcial estão preparados, mas T07 não foi promovida.

**Veredito:** condicionado à validação posterior com Postgres local.
