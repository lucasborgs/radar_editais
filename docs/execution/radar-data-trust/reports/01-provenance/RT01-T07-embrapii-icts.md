# RT01-T07 — ICTs EMBRAPII com proveniência própria

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T07-embrapii-icts.md`](../../plans/01-provenance/RT01-T07-embrapii-icts.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t07` / base `5017b052a`
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

Proveniência dual-write para `_ingest_icts` (kind `ict`, fonte EMBRAPII),
seguindo o contrato §6.4 da spec: a evidência legítima desta origem é o
REGISTRO versionado do scraper (`embrapii_*.json`), não um documento
paginado — não há chunks/RAG para ICTs (spec §3.4).

### Âncora do registro

`provenance_writer.build_ict_record_anchor` constrói um `EvidenceRef`
`locator_quality=document_only` por ICT:

- `document` = nome do arquivo `embrapii_*.json` usado (`files[-1].name`);
- `canonical_content_hash` = `"md5:<hash>"` do JSON **do registro
  individual** (`json.dumps(record, sort_keys=True, ensure_ascii=False)`) —
  **não** do arquivo inteiro, para que cada ICT do mesmo arquivo versionado
  tenha hash próprio (testado: `test_hash_covers_only_the_individual_record_not_the_whole_file`);
- `source_url` = `record["url"]` (a URL oficial da própria unidade —
  confirmado lendo `pipeline/extractors/ict_embrapii.py`, onde cada unidade
  carrega seu próprio `url`);
- sem `quote` (identidade de registro estruturado, não citação verbatim);
- `source="embrapii"`.

A âncora é construída UMA vez em `_ingest_icts` e reusada em `name`,
`metadata.url` (mesmo registro, mesma âncora) e na aresta `credenciada_por`
— nunca recomputada, evitando hashear o mesmo registro múltiplas vezes.

### Tabela de fatos implementada (builders novos em `provenance_writer.py`)

| Path | state | producer | Evidência |
|---|---|---|---|
| `name`, `metadata.url` | `stated` | `adapter` (`embrapii_scraper`) | âncora do registro |
| `uf` (só quando `_uf_from_address` derivou) | `inferred` | `deterministic` (`_uf_from_address`) | rule `_uf_from_address:v1`, inputs `["record.address"]`, sem refs |
| `setores`, `tecnologias_tags` (só quando `areas_raw` não vazio) | `inferred` | `deterministic` (`normalize_tags`) | rule `normalize_tags:v1`, inputs `["record.areas_raw"]`, sem refs |
| aresta `credenciada_por` | `stated` | `adapter` (`embrapii_scraper`) | MESMA âncora do registro |

Campo sem valor no registro não recebe entrada no dict (`build_ict_fact_provenance`
omite o path — testado com registro vazio e com campos faltando um a um).
Nenhum `unknown` fabricado.

### Arquivos modificados

**`src/radar/core/kg/provenance_writer.py`** — seção nova ao final do
módulo (antes do `__all__`), sem tocar nos builders FINEP/T05/T06
existentes: `ICT_SCRAPER_PRODUCER_NAME`, `build_ict_record_anchor`,
`build_ict_identity_provenance`, `build_ict_uf_provenance`,
`build_ict_tags_provenance`, `build_ict_credenciada_por_provenance`,
`build_ict_fact_provenance` (composição). Import de `hashlib`/`json`
adicionado ao topo (infraestrutura, não builder).

**`src/radar/core/kg/gold.py`** — única função tocada: `_ingest_icts`.
Constrói a âncora e o dict de provenance via os builders novos antes da
transação; passa `provenance=` para `_upsert_entity` e para o `_upsert_rel`
da aresta `credenciada_por` (dentro do `if aid:` existente — comportamento
"só grava a aresta quando a agência resolve" preservado). Também atualizada
a docstring de `_upsert_rel` (estava desatualizada dizendo que
`_ingest_icts` "chama sem o kwarg... até T07/T08" — agora reflete que T07
passa `provenance` na aresta; só `_ingest_programas` segue sem o kwarg até
T08). `_ingest_investidores`, `_ingest_programas` e `_get_agency`
(escopo da T08) **não foram tocados**.

**`tests/unit/test_gold_provenance_sources.py`** — `test_non_edital_entities_still_empty`
atualizado minimamente: exclui `kind == "ict"` (agora tem provenance
não-vazia, afirmada em `test_gold_provenance_icts.py`); `investidor`/
`programa`/`agencia` continuam verificados vazios.

**`tests/unit/test_gold_provenance_dualwrite.py`** — `test_actor_edges_have_empty_provenance_until_t07_t08`
renomeado para `test_programa_edges_have_empty_provenance_until_t08` e
restrito a arestas `programa|*` (a aresta `credenciada_por` de ICT deixou de
estar vazia com esta task — movida para `test_gold_provenance_icts.py`).
Docstring do módulo atualizada (já estava parcialmente desatualizada desde
T06 quanto a fapesp/fapesc/web; corrigida a menção a `ict` como parte do
escopo desta task e deixada nota sobre as demais origens).

**`tests/unit/test_gold_provenance_icts.py`** (novo, 20 testes) — três
blocos: (a) builders isolados (âncora, tabela de fatos, composição —
inclusive adversarial "campo sem valor não vira entrada"); (b) captura via
harness T02 (ICT do fixture `embrapii_fixture.json`/CEIA-UFG com os 5 paths
esperados; aresta `credenciada_por` stated com a âncora; não-regressão de
editais T06; investidor/programa/agência seguem vazios); (c) adversarial
(nenhum `stated` sem `EvidenceRef` exact/document_only, em entidades E
relações) + determinismo (2 capturas idênticas).

## Divergências e decisões

| Item | Decisão |
|---|---|
| `test_actor_edges_have_empty_provenance_until_t07_t08` (em `test_gold_provenance_dualwrite.py`) não estava na lista de arquivos a modificar da task, mas quebra como consequência direta e inevitável de implementar `credenciada_por` corretamente (o próprio nome do teste já previa "until_t07_t08") | Atualizado no mesmo espírito autorizado explicitamente para `test_non_edital_entities_still_empty` (mesma classe de invalidação: contrato muda de propósito, não bug). Restringido a `programa\|*` (escopo T08) e renomeado; a cobertura da forma vigente para `ict` vive em `test_gold_provenance_icts.py`. Segue o precedente do relatório T06 (`test_non_finep_entities_have_empty_provenance` removido/reescrito pela mesma razão). Se a T08 pousar em paralelo e também tocar este teste, é conflito de pouso para a governança resolver — nenhuma função de T08 (`_ingest_investidores`/`_ingest_programas`/`_get_agency`) foi tocada. |
| `source_url` da âncora | Interpretado como `record["url"]` — a URL oficial da própria unidade EMBRAPII (não uma URL de listagem separada). Confirmado no docstring de `pipeline/extractors/ict_embrapii.py`: cada registro do bronze carrega seu próprio `url` (a página da unidade), que é o mesmo valor usado em `metadata.url`. Não existe URL de "listagem" separada no registro. |
| Produtor de `setores`/`tecnologias_tags` | A tabela de fatos da task unifica as duas sob uma única regra (`normalize_tags:v1`), mesmo `setores` internamente usando `normalize_setores` em `_ingest_icts`. Segui a tabela literalmente (contrato aprovado da task), documentando a decisão no docstring do builder. |
| `name`/`metadata.url` só recebem entrada quando o registro TEM o campo | `_ingest_icts` usa `r.get("name") or slug` como fallback para o `name` persistido — mas `build_ict_fact_provenance` só declara o path `name` como `stated` quando `record.get("name")` é truthy (regra explícita da task: "campo sem valor no registro → sem entrada"). Se o registro não tiver `name`, o valor gravado vem do slug determinístico, não é uma declaração do scraper — corretamente sem provenance `stated` fabricada. |

## Dados e migrations

Nenhuma migration nova. Nenhuma fixture nova (reusa
`tests/fixtures/gold_equivalence/bronze/ict_raw/embrapii_fixture.json`,
já existente). A coluna `entities.provenance`/`entity_relationships.provenance`
já existia (migration 036, T04/T05).

## Validação

### 1. Suíte nova (20/20 passam)

```
$ PYTHONPATH=src pytest -q tests/unit/test_gold_provenance_icts.py
....................                                                     [100%]
20 passed in 0.69s
```

### 2. Gate T02 — equivalência do baseline (16/16 passam, intacto)

```
$ PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py
................                                                         [100%]
16 passed in 0.50s
```

### 3. Suítes de proveniência + mappers (133/133 passam)

```
$ PYTHONPATH=src pytest -q tests/unit/test_gold_provenance_sources.py \
    tests/unit/test_gold_provenance_dualwrite.py tests/unit/test_provenance.py \
    tests/unit/test_evidence_resolver.py tests/unit/test_etl_gold_pipeline.py \
    tests/unit/test_gold_mappers.py
........................................................................ [ 54%]
.............................................................            [100%]
133 passed in 1.30s
```

### 4. Ruff + diff hygiene

```
$ ruff check src/radar/core/kg/gold.py src/radar/core/kg/provenance_writer.py \
    tests/unit/test_gold_provenance_icts.py tests/unit/test_gold_provenance_sources.py \
    tests/unit/test_gold_provenance_dualwrite.py
All checks passed!

$ git diff --check
(sem output — sem erros de whitespace)

$ git diff 5017b052a --stat
 src/radar/core/kg/gold.py                    |  29 ++++--
 src/radar/core/kg/provenance_writer.py       | 131 +++++++++++++++++++++++++++
 tests/unit/test_gold_provenance_dualwrite.py |  29 +++---
 tests/unit/test_gold_provenance_sources.py   |   5 +-
 4 files changed, 173 insertions(+), 21 deletions(-)
```

`tests/unit/test_gold_provenance_icts.py` é arquivo novo (não rastreado até
o commit desta task).

### Extra: suíte unitária completa (sanity, não exigida pela task)

```
$ PYTHONPATH=src pytest -q tests/unit
1260 passed, 2 skipped, 4 warnings in 17.48s
```

`git status --short` no fim da execução:

```
 M src/radar/core/kg/gold.py
 M src/radar/core/kg/provenance_writer.py
 M tests/unit/test_gold_provenance_dualwrite.py
 M tests/unit/test_gold_provenance_sources.py
?? tests/unit/test_gold_provenance_icts.py
```

(sem untracked além do esperado; tudo foi para o commit único da task)

## Pendências

Nenhuma. `deadline`/`name`-fallback (quando o registro não declara `name`)
e demais campos não cobertos pela tabela de fatos da task (ex.
`institution_type`, `contact`, `embrapii_kind`) seguem sem provenance —
fora do escopo aprovado desta task, mesma dívida deliberada do padrão
FINEP/T05.

## Auditoria Codex

- **Arquivos tocados**: `src/radar/core/kg/gold.py` (só `_ingest_icts` +
  docstring de `_upsert_rel`), `src/radar/core/kg/provenance_writer.py`
  (seção nova ao final, builders existentes intocados),
  `tests/unit/test_gold_provenance_sources.py` (1 asserção),
  `tests/unit/test_gold_provenance_dualwrite.py` (1 teste renomeado/restrito
  + 2 docstrings), `tests/unit/test_gold_provenance_icts.py` (novo)
- **Gates intocáveis**: `tests/helpers/gold_projection.py` não modificado;
  `tests/unit/test_gold_equivalence.py` passa (16/16) sem alteração;
  `src/radar/core/kg/equivalence.py` não modificado; fixtures de
  `tests/fixtures/gold_equivalence/**` não modificadas nem regeneradas
- **Sem alteração de**: prompts/modelos LLM, migrations, RLS, score de
  confiança, estado factual novo além do aprovado pela tabela de fatos,
  coordenada/hash fabricado, consumidor novo lendo provenance, banco
  remoto/rede/LLM real
- **Funções de T08 não tocadas**: `_ingest_investidores`,
  `_ingest_programas`, `_get_agency` — confirmado por `git diff --stat`
  (só `_ingest_icts` mudou em `gold.py`, mais a docstring informativa de
  `_upsert_rel`)
- **Hashes**: prefixo `md5:` real (`hashlib.md5`, mesmo padrão de
  `source_docs.canonical_hash`), nunca re-hasheado para simular outro
  algoritmo; hash cobre o registro individual, não o arquivo inteiro
  (testado explicitamente)
- **Worktree limpo**: sem untracked além dos arquivos desta task

**Veredito:** pendente

## Auditoria (governança — Fable)

**Veredito:** aprovada em 2026-07-24.

- Diff inspecionado integralmente: só `_ingest_icts` tocado no gold (T08
  intocada), builders em seção delimitada, gate T02 byte-idêntico (0 linhas
  de diff em harness/teste/baseline/fixtures);
- suítes reexecutadas: 169 passed; Ruff e `git diff --check` limpos;
- adversarial independente (captura própria): baseline com 0 divergências;
  âncora do registro recomputada de forma independente a partir da fixture
  e IDÊNTICA à gravada (`md5:` do JSON canônico do registro individual);
  `document_only` sem quote e sem coordenadas fabricadas; edge
  `credenciada_por` stated com a MESMA âncora; investidor/programa/agencia
  seguem com provenance vazia (escopo T08 preservado);
- a atualização do teste `test_actor_edges_have_empty_provenance_until_t07_t08`
  (restrito a `programa|*`) foi além da lista explícita de arquivos, mas é
  consequência direta e inevitável do contrato da task — aceita, espelha o
  precedente da T06; o conflito esperado com a T08 nesse arquivo será
  resolvido pela governança no pouso;
- decisões do implementador aceitas: hash por registro individual (não do
  arquivo inteiro — testado), âncora construída uma vez e reusada na edge.

