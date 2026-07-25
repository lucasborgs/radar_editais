# RT01-T02 — Baseline de equivalência

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T02-equivalence-baseline.md`](../../plans/01-provenance/RT01-T02-equivalence-baseline.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t02` / `ed718bf5a`
**Commits:** nenhum — mudanças em staging (`git add`), commit fica para depois da auditoria da governança
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

- `src/radar/core/kg/equivalence.py` — módulo PURO (sem I/O, sem import de
  `tests/`) com o contrato da projeção canônica do gold (spec §9.3):
  - `entity_key(kind, source, native_id)` / `relation_key(source_key,
    target_key, type)` — chaves naturais determinísticas;
  - `make_entity_record(...)` — registro de entidade com campos escalares,
    arrays (`setores`/`tecnologias_tags`), `constraints`, `requisitos_texto`,
    `metadata`, `status`, `deadline` (ISO), `curated`, `uf`, e o hash
    sha256/modelo/dimensionalidade do INPUT de embedding (nunca o vetor).
    Campos aditivos `tagger_input_hash`/`constraints_input_hash` guardam o
    hash do input dos dois produtores LLM stubados, quando aplicável ao
    `kind`;
  - `make_relation_record(...)` — `(source_entity_key, target_entity_key,
    type)` + `properties`;
  - `make_chunk_record(...)` — `idx`, `section_path`/`kind` (coordenadas),
    `text` e hash/modelo/dim do input de embedding do chunk;
  - `GoldProjection` — acumulador (`entities`/`relations`/`match_chunks`
    indexados por chave natural) com `to_dict()`/`to_json()`/`from_dict()`;
  - `to_json()` — serialização determinística (`sort_keys=True,
    ensure_ascii=False`);
  - `diff_projections(old, new)` — comparador por chave natural; detecta
    entidade/relação ausente ou extra, mudança de campo escalar/array/
    constraint/metadata, e chunk com texto ou coordenada alterada. Ignora
    SOMENTE `IGNORED_ENTITY_FIELDS`/`IGNORED_RELATION_FIELDS`/
    `IGNORED_CHUNK_FIELDS` (`{"id", "created_at", "updated_at",
    "provenance"}`, mais `source_id`/`target_id`/`entity_id` conforme o
    registro), versionadas em `IGNORE_LIST_VERSION = 1`;
  - `render_divergences(...)` — relatório humano para mensagens de teste.
- `tests/helpers/gold_projection.py` — harness que roda
  `radar.core.kg.gold.ingest_all()` **real** sobre as fixtures, interceptando
  apenas os seams de infraestrutura:
  - banco: `_upsert_entity`, `_upsert_rel`, `_replace_match_chunks`,
    `_programa_id_map`, `_existing_hash` (sempre `None` — captura sempre
    "fresh", nunca há entidade pré-existente) e `gold.psycopg` inteiro
    (`_FakePsycopg.connect` devolve uma conexão fake com `.transaction()`/
    `.cursor()` no-op; `DATABASE_URL` recebe um DSN dummy só para `_dsn()`
    não levantar);
  - LLM tagger: `gold._tag_edital` → stub determinístico, registra
    `sha256(thematic_text)` numa fila FIFO;
  - constraints: `constraints_producer.produce_from_text` → stub
    determinístico (replica o guard real "texto vazio → 4 listas vazias"),
    registra `sha256(texto de elegibilidade)`;
  - embeddings: `embedder.embed_query` e `gold._embed_match_chunks` → vetor
    sintético determinístico (derivado do hash do input, nunca uma chamada
    real), `embedding_model="stub-embedder-v1"`, `embedding_dim=8`;
  - paths: `gold.SILVER_DIR`, `gold.STRUCTURED_DIR` (materializado à parte no
    import de `gold.py`, precisa de patch próprio) e `gold.BRONZE_DIR`
    apontam para `tests/fixtures/gold_equivalence/`;
  - caches: `gold._programa_detection.cache_clear()`,
    `gold._load_bronze.cache_clear()`, `gold._section_rules.cache_clear()`
    entre capturas.
  - `run_capture(monkeypatch, fixtures_dir=None, sources=None)` →
    `(GoldProjection, stats)`; `regenerate_baseline(fixtures_dir=None)` —
    comando de regeneração documentado (ver "Dados e migrations").
- `tests/fixtures/gold_equivalence/` — uma fixture real por origem, copiada
  (leitura apenas) de `/Users/lucasborges/radar_editais/data/`:
  finep (`602`), fapesp (`16466`), fapesc (`35-2026`), web
  (`ce032edb720c`), EMBRAPII (1 registro), 1 investidor
  (`investidor:indicator-capital`) e 1 programa (`programa:centelha`,
  operador multi-agência "MCTI / FINEP / FAPs estaduais" — exercita 3
  relações `operado_por` numa única entidade). `manifest.json` documenta
  path de origem, data da cópia, sha256, quais produtores estão stubados e o
  comando de regeneração do baseline. `baseline_projection.json` é o
  snapshot congelado (13 entidades, 7 relações, 5 grupos de match_chunks).
- `tests/unit/test_gold_equivalence.py` — 16 testes:
  - comparador (12): diff vazio para projeções idênticas; detecta mudança de
    campo escalar, array, constraint e metadata; detecta entidade
    ausente/extra e relação ausente/extra; detecta chunk com texto e com
    coordenada (`section_path`) alterados; três testes confirmam que a
    ignore-list (`id`/`created_at`/`updated_at`/`provenance`,
    `source_id`/`target_id`, `entity_id`) nunca gera divergência em
    entidade/relação/chunk;
  - baseline (2): captura real sobre as fixtures via `run_capture` ==
    `baseline_projection.json` (diff vazio, com sanity check de que a
    projeção capturada não está vazia); duas capturas consecutivas produzem
    JSON byte-idêntico (determinismo).

## Divergências e decisões

- **`_get_agency` não foi monkeypatchada diretamente**, embora esteja
  listada entre os seams de banco no enunciado. A única escrita de
  `_get_agency` passa por `_upsert_entity` (já stubado) e seu único
  embedding por `embedder.embed_query` (já stubado, via `from
  radar.core.retrieval.embedder import embed_query` local dentro da
  função) — ambos resolvidos dinamicamente pelo namespace do módulo `gold`
  a cada chamada. Como `_get_agency` real nunca chama `cur.execute(...)`
  diretamente (só repassa `cur` para `_upsert_entity`), deixá-la rodar sem
  modificação evita reimplementar sua lógica (canonicalização de nome via
  `_canon_agency`, slug via `schema.slugify`, cache por nome canônico) no
  harness — que era exatamente a instrução "PROIBIDO reimplementar lógica
  de mapeamento do gold no harness". `baseline_projection.json` confirma
  que as 6 entidades `kind=agencia` (EMBRAPII, FAPESC, FAPESP, FAPs
  estaduais, FINEP, MCTI) e as 7 relações `operado_por`/`credenciada_por`
  foram capturadas corretamente por essa via indireta.
- **EMBRAPII: 1 registro em vez de até 3**, por proteção de dado pessoal.
  89 dos 90 registros de `data/bronze/ict_raw/embrapii_*.json` carregam
  `contact.responsavel` com nome (e às vezes telefone) do ponto focal da
  unidade credenciada — dado pessoal fora do escopo desta fixture. O único
  registro sem identificação pessoal
  (`inteligencia-artificial-ceia-ufg`, `contact.responsavel=""`) foi
  escolhido deliberadamente; documentado em `manifest.json`. Verificação
  final (grep por e-mail/CPF em todas as fixtures) só encontrou endereços
  institucionais genéricos (`fapesc@fapesc.sc.gov.br`,
  `seac@finep.gov.br`, `ceia@ceia.ufg.br`, `helpme@openstartups.net` etc.),
  publicados nos próprios editais — nenhum dado pessoal identificado.
- **`sha256_of(None | "")` retorna `None`, não o hash de string vazia.**
  Decisão: distinguir "sem input" (`None`) de "input vazio processado"
  evita colisão silenciosa entre os dois casos no snapshot.
- **Modelo/dimensionalidade do stub de embedding são deliberadamente
  fictícios** (`"stub-embedder-v1"`, dim 8) em vez de espelhar o
  `EMBEDDING_MODEL`/`EMBEDDING_DIMENSIONS` reais (`text-embedding-3-small`,
  1536) — o snapshot nunca deve poder ser lido como saída real de modelo
  (spec, "Proibido... snapshot e stubs nunca se apresentam como saída real
  de modelo").
- **`manifest.json` recebeu o campo `regenerate_baseline_command`** (adição
  pontual desta revisão) para que o comando de regeneração fique
  documentado tanto no manifest quanto no docstring do harness e neste
  relatório, conforme pedido no enunciado ("documentado no manifest/README
  da pasta e no relatório").
- Nenhuma lacuna de fixture: as 6 origens exigidas pela spec §9.3 (FINEP,
  FAPESP, FAPESC, Web promovida, EMBRAPII, catálogos curados
  investidor+programa) tinham dado local utilizável e foram todas cobertas.
- Nenhuma migration, banco (local ou remoto), rede ou chamada LLM real —
  confirmado por inspeção do harness (todos os seams de I/O externo são
  stubados) e pela execução hermética dos testes.

## Dados e migrations

- Não aplicável — nenhuma migration, tabela ou dado de `data/` foi alterado.
  As cópias em `tests/fixtures/gold_equivalence/` são leitura apenas
  (verificado por sha256 registrado em `manifest.json`).
- Comando de regeneração do `baseline_projection.json` (documentado em
  `tests/helpers/gold_projection.py`, `tests/fixtures/gold_equivalence/manifest.json`
  e aqui):
  ```bash
  cd /private/tmp/radar-editais-rt01-t02
  PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/python -c \
      "from tests.helpers.gold_projection import regenerate_baseline; regenerate_baseline()"
  ```

## Validação

| Comando/verificação | Resultado |
|---|---|
| `PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py` | `16 passed` |
| `PYTHONPATH=src pytest -q tests/unit/test_provenance.py` | `57 passed` |
| `PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py tests/unit/test_provenance.py` (conjunto) | `73 passed` |
| `ruff check src/radar/core/kg/equivalence.py tests/helpers/gold_projection.py tests/unit/test_gold_equivalence.py` | `All checks passed!` (1 erro de import-order em `test_gold_equivalence.py` corrigido via `ruff check --fix` antes desta rodada) |
| `git diff --check` | limpo |
| `git diff ed718bf5a -- src/radar/core/kg/gold.py` | vazio (arquivo não modificado) |
| `git diff ed718bf5a --stat` | 20 arquivos, todos novos (`A`), 2620 inserções, 0 deleções, 0 modificações em arquivo existente |
| Grep manual por e-mail/CPF/`responsavel`/`telefone` em todas as fixtures | só e-mails institucionais genéricos; nenhum CPF; `contact.responsavel` vazio no único registro EMBRAPII incluído |

`git status --short`:
```
A  src/radar/core/kg/equivalence.py
A  tests/fixtures/gold_equivalence/baseline_projection.json
A  tests/fixtures/gold_equivalence/bronze/fapesc_raw/fapesc_scan_fixture.json
A  tests/fixtures/gold_equivalence/bronze/fapesp_raw/fapesp_scan_fixture.json
A  tests/fixtures/gold_equivalence/bronze/finep_raw/finep_chamadas_fixture.json
A  tests/fixtures/gold_equivalence/bronze/ict_raw/embrapii_fixture.json
A  tests/fixtures/gold_equivalence/bronze/web_raw/web_scan_fixture.json
A  tests/fixtures/gold_equivalence/manifest.json
A  tests/fixtures/gold_equivalence/silver/investidores.json
A  tests/fixtures/gold_equivalence/silver/programas.json
A  tests/fixtures/gold_equivalence/silver/structured_docs/fapesc/35-2026.jsonl
A  tests/fixtures/gold_equivalence/silver/structured_docs/fapesc/35-2026.meta.json
A  tests/fixtures/gold_equivalence/silver/structured_docs/fapesp/16466.jsonl
A  tests/fixtures/gold_equivalence/silver/structured_docs/fapesp/16466.meta.json
A  tests/fixtures/gold_equivalence/silver/structured_docs/finep/602.jsonl
A  tests/fixtures/gold_equivalence/silver/structured_docs/finep/602.meta.json
A  tests/fixtures/gold_equivalence/silver/structured_docs/web/ce032edb720c.jsonl
A  tests/fixtures/gold_equivalence/silver/structured_docs/web/ce032edb720c.meta.json
A  tests/helpers/gold_projection.py
A  tests/unit/test_gold_equivalence.py
```

`git diff ed718bf5a --stat`:
```
 src/radar/core/kg/equivalence.py                   | 357 ++++++++
 .../gold_equivalence/baseline_projection.json      | 936 +++++++++++++++++++++
 .../bronze/fapesc_raw/fapesc_scan_fixture.json     |  25 +
 .../bronze/fapesp_raw/fapesp_scan_fixture.json     |  14 +
 .../bronze/finep_raw/finep_chamadas_fixture.json   |  35 +
 .../bronze/ict_raw/embrapii_fixture.json           |  25 +
 .../bronze/web_raw/web_scan_fixture.json           |  18 +
 tests/fixtures/gold_equivalence/manifest.json      | 115 +++
 .../gold_equivalence/silver/investidores.json      |  38 +
 .../gold_equivalence/silver/programas.json         |  31 +
 .../silver/structured_docs/fapesc/35-2026.jsonl    | 232 +++++
 .../structured_docs/fapesc/35-2026.meta.json       |   8 +
 .../silver/structured_docs/fapesp/16466.jsonl      |  71 ++
 .../silver/structured_docs/fapesp/16466.meta.json  |   8 +
 .../silver/structured_docs/finep/602.jsonl         | 121 +++
 .../silver/structured_docs/finep/602.meta.json     |   8 +
 .../silver/structured_docs/web/ce032edb720c.jsonl  |   9 +
 .../structured_docs/web/ce032edb720c.meta.json     |   8 +
 tests/helpers/gold_projection.py                   | 331 ++++++++
 tests/unit/test_gold_equivalence.py                | 230 +++++
 20 files changed, 2620 insertions(+)
```

Worktree limpo confirmado: nenhum arquivo rastreado pré-existente aparece no
diff (só `A`, zero `M`/`D`); todas as mudanças estão em staging (`git add`),
nenhum commit foi criado.

## Pendências

- Nenhuma dentro do escopo desta task. Fora de escopo, para tasks
  seguintes:
  - `RT01-T03` (resolução `quote → Documento Canônico/silver`) e
    `RT01-T04` (migration aditiva `provenance jsonb`) não foram tocadas.
  - `RT01-T05`/`T06`/`T12` são quem vai efetivamente usar
    `diff_projections` para comparar old-path × dual-write; este baseline
    só existe para servir de "lado old-path" fixo nessa comparação futura.
  - A ignore-list (`IGNORE_LIST_VERSION = 1`) cobre os 3 grupos previstos
    pela spec (IDs físicos, timestamps operacionais, coluna
    `provenance`); se o dual-write introduzir um campo aditivo novo fora
    desses 3 grupos, a lista precisa de bump de versão e novo baseline
    aprovado — não deve ser estendida silenciosamente.

## Auditoria (governança — Fable)

**Veredito:** aprovada em 2026-07-23.

Validação independente, sem confiar no resumo do implementador:

- diff completo inspecionado: 21 arquivos, 100% aditivos (`git diff
  ed718bf5a --cached --diff-filter=MD` vazio); `gold.py` e todos os módulos
  preexistentes intactos byte a byte;
- `equivalence.py`, `gold_projection.py` e `test_gold_equivalence.py` lidos
  integralmente; a correlação FIFO produtor→entidade foi verificada contra o
  código real de `gold.py` (todo `_upsert_entity`, inclusive o de
  `_get_agency`, recebe `embedding=embed_query(...)` avaliado imediatamente
  antes da chamada — push/pop nunca desalinham); a cobertura transitiva de
  `_get_agency` foi aceita como a leitura correta de "não reimplementar
  mapeamento no harness";
- o guard "texto vazio → 4 listas vazias" do stub confere com o
  `produce_from_text` real (inspecionado);
- testes reexecutados: 120 passed no conjunto direcionado
  (`test_gold_equivalence` + `test_provenance` + `test_etl_gold_pipeline` +
  `test_gold_mappers` + `test_gold_constraints_parser`); Ruff limpo;
  `git diff --check` limpo; coleta completa de `tests/unit` sem erro de
  import (1202 testes coletados);
- teste adversarial próprio (fora da suíte, em cópia das fixtures em
  scratchpad): adulterar o texto de um bloco silver FINEP e remover o
  investidor produziu 11 divergências (`chunk_field`, `entity_field`,
  `entity_missing`) contra o baseline congelado — o comparador detecta
  mudança real de conteúdo de ponta a ponta;
- snapshot auditado: zero vetores de embedding, zero nomes de modelo real
  (`text-embedding*`/`gpt*`/`gemini*`/`claude*` ausentes), só hashes de
  input e o rótulo fictício `stub-embedder-v1`;
- varredura de dado pessoal reexecutada: apenas contatos institucionais
  públicos de editais; a escolha do único registro EMBRAPII sem
  `contact.responsavel` foi confirmada como correta.

Nota de proveniência da entrega: o worktree continha resíduo não commitado
de um despacho anterior interrompido da MESMA task (mesmo prompt); o
implementador revisou, completou e validou esse material em vez de
reescrevê-lo. A auditoria acima valida o conteúdo final diretamente, então
essa origem mista não altera o veredito.
