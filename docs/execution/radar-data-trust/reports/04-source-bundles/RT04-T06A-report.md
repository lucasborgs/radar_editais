# RT04-T06A Report

- Base: `989199796fbfa1b3b10a1ab0c91a34484e7fa7aa`
- Branch: `codex/radar-data-trust-04-t06a`
- Worktree: `/private/tmp/radar-editais-rt04-t06a`

## Arquivos alterados

- [source_bundle_projection.py](/private/tmp/radar-editais-rt04-t06a/src/radar/core/kg/source_bundle_projection.py)
- [test_source_bundle_projection.py](/private/tmp/radar-editais-rt04-t06a/tests/unit/test_source_bundle_projection.py)

## Read model minimo escolhido

- `BundleProjection`
  - projeção corrente pura de um `SourceBundle complete`
  - remove apenas documentos `superseded`
  - preserva `active` e `contextual`
- `ExplicitClaim`
  - mínimo obrigatório: `field`, `value`, `content_hash`
  - adicional mínimo para emenda conservadora: `supersedes_content_hash`
  - esse campo extra não altera persistência; existe só no read model da composição
- `FieldResolution`
  - `state`
  - `value`
  - `evidence_refs`
  - `limitations`

## Matriz exata de precedencia implementada

- `amendment` vence somente quando:
  - o claim vencedor traz `supersedes_content_hash`
  - esse hash aponta para o documento perdedor
  - o documento `amendment` também traz `amends_content_hash` para o mesmo documento
- `opportunity_page` vence `program_page` no mesmo campo
- qualquer papel oficial do bundle vence `curated_record` no mesmo campo
- valores iguais nunca conflitam; as evidências são agregadas
- nenhuma precedência é inferida de:
  - `published_at`
  - `doc_name`
  - `composition_order`
  - posição do documento no bundle

## Comportamento de conflicting

- se houver valores incompatíveis sustentados por documentos autorizados
- e não existir uma precedência explícita e confiável
- o resultado é `FactState.CONFLICTING`
- nesse caso o read model preserva as evidências de todos os documentos envolvidos

## Comportamento sem claims

- sem claims explícitos para um campo:
  - `FactState.UNKNOWN`
  - `value=None`
  - sem conflito fabricado
  - limitação registrada em `limitations`

## Limitacao do consolidado

- consolidado permanece não implementável na T06-A
- nenhum papel, nome, ordem, URL ou data foi usado para inferir documento consolidado
- depende de marcador explícito do produtor

## Testes e resultados reais

- `ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit/test_source_bundles.py tests/unit/test_source_bundles_repo.py tests/unit/test_source_bundle_projection.py`
  - resultado: `116 passed in 0.41s`
- `/Users/lucasborges/radar_editais/.venv/bin/ruff check src/radar/core/kg/source_bundle_projection.py tests/unit/test_source_bundle_projection.py`
  - resultado: `All checks passed!`
- `git diff --check`
  - resultado: sem erros

## Divergencias

- nenhuma divergência material entre plano e código real foi encontrada

## Auditoria

**Auditoria Codex: aprovada em 2026-07-27.**

- A projeção é pura e não altera o histórico persistido.
- Claims ausentes ou sem documento autorizado não fabricam fatos.
- As três precedências implementadas ficam restritas ao mesmo campo.
- Conflito exige valores incompatíveis sustentados e ausência de precedência.
- Nenhuma autoridade foi inferida de data, nome ou ordem incidental.
- A ausência de marcador de consolidado foi preservada como limitação.
- Gate independente: `116 passed`, Ruff limpo e `git diff --check` limpo.
