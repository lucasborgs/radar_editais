# RT04-T05 Report

- Base obrigatória: `23a7ef406770c515dad4c2c61c67bc25344e19a8`
- Branch: `codex/radar-data-trust-04-t05`
- Worktree: `/private/tmp/radar-editais-rt04-t05`
- Data: `2026-07-27`

## Escopo implementado

- Adicionado produtor simples de SourceBundle para atores em [gold.py](/private/tmp/radar-editais-rt04-t05/src/radar/core/kg/gold.py).
- ICT EMBRAPII agora gera bundle com:
  - `subject_kind=ict`
  - `subject_id=ict:embrapii:<slug>`
  - documento único `official_record`
  - `source_url` vindo de `record["url"]`
  - `collected_at` vindo de `data_extracao`
- Investidores e programas curados agora geram bundle com:
  - `source=curadoria`
  - IDs preservados (`investidor:<slug>`, `programa:<slug>`)
  - documento único `curated_record`
  - `source_url` vindo de `site` quando presente
  - `collected_at` vindo de `last_updated` do catálogo
- O conteúdo documental do bundle é a serialização determinística do registro já consumido pelo gold:
  - `json.dumps(record, sort_keys=True, ensure_ascii=False)`
- `acquisition_status`:
  - `complete` quando o registro já contém conteúdo substantivo
  - `partial` quando o registro só traz identidade/URL mínima
- Data pura `YYYY-MM-DD` é aceita e normalizada para `00:00 UTC`.
- Datetime sem timezone explícito é rejeitado e não vira `now()`.
- Persistência append-only best-effort roda depois do processamento de cada ator.
- Falha de persistência ou validação do bundle:
  - não bloqueia o gold
  - não incrementa erro de ingestão
  - não vaza a mensagem bruta da exceção
- Agências derivadas por `_get_agency()` ficaram explicitamente sem bundle aplicável:
  - nenhum bundle é fabricado para token derivado
  - o operador continua apenas como metadado/relação existente

## O que foi preservado

- Proveniência EMBRAPII continua ancorada no registro oficial atual.
- Campos copiados de curadoria continuam `unknown`.
- Nenhum `official_page` é criado só por existir URL declarada.
- Nenhum chunk, RAG, descrição sintética, fato novo, relação nova ou LLM foi adicionado para atores.
- `source_bundles.py`, API, frontend, migrations e JSONs de dados não foram alterados.

## Testes adicionados

- Nova suíte: [test_gold_actor_source_bundles.py](/private/tmp/radar-editais-rt04-t05/tests/unit/test_gold_actor_source_bundles.py)

Cobertura validada:

- uma ICT oficial
- um investidor curado
- um programa curado
- ator incompleto (`partial`)
- ausência justificada de bundle de agência
- IDs, papéis, timestamps e hashes determinísticos
- recoleta idempotente
- timestamp ausente, inválido e datetime ingênuo
- data pura normalizada para `00:00 UTC`
- falha best-effort sem bloquear gold ou vazar segredo
- estados de proveniência existentes permanecem iguais

## Checks executados

- `PYTHONPATH=src python3 -m pytest tests/unit/test_gold_actor_source_bundles.py tests/unit/test_gold_provenance_curated.py tests/unit/test_gold_provenance_sources.py tests/unit/test_source_bundles.py tests/unit/test_source_bundles_repo.py`
  - resultado: `151 passed`
- `ruff check src/radar/core/kg/gold.py tests/unit/test_gold_actor_source_bundles.py`
  - resultado: `All checks passed!`
- `git diff --check`
  - resultado: sem erros

## Observação de ambiente

- `ruff check .` no repositório inteiro falhou por um problema preexistente e fora do escopo desta task em `src/radar/domain/__init__.py` (ordenação de imports). A RT04-T05 ficou limpa nos arquivos alterados.
