# RT04-T06-B Report

- Base: `7c1912db2c0f2b3a7664ce34ff42e8c76c2faacf`
- Branch: `codex/radar-data-trust-04-t06b`
- Worktree: `/private/tmp/radar-editais-rt04-t06b`
- Commits:
  - implementação inicial: `9cf4d428c`
  - relatório inicial: `56e38a00f`
  - implementação de correção: `fccf8fc2c`
  - relatório final: `<pending>`
- Auditoria Codex: pendente

## Escopo

Implementação exclusiva da RT04-T06-B para ligar fatos, relações e chunks à versão documental efetivamente recuperável, sem migration, sem UUID adicional de `source_bundles`, sem rede e sem alterar a composição/precedência da T06-A.

## Decisão de contrato

- `EvidenceRef` recebeu os campos aditivos `bundle_hash` e `content_hash`.
- Os dois campos são opcionais, mas só aparecem juntos.
- O formato aceito é `sha256:<64 hex>`.
- A referência estável adotada foi:
  - `bundle_hash = SourceBundle.compute_bundle_hash()`
  - `content_hash = hash SHA-256 do documento dentro do bundle`
- `source_bundle_id` permaneceu ausente. Não foi criada migration nem nova consulta para recuperar UUID do repositório.

## Helper puro

Foi adicionado um helper mínimo em [`src/radar/core/kg/source_bundle_projection.py`](/private/tmp/radar-editais-rt04-t06b/src/radar/core/kg/source_bundle_projection.py):

- `attach_bundle_lineage(ref, bundle, document)`
- valida que o documento pertence ao bundle corrente;
- rejeita documento ausente, ambíguo ou superseded;
- enriquece apenas a `EvidenceRef`, sem copiar o documento inteiro para a proveniência.
- `attach_bundle_metadata_to_documents(documents, bundle)`
- consolida a projeção documental reutilizada por Web e FAPESC;
- só anexa metadados quando o bundle corrente é `complete`.

## Produtores ligados

### T03 Web

- [`src/radar/core/services/discovery_materializer.py`](/private/tmp/radar-editais-rt04-t06b/src/radar/core/services/discovery_materializer.py)
- promoção continua best-effort;
- `source_docs` recebe `bundle_hash` e `content_hash` apenas quando `source_bundles.save(bundle) == True`;
- bundle parcial continua projetável sem fabricar documento ausente;
- falha ou `save=False` preserva a projeção legada.

### T04 FAPESC

- [`src/radar/core/tasks.py`](/private/tmp/radar-editais-rt04-t06b/src/radar/core/tasks.py)
- `_save_fapesc_bundle_if_available()` passou a retornar um `SourceBundle` somente quando a persistência foi confirmada e o bundle é `complete`;
- falhas esperadas (`ValidationError`, `BundleStorageError`) ficam isoladas e sanitizadas;
- `source_docs.save()` continua rodando mesmo sem bundle persistido;
- o fallback existente foi preservado.

### T05 Atores

- [`src/radar/core/kg/gold.py`](/private/tmp/radar-editais-rt04-t06b/src/radar/core/kg/gold.py)
- [`src/radar/core/kg/provenance_writer.py`](/private/tmp/radar-editais-rt04-t06b/src/radar/core/kg/provenance_writer.py)
- ICT EMBRAPII: âncora oficial enriquecida quando o bundle oficial foi persistido;
- investidor/programa curados: âncora curada enriquecida quando o bundle curado foi persistido;
- bundle `partial` continua sendo persistido, mas não é promovido a linhagem corrente;
- estados factuais existentes foram preservados:
  - ICT continua `stated` onde já era `stated`;
  - investidor/programa continuam `unknown` nos campos copiados;
  - agência derivada continua sem bundle fabricado.

### Oportunidades no gold

- [`src/radar/core/kg/evidence_resolver.py`](/private/tmp/radar-editais-rt04-t06b/src/radar/core/kg/evidence_resolver.py)
- as `EvidenceRef` de `requisitos_texto` agora herdam `bundle_hash` e `content_hash` quando:
  - os blocos silver carregam `document_metadata`;
  - a resolução documental é `exact` ou `document_only`;
  - a linhagem documental é única e inequívoca entre os blocos candidatos.
- quando os blocos têm metadados conflitantes, ausentes ou ambíguos, a evidência permanece legada.

## Chunks

- [`src/radar/core/tasks.py`](/private/tmp/radar-editais-rt04-t06b/src/radar/core/tasks.py)
- chunks agora recebem `bundle_hash` e `content_hash` apenas quando:
  - o documento canônico carregado já traz esses hashes;
  - existe exatamente um `doc_name` correspondente no conjunto ativo;
  - não há ambiguidade documental.
- Quando a correspondência não é inequívoca, o metadata legado é preservado.
- O marcador de reindexação do chunking foi mantido compatível via `index_content_hash`, com fallback de leitura para o campo legado `content_hash`.
- `match_chunks` do gold permanecem legados: o schema atual não tem colunas para `bundle_hash`/`content_hash`, e a task proibiu migrations. A linhagem documental nova sobe até `EvidenceRef` de oportunidades e até `edital_chunks`, mas a persistência de `match_chunks` fica explicitamente adiada.

## Compatibilidade legada

- payloads antigos de `EvidenceRef` continuam válidos;
- `canonical_content_hash` e `silver_source_hash` não foram reinterpretados;
- ausência de bundle continua válida;
- nenhum backfill foi executado;
- nenhuma tabela, migration, API ou frontend foi alterado.

## Limitações

- Não foi criado caminho para recuperar UUID de `source_bundles`.
- A ligação de chunk depende de identidade documental já preservada no pipeline; quando há ambiguidade, o chunk permanece legado.
- Não houve tentativa de ligar agência derivada a bundle, porque `_get_agency()` não consome um registro documental próprio.

## Validação executada

- `ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit/test_provenance.py tests/unit/test_source_bundles.py tests/unit/test_source_bundles_repo.py tests/unit/test_source_bundle_projection.py tests/unit/test_gold_actor_source_bundles.py tests/unit/test_rt04_t03b_materialization.py tests/unit/test_fapesc_source_bundle.py tests/unit/test_chunk_lineage.py tests/unit/test_chunk_edital_gate.py tests/unit/test_gold_provenance_icts.py tests/unit/test_gold_provenance_curated.py tests/unit/test_gold_provenance_dualwrite.py tests/unit/test_gold_provenance_sources.py`
  - resultado: `309 passed`
- `ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/python -m radar.core.eval run provenance`
  - resultado: suíte local executada com sucesso; arquivo gerado em `/private/tmp/radar-editais-rt04-t06b/eval_results/20260727_172321_provenance.json`
- `ruff check <arquivos alterados>`
  - resultado: `All checks passed!`
- `git diff --check`
  - resultado: sem diferenças inválidas
