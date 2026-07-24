# RT01-T06 — FAPESP, FAPESC e Web no mesmo contrato de proveniência

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T06-other-opportunity-sources.md`](../../plans/01-provenance/RT01-T06-other-opportunity-sources.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t06` / base `a1d16a4a2`
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

Removido o gate `if src == "finep"` que isolava a escrita de proveniência ao
edital FINEP, para que os mesmos blocos de código de proveniência
(`build_edital_fact_provenance`, `chunk_storage_coords`,
`build_operado_por_provenance`, `build_subordinado_a_provenance`) executem
para TODAS as 4 fontes de edital (finep, fapesp, fapesc, web).

### Arquivos modificados

**`src/radar/core/kg/gold.py`** — única modificação: remoção do
`if src == "finep":` guard nas linhas 907–930, substituído por execução
incondicional do bloco de proveniência para todos os editais. Atualizado
comentário do bloco para refletir que a proveniência agora vale para todas as
fontes.

**`tests/unit/test_gold_provenance_sources.py`** (NOVO) — 10 testes
herméticos (sem rede/banco, stubs do harness T02) provando:

| Teste | O que prova |
|---|---|
| `test_fapesp_has_provenance` | fapesp edital tem provenance com paths `status`, `setores`, `tecnologias_tags` |
| `test_fapesc_has_provenance` | fapesc edital tem provenance análoga |
| `test_web_has_provenance` | web edital tem provenance |
| `test_finep_still_has_provenance` | finep continua com provenance |
| `test_non_edital_entities_still_empty` | entidades não-edital continuam com provenance vazia |
| `test_web_has_no_operado_por_edge` | web NÃO cria aresta operado_por (`_SOURCE_AGENCY` retorna None) |
| `test_new_edges_have_provenance` | arestas fapesp/fapesc operado_por têm provenance válida |
| `test_chunk_coords_present_for_edital_sources` | editais de todas as fontes têm `source_hash` nos chunks |
| `test_no_stated_without_evidence_ref` | nenhum `stated` sem EvidenceRef ou com locator não exato |
| `test_two_captures_deterministic` | capturas repetidas produzem o mesmo resultado |

### Design decisions

- **Nenhum builder novo**: reusa exatamente os mesmos builders da T05
- **Web sem operado_por**: `_SOURCE_AGENCY` não tem entrada para `"web"` →
  `_get_agency` não é chamado → edge não criada (comportamento pré-T06 mantido)
- **Web page=1**: o structurer emite page=1 para blocos web; `chunk_storage_coords`
  lê `src_page` do chunk e persiste como está — sem fabricação
- **Proveniência não usada por consumidor novo**: estritamente aditiva
  (colunas `provenance::jsonb` já existiam na migration 036)

## Divergências e decisões

| Item | Decisão |
|---|---|
| T05 `test_non_finep_entities_have_empty_provenance` quebra | Esperado — T06 muda intencionalmente o comportamento que aquele teste verificava. O T05 testava que fapesp/fapesc/web editais tinham provenance vazia; agora elas têm. A governança deve decidir se o T05 test deve ser atualizado ou arquivado. |
| `test_chunk_coords_present_for_all_sources` original do plano era muito amplo (validava source_hash em chunks de **programas**, que não recebem source_hash do provenance_writer) | Restrito a `kind == "edital"` no teste final — programas continuam sem source_hash (design original). |
| Removido comentário de emenda da governança T05 | O comentário mencionava "SOMENTE finep grava" — substituído por comentário atualizado. |

## Dados e migrations

Nenhuma migration nova. Nenhuma fixture nova. Nenhum dado real tocado.
As colunas `provenance::jsonb` em `entities` e `entity_relationships`, e as
colunas `document`/`page`/`silver_block_idx`/`source_hash` em `match_chunks`,
já existiam (migration 036, T05).

## Validação

### Testes novos (10/10 passam)

```
$ PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_gold_provenance_sources.py -v
============================= test session starts ==============================
...
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_fapesp_has_provenance PASSED [ 10%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_fapesc_has_provenance PASSED [ 20%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_web_has_provenance PASSED [ 30%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_finep_still_has_provenance PASSED [ 40%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_non_edital_entities_still_empty PASSED [ 50%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_web_has_no_operado_por_edge PASSED [ 60%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_new_edges_have_provenance PASSED [ 70%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_chunk_coords_present_for_edital_sources PASSED [ 80%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_no_stated_without_evidence_ref PASSED [ 90%]
tests/unit/test_gold_provenance_sources.py::TestSourceProvenance::test_two_captures_deterministic PASSED [100%]
============================== 10 passed in 0.97s ==============================
```

### Gate T02 — baseline equivalence (16/16 passam)

```
$ PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_gold_equivalence.py
................                                                         [100%]
16 passed in 0.70s
```

### Suíte T05 (builder + dualwrite + mappers) — 83 passed, 1 expected failure

```
$ PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/test_provenance.py tests/unit/test_evidence_resolver.py tests/unit/test_gold_provenance_dualwrite.py tests/unit/test_gold_mappers.py -x
........................................................................ [ 59%]
...........F
=================================== FAILURES ===================================
_ TestHarnessCapturedProvenance.test_non_finep_entities_have_empty_provenance __
...
E           AssertionError: edital|fapesp|fapesp:16466 deveria ter provenance vazia
...
1 failed, 83 passed in 0.82s
```

Falha exclusivamente no teste que **verificava o comportamento pré-T06**
(fapesp/fapesc/web com provenance vazia). Todos os 83 testes de builder,
resolver, mappers, composition e chunk coords continuam passando.

### Ruff — clean

```
$ PYTHONPATH=src .venv/bin/python -m ruff check src/radar/core/kg/gold.py tests/unit/test_gold_provenance_sources.py
All checks passed!
```

### git diff --check

```
$ git diff --check
(sem output — sem erros de whitespace)
```

### git diff --stat (contra a1d16a4a2)

```
$ git diff a1d16a4a2 --stat
 src/radar/core/kg/gold.py | 48 ++++++++++++++++++++---------------------------
 1 file changed, 20 insertions(+), 28 deletions(-)
```

Arquivo novo (não rastreado): `tests/unit/test_gold_provenance_sources.py`

## Pendências

Nenhuma. Implementação mínima completa.

## Auditoria Codex

- **Arquivos tocados**: `src/radar/core/kg/gold.py` (modificado),
  `tests/unit/test_gold_provenance_sources.py` (novo)
- **Gates intocáveis**: `tests/helpers/gold_projection.py` não modificado;
  `tests/unit/test_gold_equivalence.py` passa (16/16); `equivalence.py`
  inalterado; `baseline_projection.json` não regenerado
- **Sem alteração de**: prompts LLM, migrations, RLS, score de confiança,
  estado factual novo, consumidor lendo provenance, banco remoto/rede/LLM real
- **Hashes**: prefixo `md5:` real, nunca re-hasheados para simular outro
  algoritmo
- **Proibições respeitadas**: sem fabricação de coordenada/página/hash;
  sem `stated` sem EvidenceRef; sem overengineering

**Veredito:** pendente
