# RT01-T05 — Vertical slice FINEP

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T05-finep-vertical-slice.md`](../../plans/01-provenance/RT01-T05-finep-vertical-slice.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t05` / `87cf84728`
**Commits:** nenhum — mudanças em staging (`git add`), commit fica para depois da auditoria da governança
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

- `src/radar/core/kg/provenance_writer.py` (NOVO) — módulo PURO (sem I/O,
  sem banco, sem chamada de rede) com os builders da tabela de fatos
  aprovada da task:
  - `build_status_provenance` — `status`: sempre `inferred/deterministic`,
    `rule="_normalize_status:v1"`, `inputs=["bronze.status","deadline"]`,
    sem `EvidenceRef`;
  - `build_mecanismo_provenance` — `mecanismo`: `inferred/deterministic`,
    `rule="_infer_mecanismo_from_text:v1"`;
  - `build_tags_provenance` — `setores`/`tecnologias_tags`: `inferred/llm`,
    `producer.name="gold_tagger"`, `model=<OPENAI_MODEL efetivo>`,
    `prompt_version="gold_tagger:v1"` (constante — os prompts originais
    `_TAGGER_SYSTEM`/`_SYSTEM_V3` são intocados; a versão descreve o
    contrato do prompt corrente, não altera seu texto);
  - `build_constraints_provenance` — `constraints`: `inferred/llm`,
    `producer.name="constraints_producer"`, `version="v3"` (o entry point
    real usado, `produce_from_text`, é literalmente descrito como "v3" no
    docstring do próprio módulo), `model=CONSTRAINTS_MODEL`,
    `prompt_version="constraints_producer:v3"`;
  - `build_requisito_provenance` — um item de `requisitos_texto`: chama
    `evidence_resolver.resolve_quote` contra os blocos silver reais;
    `stated` **somente** quando o locator resolvido é `exact` ou
    `document_only`; caso contrário `inferred` — nunca `stated` sem
    `EvidenceRef` (a condição checa o `locator_quality`, não só a presença
    de referência: `unresolved` também carrega um `EvidenceRef`, mas não
    promove a `stated`);
  - `build_operado_por_provenance` / `build_subordinado_a_provenance` —
    arestas: `inferred/deterministic`, regras `_SOURCE_AGENCY:v1` /
    `_detect_programa:v1` (esta última com `inputs=["name"]`, conforme a
    tabela de fatos);
  - `build_edital_fact_provenance` — compõe o dict `path ->
    FactProvenance.model_dump(mode="json")` completo de um edital: `status`
    sempre presente; `mecanismo` só quando não `None`; `constraints` só
    quando não vazio; um `requisitos_texto.<i>` por item (índice 0-based);
  - `chunk_storage_coords` — coordenadas do PRIMEIRO bloco constituinte de
    um chunk já empacotado (`document`/`page`/`silver_block_idx`/
    `source_hash`), dict vazio quando o chunk não tem bloco de origem
    identificável (não fabrica coordenada).
- `src/radar/core/kg/gold.py` (ÚNICO arquivo produtivo modificado, mínimo):
  - `_pack_chunks`: cada chunk ganha as chaves aditivas `src_doc`/
    `src_page`/`src_idx` do PRIMEIRO bloco constituinte (`head`) —
    presentes para TODA fonte (aditivo e inerte quando não usado; ver
    "Decisões" sobre a semântica de âncora);
  - `_ENTITY_UPSERT` + `_upsert_entity`: coluna `provenance` no INSERT, com
    guard anti-clobber no `ON CONFLICT`: `provenance = case when
    excluded.provenance = '{}'::jsonb then entities.provenance else
    excluded.provenance end` — um upsert que não menciona provenance (ou
    menciona `{}`) NUNCA apaga a proveniência já gravada; um upsert com
    provenance não vazia SUBSTITUI integralmente (não faz merge por path);
  - `_upsert_rel`: novo parâmetro opcional `provenance: dict | None = None`,
    gravado no mesmo INSERT (`on conflict do nothing` preservado — uma
    aresta pré-existente não é atualizada, nem `properties` nem
    `provenance`, por uma chamada repetida). **Atualização pós-emenda da
    governança (ver "Divergências e decisões"): `_ingest_editais` agora
    PASSA este parâmetro** para as arestas `operado_por`/`subordinado_a`,
    SOMENTE quando `source == "finep"` — outras fontes continuam chamando
    sem `provenance` (byte-idêntico ao path antigo);
  - `_replace_match_chunks`: as 4 colunas novas
    (`document`/`page`/`silver_block_idx`/`source_hash`) são lidas de cada
    dict de `chunks` via `.get(...)` e persistidas na mesma INSERT; ausentes
    → NULL (mesmo comportamento "legado" definido pela migration 042);
  - `_ingest_editais`: para `source == "finep"`, monta
    `entity_provenance = provenance_writer.build_edital_fact_provenance(...)`
    (usando `status`/`mecanismo_value`/`constraints`/`requisitos` já
    calculados, os `blocks` silver completos do edital, `native_id=stem`
    "cru" e `edital_id=<src>:<stem>` prefixado — mesma convenção do exemplo
    `EvidenceRef` da spec §4.2), anota cada chunk empacotado com
    `provenance_writer.chunk_storage_coords(c, f"md5:{src_hash}")` antes de
    `_replace_match_chunks`, e (pós-emenda) monta
    `operado_por_provenance`/`subordinado_a_provenance` via
    `provenance_writer.build_operado_por_provenance()`/
    `build_subordinado_a_provenance()`, passados a `_upsert_rel(...,
    provenance=...)` nas duas chamadas de aresta; para as demais 3 fontes de
    edital (fapesp/fapesc/web), `entity_provenance` permanece `{}`, os
    chunks não ganham as 4 chaves novas, e as chamadas de `_upsert_rel`
    passam `provenance=None` (mesmo default de antes) — comportamento
    observável byte-idêntico ao path antigo.
- `tests/helpers/gold_projection.py` (EMENDA MÍNIMA, autorizada pela
  governança em 2026-07-24 — ver "Divergências e decisões"): `stub_upsert_rel`
  ganha o parâmetro `provenance: dict | None = None`, aceito e
  DELIBERADAMENTE IGNORADO (`del provenance`, comentário explícito) — não
  entra em `make_relation_record`, não é capturado na projeção comparada
  pelo gate T02. Nada mais no harness foi tocado.
- `tests/unit/test_gold_provenance_dualwrite.py` (NOVO) — 19 testes em dois
  blocos:
  - (a) builders isolados (sem gold.py/harness): um teste por linha da
    tabela de fatos confirmando estado/produtor/derivação e que todo
    `FactProvenance` emitido passa `FactProvenance.model_validate(...)`;
    `TestRequisitoResolution` contra o silver REAL da fixture
    `tests/fixtures/gold_equivalence/.../finep/602.jsonl` — um trecho
    verbatim único ("A duração máxima de cada projeto será de 2 (dois)
    anos.", bloco `idx=47`, página 5) resolve `stated` com `EvidenceRef`
    `locator_quality=exact`; um trecho inexistente cai em `inferred`;
    caso adversarial extra: mesmo com o trecho existindo verbatim, SEM hash
    (`silver_source_hash=None`) o resultado é `inferred` com
    `evidence_refs=[]` (nunca `stated` sem referência resolvida, mesmo
    quando o texto "bateria");
  - (b) captura via harness T02 **modificado apenas pela emenda mínima
    autorizada** (ver acima) — subclasse local
    `_ProvenanceCapturingHarness(GoldCaptureHarness)` que reusa 100% da
    lógica herdada (`super().stub_upsert_entity(...)`/
    `super().stub_upsert_rel(...)`/`super().stub_replace_match_chunks(...)`)
    e adicionalmente guarda `f.get("provenance")` por chave natural de
    entidade, o kwarg `provenance` de `stub_upsert_rel` por chave natural de
    relação (`equivalence.relation_key`), e as 4 coordenadas por chunk.
    Prova: o edital finep tem provenance não vazia com os paths mínimos
    esperados (`status`/`setores`/`tecnologias_tags`, mais
    `mecanismo`/`constraints`/`requisitos_texto.0` no caso real da fixture);
    TODAS as demais entidades da fixture (fapesp/fapesc/web/embrapii/
    curadoria — investidor, programa, ICT, agências) têm provenance vazia
    (`{}`); coordenadas de chunk só existem (não-None) nos chunks do edital
    finep, todas as outras entidades com chunks têm as 4 colunas `None`; a
    aresta `operado_por` do edital finep tem provenance válida
    (`FactProvenance.model_validate` ok, `state=inferred`,
    `producer.kind=deterministic`, `rule=_SOURCE_AGENCY:v1`); TODAS as
    demais arestas da fixture (fapesp/fapesc `operado_por`, EMBRAPII
    `credenciada_por`, as 3 `operado_por` do programa Centelha) têm
    provenance vazia; duas capturas consecutivas concordam byte a byte
    (determinismo, incluindo a proveniência de relação).
- `tests/integration/test_provenance_dualwrite.py` (NOVO) — gated pela mesma
  sonda de conectividade de `test_provenance_storage.py` (skip em runtime se
  o Postgres local não responder, não por presença de env var), 7 testes,
  toda transação revertida no teardown do fixture `pg_conn`:
  1. `_upsert_entity` persiste `provenance` não vazia passada explicitamente
     (round-trip + `FactProvenance.model_validate` de cada path);
  2. re-upsert do MESMO `(source, native_id)` sem passar `provenance` (ou
     com `{}`) NÃO apaga a já gravada — guard anti-clobber confirmado contra
     o banco real; campos normais (`name`) continuam sendo sobrescritos
     normalmente;
  3. re-upsert com `provenance` NÃO vazia SUBSTITUI integralmente (não faz
     merge por path);
  4. `_upsert_rel(..., provenance=...)` grava a proveniência no INSERT;
  5. uma segunda chamada de `_upsert_rel` para a MESMA aresta (com
     provenance diferente) não a atualiza — `on conflict do nothing`
     confirmado (1 única linha, com o `provenance` da PRIMEIRA chamada);
  6. `_replace_match_chunks` persiste as 4 colunas novas quando o dict do
     chunk as carrega, e mantém `NULL` para um chunk sem essas chaves (dois
     chunks no mesmo `entity_id`, comportamento por-chunk confirmado);
  7. `_replace_match_chunks` (delete+insert) substitui integralmente as
     linhas anteriores, inclusive as 4 colunas novas.

## Divergências e decisões

- **ACHADO original (antes da emenda): arestas `operado_por`/`subordinado_a`
  — a capacidade de gravar `provenance` existia em `_upsert_rel`, mas NÃO
  estava conectada às chamadas reais de `_ingest_editais`/
  `_ingest_programas`/`_ingest_icts`.**
  Motivo, verificado por leitura direta do harness congelado
  (`tests/helpers/gold_projection.py::GoldCaptureHarness.stub_upsert_rel`):
  sua assinatura era fixa,
  `(self, cur, source_id, target_id, rtype, properties=None)`, sem
  `**kwargs`. Ao contrário de `stub_upsert_entity(self, cur, **f)`
  (flexível — aceita `provenance=` sem quebrar, o que é exatamente como a
  entidade do edital finep recebe proveniência nesta task) e de
  `stub_replace_match_chunks` (cujo 3º argumento posicional, `chunks`, é uma
  lista de dicts onde chaves aditivas são inertes ao stub), `_upsert_rel`
  não tinha um canal seguro para dado adicional: passar `provenance=` como
  kwarg nomeado a partir de `_ingest_editais` faria o MESMO código, ao rodar
  sob o harness T02 (que substitui `gold._upsert_rel` inteiro pelo stub
  congelado), lançar `TypeError: unexpected keyword argument 'provenance'`
  — quebrando `tests/unit/test_gold_equivalence.py` (INTOCÁVEL) com erro,
  não com divergência. Smugglar o valor dentro do parâmetro `properties`
  existente também não funcionava: `properties` É um campo comparado pelo
  comparador de equivalência (`equivalence.IGNORED_RELATION_FIELDS` não o
  inclui), então qualquer conteúdo além de `{}` ali seria uma divergência
  real contra o baseline congelado (que tem `properties={}` para as arestas
  `operado_por` capturadas).
  **Decisão original (entrega inicial desta task):** entregar a capacidade
  em `_upsert_rel` (validada diretamente pela integração, chamando a função
  sem passar por `_ingest_editais`) e PARAR — registrar a fiação real como
  pendência explícita em vez de arriscar o gate T02, conforme o critério de
  parada do enunciado ("a tabela de fatos não acomodar caso real").

- **CORREÇÃO autorizada pela governança (Fable, 2026-07-24), após revisão
  do achado acima: emenda mínima e escopada no harness T02, decisão
  registrada e aplicada.** A governança concordou com o diagnóstico (a
  parada foi correta, o achado legítimo) e autorizou explicitamente:
  1. `tests/helpers/gold_projection.py::stub_upsert_rel` ganha o parâmetro
     `provenance: dict | None = None`, **aceito e IGNORADO por desenho**
     (`del provenance` logo no corpo da função, com comentário) — não entra
     em `make_relation_record`, não é capturado na projeção que o gate T02
     compara. `equivalence.py` e `test_gold_equivalence.py` continuam
     INTOCADOS; `baseline_projection.json` continua INTOCADO.
  2. `_ingest_editais` passa a chamar `_upsert_rel(..., provenance=...)`
     para `operado_por`/`subordinado_a`, com o valor real
     (`provenance_writer.build_operado_por_provenance()`/
     `build_subordinado_a_provenance()`) **somente quando `source ==
     "finep"`**; para as outras 3 fontes de edital, a chamada continua sem
     provenance de fato (`provenance=None`, o mesmo default de antes —
     resultado gravado idêntico, `'{}'::jsonb`).
  3. O wrapper local de teste (`_ProvenanceCapturingHarness` em
     `tests/unit/test_gold_provenance_dualwrite.py`) passou a também
     sobrescrever `stub_upsert_rel` para capturar o `provenance` recebido
     (delegando a gravação da projeção normal para `super()`), com dois
     testes novos: a aresta `operado_por` do edital finep tem
     `FactProvenance` válida (`state=inferred`,
     `producer.kind=deterministic`, `rule=_SOURCE_AGENCY:v1`); todas as
     demais arestas da fixture (fapesp/fapesc `operado_por`, EMBRAPII
     `credenciada_por`, as 3 `operado_por` do programa Centelha) têm
     provenance vazia.
  Por que a emenda é segura (verificado, não assumido): `stub_upsert_rel`
  ainda constrói o `record` da projeção exatamente como antes
  (`make_relation_record(source_entity_key=..., target_entity_key=...,
  type=rtype, properties=properties)` — sem o parâmetro novo), então a
  projeção capturada é estruturalmente idêntica independente do valor de
  `provenance` recebido; `git diff` confirma que `baseline_projection.json`
  não mudou; `tests/unit/test_gold_equivalence.py` roda 16/16 verde após a
  emenda (ver "Validação").
  Resultado: a pendência do achado original está RESOLVIDA — as duas
  arestas da tabela de fatos (`operado_por`/`subordinado_a`) agora gravam
  proveniência de fato num `ingest_all()` real para `source == "finep"`,
  com a mesma restrição de escopo do resto da task.
- **`tests/integration/test_provenance_storage.py` (T04) precisou de um
  ajuste mínimo, não listado no escopo `MODIFICAR` da task.** A mudança
  aditiva em `_ENTITY_UPSERT` (nova coluna nomeada `provenance` no INSERT)
  quebra `TestGoldEntityUpsertCompatibility::
  test_entity_upsert_sql_literal_runs_twice_after_migration`, cujo `params`
  dict não tinha a chave `provenance` — psycopg levanta
  `ProgrammingError: query parameter missing: provenance` porque o SQL
  literal agora referencia esse placeholder duas vezes (INSERT + `CASE` do
  guard anti-clobber). Corrigido com o mínimo necessário: `"provenance":
  json.dumps({})` adicionado ao dict de params, e o comentário/docstring
  desatualizado ("sem `provenance`"/"o upsert não menciona a coluna nova")
  atualizado para refletir o estado real pós-T05. O comportamento provado
  pelo teste (upsert idempotente por `(source, native_id)`;
  `provenance='{}'` quando o caller não passa proveniência de fato)
  permanece o mesmo — só o SQL literal mudou de forma que o teste precisa
  acompanhar. Este arquivo NÃO está na lista INTOCÁVEIS da task (só
  `tests/helpers/gold_projection.py`/`tests/unit/test_gold_equivalence.py`/
  `src/radar/core/kg/equivalence.py` estão).
- **`native_id` vs `edital_id` em `EvidenceRef`** — segui o exemplo literal
  da spec §4.2 (`"native_id": "745"`, `"native_id"` SEM prefixo de fonte;
  `"edital_id": "finep:745"`, COM prefixo): `_ingest_editais` passa
  `native_id=stem` (ex. `"602"`) e `edital_id=native_id` (a variável já
  prefixada, ex. `"finep:602"`) para `build_edital_fact_provenance`.
- **`deadline` e `name` não recebem proveniência** — dívida deliberada
  confirmada pelo enunciado da task: a âncora versionada do registro do
  portal (fonte/coleta/hash do bronze) pertence à spec 04
  (source-bundles), fora do escopo desta vertical slice. Nenhum código
  desta task grava `entities.provenance["deadline"]` ou
  `entities.provenance["name"]`.
- **`prompt_version` dos dois produtores LLM (`gold_tagger:v1`,
  `constraints_producer:v3`) são constantes NOVAS, documentadas em
  `provenance_writer.py`** — os módulos originais
  (`gold._TAGGER_SYSTEM`, `constraints_producer._SYSTEM_V3`) não
  versionam a si próprios e são INTOCÁVEIS nesta task; a versão fixa o
  valor CORRENTE do contrato do prompt sem alterar nenhum texto de prompt
  real. `"v3"` do `producer.version` de `constraints_producer` reaproveita
  a nomenclatura já usada no docstring do próprio módulo
  ("v3 (spec docs/specs/v3-unified.md) — entry point ADITIVO"), não é
  inventada.
- **`_pack_chunks` ganha `src_doc`/`src_page`/`src_idx` para TODA fonte**
  (não só finep) — decisão de simplicidade: são chaves aditivas inertes
  para o harness (`stub_replace_match_chunks` só lê `section_path`/`kind`/
  `text` via `.get`/`[...]`) e para as fontes que não as consomem
  (`_ingest_programas` nunca chama `chunk_storage_coords`, então essas
  chaves ficam no dict do chunk mas nunca viram colunas persistidas). Só
  `_ingest_editais`, guardado por `if src == "finep"`, de fato as usa para
  popular `document`/`page`/`silver_block_idx`/`source_hash`.
- **Semântica de âncora do chunk** (pedida explicitamente pelo enunciado):
  cada `match_chunk` finep grava as coordenadas do **primeiro bloco
  constituinte** — "o chunk começa aqui", não um range que cobre todos os
  blocos empacotados (um chunk pode conter vários blocos silver
  concatenados até `_CHUNK_CHARS`). Documentado no docstring de
  `_pack_chunks` e de `provenance_writer.chunk_storage_coords`.
- Nenhuma outra divergência do plano/spec. O gate central (T02) permanece
  byte-idêntico — ver "Validação".

## Dados e migrations

- Nenhuma migration nova nesta task (a 042 já existe, da T04).
- Nenhum dado persistido permanentemente: toda escrita de teste
  (integração) ocorreu dentro de transações revertidas
  (`pg_conn` fixture, `rollback()` sempre no teardown, inclusive em falha).
  Contagens de linhas idênticas antes e depois de toda a sessão (23/2/67) —
  ver "Validação", item 5.

## Validação

Duas rodadas: a primeira (entrega inicial, achado registrado) e a segunda
(pós-emenda autorizada pela governança, seção 5 do pedido de correção —
"Reexecutar TODAS as validações 1–6"). Os outputs abaixo são da rodada
FINAL, pós-emenda; a rodada inicial (pré-emenda) já havia confirmado 16/16
no gate com 17 testes em (1) — números preservados na história do commit
via este relatório, substituídos abaixo pelos números finais.

### 1. `PYTHONPATH=src pytest -q tests/unit/test_gold_provenance_dualwrite.py`

```
...................                                                      [100%]
19 passed in 0.60s
```
(17 originais + 2 novos pós-emenda: `test_finep_operado_por_edge_has_valid_deterministic_provenance`,
`test_non_finep_edges_have_empty_provenance`.)

### 2. `PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py` (gate central)

```
................                                                         [100%]
16 passed in 0.44s
```

`src/radar/core/kg/equivalence.py` e `baseline_projection.json` permanecem
INTOCADOS (diff vazio contra `87cf84728`, confirmado abaixo).
`tests/helpers/gold_projection.py` recebeu a emenda MÍNIMA autorizada pela
governança (1 parâmetro novo em `stub_upsert_rel`, aceito e ignorado — ver
"Divergências e decisões"); o gate continua 16/16 verde com o baseline
congelado intacto.

### 3. Suíte direcionada

```
PYTHONPATH=src pytest -q tests/unit/test_provenance.py tests/unit/test_evidence_resolver.py tests/unit/test_etl_gold_pipeline.py tests/unit/test_gold_mappers.py tests/unit/test_gold_constraints_parser.py tests/unit/test_promote_gold_ingest.py

........................................................................ [ 60%]
...............................................                          [100%]
119 passed in 0.87s
```

### 4. Integração

```
PYTHONPATH=src pytest -q tests/integration/test_provenance_storage.py tests/integration/test_provenance_dualwrite.py

.............                                                            [100%]
13 passed in 0.50s
```
(6 do T04 + 7 novos do T05 — todos verdes, incluindo o teste ajustado de
`TestGoldEntityUpsertCompatibility`.)

### 5. Contagens do banco antes/depois

Antes de qualquer teste desta task:
```
entities 23
entity_relationships 2
match_chunks 67
```

Depois de rodar TODA a suíte (unit + integration, seções 1-4 acima):
```
entities 23
entity_relationships 2
match_chunks 67
leftover rt01t05 entities (should be 0): 0
```

Idênticas — zero resíduo confirmado.

### 6. `ruff check` + `git diff --check`

```
$ ruff check src/radar/core/kg/gold.py src/radar/core/kg/provenance_writer.py \
    tests/unit/test_gold_provenance_dualwrite.py \
    tests/integration/test_provenance_dualwrite.py \
    tests/integration/test_provenance_storage.py \
    tests/helpers/gold_projection.py
All checks passed!
```
(1 rodada inicial encontrou 2 `B007` — variável de loop `path` não usada em
`test_gold_provenance_dualwrite.py`/`test_provenance_dualwrite.py`;
renomeadas para `_path`, segunda rodada limpa; `gold_projection.py` incluído
na verificação após a emenda, limpo.)

```
$ git diff --check
(saída vazia — sem trailing whitespace / conflitos)
```

### Sanidade adicional

```
$ PYTHONPATH=src pytest -q tests/unit --collect-only
1231 tests collected in 5.21s
```
Nenhum erro de import na suíte `tests/unit` inteira — confirma que nenhum
outro módulo depende de uma assinatura antiga que este diff quebrou.

### Invariantes confirmados (pós-emenda)

```
$ git diff 87cf84728 -- tests/fixtures/gold_equivalence/baseline_projection.json | wc -l
0
$ git diff 87cf84728 -- src/radar/core/kg/equivalence.py tests/unit/test_gold_equivalence.py | wc -l
0
```
Baseline e módulo/teste do gate T02: zero diferença contra `87cf84728`.

```
$ git diff 87cf84728 --diff-filter=MD --stat
 src/radar/core/kg/gold.py                    | 108 +++++++++++++++++++++++----
 tests/helpers/gold_projection.py             |  12 ++-
 tests/integration/test_provenance_storage.py |  20 +++--
 3 files changed, 117 insertions(+), 23 deletions(-)
```
(3 arquivos EXISTENTES modificados: `gold.py` — único arquivo produtivo
tocado, exatamente o escopo `MODIFICAR` da task;
`tests/helpers/gold_projection.py` — a emenda mínima autorizada pela
governança (diff isolado mostrado abaixo, 1 parâmetro novo + comentário);
`test_provenance_storage.py` — ajuste mínimo justificado acima. Nenhum
arquivo protegido pela emenda — `equivalence.py`, `test_gold_equivalence.py`,
`baseline_projection.json`, `evidence_resolver.py`, `provenance.py`,
migrations, fixtures — aparece nesta lista.)

Diff isolado da emenda no harness (única mudança em
`tests/helpers/gold_projection.py`):
```diff
@@ -203,8 +203,18 @@ class GoldCaptureHarness:
         return synthetic_id

     def stub_upsert_rel(
-        self, cur: Any, source_id: str, target_id: str, rtype: str, properties: dict | None = None
+        self, cur: Any, source_id: str, target_id: str, rtype: str,
+        properties: dict | None = None, provenance: dict | None = None,
     ) -> None:
+        # `provenance` (RT01-T05, emenda autorizada pela governança em
+        # 2026-07-24): aceito para que a assinatura acompanhe
+        # `gold._upsert_rel`, mas deliberadamente IGNORADO — não entra em
+        # `make_relation_record`, não é capturado na projeção. A projeção
+        # capturada permanece byte-idêntica ao baseline congelado
+        # independente do que este parâmetro receber; a captura real do
+        # valor (para os testes específicos de T05) é feita por um wrapper
+        # LOCAL em tests/unit/test_gold_provenance_dualwrite.py, não aqui.
+        del provenance
         record = equivalence.make_relation_record(
             source_entity_key=self._id_to_key.get(source_id, source_id),
             target_entity_key=self._id_to_key.get(target_id, target_id),
```

```
$ git diff 87cf84728 --stat
 .../01-provenance/RT01-T05-finep-vertical-slice.md | 485 +++++++++++++++++++++
 src/radar/core/kg/gold.py                          | 108 ++++-
 src/radar/core/kg/provenance_writer.py             | 299 +++++++++++++
 tests/helpers/gold_projection.py                   |  12 +-
 tests/integration/test_provenance_dualwrite.py     | 240 ++++++++++
 tests/integration/test_provenance_storage.py       |  20 +-
 tests/unit/test_gold_provenance_dualwrite.py       | 346 +++++++++++++++
 7 files changed, 1487 insertions(+), 23 deletions(-)
```
(o próprio relatório entra nesta contagem, autorreferencial — número
aproximado por natureza, cresce a cada revisão do relatório; o que importa é
que apenas os 3 `M`/4 `A` esperados aparecem, nenhum arquivo INTOCÁVEL.)

`git status --short`:
```
A  docs/execution/radar-data-trust/reports/01-provenance/RT01-T05-finep-vertical-slice.md
M  src/radar/core/kg/gold.py
A  src/radar/core/kg/provenance_writer.py
M  tests/helpers/gold_projection.py
A  tests/integration/test_provenance_dualwrite.py
M  tests/integration/test_provenance_storage.py
A  tests/unit/test_gold_provenance_dualwrite.py
```

Worktree limpo confirmado: exatamente 3 `M` (gold.py, a emenda mínima em
gold_projection.py, e o ajuste mínimo em test_provenance_storage.py) e 4
`A`, zero `D`; tudo em staging (`git add`), nenhum commit criado.

## Pendências

- ~~Arestas `operado_por`/`subordinado_a` sem `provenance` num
  `ingest_all()` real~~ — **RESOLVIDA** pela emenda autorizada pela
  governança em 2026-07-24 (ver "Divergências e decisões"). `_ingest_editais`
  agora grava `provenance` nas duas arestas para `source == "finep"`.
- `deadline`/`name` continuam sem proveniência (dívida deliberada,
  confirmada pelo enunciado — pertence à spec 04/source-bundles).
- `RT01-T06` (FAPESP/FAPESC/Web no mesmo contrato) não foi tocado — esta
  task só cobre `source == "finep"`; as outras 3 fontes de edital
  permanecem byte-idênticas ao path antigo (`entity_provenance = {}`,
  chunks sem as 4 colunas novas), confirmado pelos testes (b) do bloco de
  harness.
- Nenhum consumidor lê `entities.provenance`/`entity_relationships.
  provenance`/as 4 colunas novas de `match_chunks` ainda — como esperado
  para esta task (spec §9.2, passo 1 de 5).

## Auditoria Codex

**Veredito:** aprovada em 2026-07-24 (auditoria da governança — Fable).

Validação independente, sem confiar no resumo do implementador:

- diff completo inspecionado: 4 arquivos novos + 3 modificados (`gold.py`;
  emenda autorizada do harness; atualização mínima do teste T04 exigida
  pelo `_ENTITY_UPSERT` novo). Gate T02 byte-idêntico: `equivalence.py`,
  `test_gold_equivalence.py` e `baseline_projection.json` com 0 linhas de
  diff contra a base;
- a emenda do harness é exatamente a autorizada: `stub_upsert_rel` aceita e
  descarta (`del`) o kwarg `provenance` sem capturá-lo — incapaz de mascarar
  divergência;
- `provenance_writer.py` e o diff de `gold.py` lidos linha a linha; a tabela
  de fatos aprovada é seguida à risca (deadline/name deferidos à spec 04,
  registrado em Pendências);
- suítes reexecutadas: 167 passed (unit+integração), Ruff e `git diff
  --check` limpos; contagens do banco local intactas (23/2/67);
- auditoria adversarial própria (captura com wrapper independente, fora da
  suíte): 0 divergências contra o baseline congelado sob o código novo;
  provenance presente SOMENTE na entidade finep (6 paths, todos passando
  `FactProvenance.model_validate`); nenhum `stated` sem EvidenceRef
  exact/document_only; `deadline`/`name` ausentes do dict como manda o
  contrato; entidades das demais fontes com provenance vazio; coordenadas
  gravadas somente nos chunks finep; edge `operado_por` finep com
  provenance deterministic válida e edges não-finep sem provenance;
- sequência achado → correção autorizada → validação preservada no
  relatório (o wiring de edges parou corretamente na condição de parada e
  foi completado sob autorização explícita da governança);
- correção da governança declarada (perda de independência assumida, escopo
  textual): a docstring de `_upsert_rel` ficara desatualizada após a emenda
  (descrevia o estado pré-wiring); a governança a reescreveu para o estado
  real. Sem mudança de comportamento; gate e Ruff reexecutados verdes após
  a correção.
