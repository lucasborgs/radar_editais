# RT01-T04 — Persistência aditiva

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T04-additive-storage.md`](../../plans/01-provenance/RT01-T04-additive-storage.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t04` / `e40833685`
**Commits:** nenhum — mudanças em staging (`git add`), commit fica para depois da auditoria da governança
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

- `supabase/migrations/042_provenance_columns.sql` — estritamente aditiva e
  idempotente (`if not exists` em toda alteração):
  - `public.entities.provenance jsonb not null default '{}'::jsonb`;
  - `public.entity_relationships.provenance jsonb not null default '{}'::jsonb`;
  - `public.match_chunks.document text`, `.page int`, `.silver_block_idx int`,
    `.source_hash text` — todas nullable (`NULL` = chunk legado, conforme
    plano);
  - `comment on column` em cada coluna nova, referenciando §4.2/§4.3/§6.1/§6.2
    de `docs/specs/radar-data-trust-01-provenance.md`;
  - nenhum índice novo, nenhuma policy/grant/RLS, nenhum drop/rename/alter de
    coluna existente.
- `tests/integration/test_provenance_storage.py` — 6 testes de integração
  gated em runtime (sonda de conectividade real a `postgresql://postgres:
  postgres@127.0.0.1:54322/postgres`, skip claro se não responder — não gated
  por presença de env var, já que `tests/conftest.py` zera `DATABASE_URL` por
  padrão fora de `INTEGRATION_TARGET`):
  1. `TestEntitiesProvenanceDefault` — insert de entidade mínima sem
     mencionar `provenance` → `select` devolve `{}` (default aplicado,
     "legado" identificável, spec §9.1);
  2. `TestEntitiesProvenanceRoundTrip` — `update` setando `provenance` =
     `model_dump(mode="json")` de um `FactProvenance` REAL
     (`radar.domain.provenance`, `producer.kind=adapter`, um `EvidenceRef`
     com `silver_source_hash="md5:<hex>"`) → `select` devolve JSON idêntico
     e `FactProvenance.model_validate(stored) == fp` (round-trip completo,
     não só JSON solto);
  3. `TestEntityRelationshipsProvenanceRoundTrip` — mesmo round-trip para
     `entity_relationships.provenance`, com duas entidades reais criadas na
     mesma transação e uma aresta `operado_por` entre elas;
  4. `TestMatchChunksNewColumns` — insert em `match_chunks` sem as 4 colunas
     novas → todas `NULL`; insert com `document/page/silver_block_idx/
     source_hash` preenchidos → round-trip íntegro;
  5. `TestGoldEntityUpsertCompatibility` — o SQL literal de
     `gold._ENTITY_UPSERT` (import direto do módulo, sem reimplementar a
     query) executado 2x com os mesmos params (`gold._vec` para o embedding
     dummy 1536d) resolve para a mesma linha (`id_first == id_second`) e a
     `provenance` da linha permanece no default `{}` — o upsert não menciona
     a coluna nova, comportamento aditivo confirmado sem regressão.

  Todas as sondas rodam dentro de uma transação por teste (`pg_conn` fixture)
  que **nunca commita** e sempre faz `conn.rollback()` no `finally` do
  teardown — inclusive se o teste falhar/lançar. Zero resíduo verificado por
  contagem de linhas antes/depois (ver "Dados e migrations").

## Divergências e decisões

- **Gate de conectividade em runtime, não em presença de env var.** Os
  padrões existentes (`test_entity_catalog.py`, `test_company_chunks_rls.py`)
  fazem `skipif` sobre a AUSÊNCIA de `SUPABASE_URL`/`DATABASE_URL` no
  ambiente. O enunciado desta task pede explicitamente "skip claro se o
  Postgres local :54322 não responder (**skip em runtime**)" e o comando de
  validação mandatório (`PYTHONPATH=src pytest -q
  tests/integration/test_provenance_storage.py`) não passa nenhuma env var.
  Como `tests/conftest.py` zera `DATABASE_URL=""` por padrão (isolamento
  hermético da suíte comum, fora de `INTEGRATION_TARGET`), o teste faz uma
  sonda de conexão real (`psycopg.connect(DSN, connect_timeout=3)` +
  `select 1`) contra o DSN local padrão quando a env está ausente/vazia, e
  usa o resultado dessa sonda (não a presença da env) como critério de
  `skipif`. Decisão registrada porque diverge do padrão dos dois arquivos de
  integração existentes — mas é o que o enunciado desta task pede
  literalmente, e o comando de validação #5 só funciona com esse gate.
- **`ruff check` não se aplica ao arquivo `.sql`.** O invariante #7 pede
  `ruff check` "nos arquivos novos", mas Ruff é um linter Python — rodá-lo
  contra `042_provenance_columns.sql` produz apenas erros de parse Python
  (SQL não é Python). Rodei `ruff check` só no arquivo Python novo
  (`test_provenance_storage.py`), que é o único arquivo novo lintável por
  Ruff. Nenhum linter SQL está configurado no repositório para este tipo de
  arquivo.
- Nenhuma outra divergência do plano/spec.

## Dados e migrations

- `supabase/migrations/042_provenance_columns.sql` aplicada 2x (idempotência)
  no Postgres local (`127.0.0.1:54322`), via `psycopg` direto — nenhum
  `supabase db reset`/`db push`/`migration repair` usado.
- Nenhum dado foi criado/alterado permanentemente: toda escrita de teste
  (integração) ocorreu dentro de transações revertidas. Contagens de linhas
  idênticas antes e depois de toda a sessão (23 / 2 / 67), confirmadas por
  consulta direta após rodar a suíte completa.
- Nota de rollout (pedida pelo enunciado): `_replace_match_chunks`
  (`src/radar/core/kg/gold.py`, delete+insert por `entity_id`) não menciona
  as 4 colunas novas — um re-ingest de gold antes de T05/T06 escreverem essas
  colunas vai apagá-las e recriá-las como `NULL` para os chunks daquele
  `entity_id`. **Esperado e aceito**: é exatamente o comportamento "chunk
  legado" que a spec §9.1 define até um produtor em dual-write escrever essas
  coordenadas.

## Validação

Sequência executada na ordem pedida pelo enunciado, com outputs reais.

### 1. Snapshot ANTES

`pg_policies` (3 tabelas):
```
('public', 'entities', 'entities_read_authenticated', 'PERMISSIVE', ['authenticated'], 'SELECT', 'true', None)
('public', 'entity_relationships', 'entity_relationships_read_authenticated', 'PERMISSIVE', ['authenticated'], 'SELECT', 'true', None)
('public', 'match_chunks', 'match_chunks_read_authenticated', 'PERMISSIVE', ['authenticated'], 'SELECT', 'true', None)
```

`information_schema.columns` — nenhuma das tabelas tinha as colunas novas
(`entities` sem `provenance`; `entity_relationships` sem `provenance`;
`match_chunks` sem `document/page/silver_block_idx/source_hash`).

Contagens ANTES:
```
entities 23
entity_relationships 2
match_chunks 67
```

### 2. Aplicar a migration (1ª vez)

```
Migration applied OK (1st run)
```
(via `psycopg.connect(DSN, autocommit=True); cur.execute(sql)` lendo o
arquivo `042_provenance_columns.sql` — DSN sempre `127.0.0.1:54322`).

### 3. Reaplicar o mesmo arquivo (idempotência)

```
Migration re-applied OK (2nd run, idempotent)
```
Sem erro — todos os `add column if not exists` e `comment on column` são
idempotentes por natureza.

### 4. Snapshot DEPOIS

`pg_policies` — **idêntico** ao snapshot ANTES (mesmas 3 linhas, byte a
byte):
```
('public', 'entities', 'entities_read_authenticated', 'PERMISSIVE', ['authenticated'], 'SELECT', 'true', None)
('public', 'entity_relationships', 'entity_relationships_read_authenticated', 'PERMISSIVE', ['authenticated'], 'SELECT', 'true', None)
('public', 'match_chunks', 'match_chunks_read_authenticated', 'PERMISSIVE', ['authenticated'], 'SELECT', 'true', None)
```

Colunas novas presentes com defaults/nullability corretos:
```
entities.provenance              → jsonb, NOT NULL, default '{}'::jsonb
entity_relationships.provenance  → jsonb, NOT NULL, default '{}'::jsonb
match_chunks.document            → text,    NULL, sem default
match_chunks.page                → integer, NULL, sem default
match_chunks.silver_block_idx    → integer, NULL, sem default
match_chunks.source_hash         → text,    NULL, sem default
```

Contagens DEPOIS — **idênticas** às de ANTES:
```
entities 23
entity_relationships 2
match_chunks 67
```

Spot-check adicional (nenhum dado pré-existente alterado pela migration):
```
entities com provenance != '{}' (deveria ser 0): 0
entity_relationships com provenance != '{}' (deveria ser 0): 0
match_chunks com alguma coluna nova não-NULL (deveria ser 0): 0
```

### 5. `PYTHONPATH=src pytest -q tests/integration/test_provenance_storage.py`

```
......                                                                   [100%]
6 passed in 0.36s
```

Contagens re-verificadas **depois** de rodar toda a suíte de integração
(prova de zero resíduo mesmo após os 6 testes que fazem insert/update):
```
entities 23
entity_relationships 2
match_chunks 67
leftover rt01t04 rows in entities (should be 0): 0
```

### 6. `PYTHONPATH=src pytest -q tests/unit/test_provenance.py tests/unit/test_gold_equivalence.py tests/unit/test_evidence_resolver.py`

```
........................................................................ [ 84%]
.............                                                            [100%]
85 passed in 0.65s
```
85 passed — projeção T02 preservada, nenhum código tocado (a suíte confirma:
nenhum arquivo de `src/radar/**` está no diff desta task).

### 7. `ruff check` + `git diff --check`

```
$ ruff check tests/integration/test_provenance_storage.py
All checks passed!
```
(`ruff check` não se aplica a `.sql` — ver "Divergências e decisões"; a
migration não tem um linter SQL configurado no repositório.)

```
$ git diff --check
(saída vazia — sem trailing whitespace / conflitos)
```

### Invariantes confirmados

```
$ git diff e40833685 --diff-filter=MD --stat
(saída vazia — nenhum arquivo existente modificado ou removido)

$ git diff e40833685 --stat
 supabase/migrations/042_provenance_columns.sql |  74 +++++++
 tests/integration/test_provenance_storage.py   | 282 +++++++++++++++++++++++++
 2 files changed, 356 insertions(+)
```

`git status --short`:
```
A  supabase/migrations/042_provenance_columns.sql
A  tests/integration/test_provenance_storage.py
```

Worktree limpo confirmado: só arquivos novos (`A`), zero `M`/`D`; tudo em
staging (`git add`), nenhum commit criado.

## Pendências

- Nenhuma dentro do escopo desta task. Fora de escopo, para tasks seguintes:
  - `RT01-T05` (vertical slice FINEP em dual-write) é quem primeiro escreve
    `provenance`/`match_chunks.document|page|silver_block_idx|source_hash`
    de fato via um produtor real — esta task só preparou o storage;
  - re-ingest de gold antes de T05/T06 zera as 4 colunas novas de
    `match_chunks` para os chunks recriados (nota de rollout registrada
    acima) — comportamento esperado, não uma regressão a corrigir aqui.

## Auditoria (governança — Fable)

**Veredito:** aprovada em 2026-07-23.

Validação independente, sem confiar no resumo do implementador:

- migration e teste de integração lidos integralmente; diff 100% aditivo
  (3 arquivos novos, zero `M`/`D`); nenhuma migration anterior, gold.py ou
  fixture tocados;
- **terceira aplicação** do arquivo 042 executada pela auditoria no banco
  local: idempotente, sem erro;
- colunas e defaults verificados via information_schema: `provenance jsonb
  not null default '{}'` nas duas tabelas; 4 colunas nullable em
  `match_chunks` — exatamente a spec §6.1/§6.2, nada além;
- `pg_policies` das 3 tabelas: apenas as 3 policies SELECT/`authenticated`
  da migration 036, nenhuma adição/mudança;
- contagens preservadas (23/2/67) e zero resíduo de teste
  (`native_id like 'rt01t04%'`, `source in (test_source, ...)` → 0);
- **ataque de RLS próprio**: como role `authenticated`, UPDATE em
  `entities.provenance` afeta 0 linhas (nenhuma linha visível para
  escrita) e INSERT é rejeitado com violação de RLS
  (`InsufficientPrivilege`); leitura continua funcionando. Escrita
  permanece service-role-only, como antes da migration;
- suítes reexecutadas: 91 passed (6 integração + 57 provenance + 16
  equivalence + 12 resolver); Ruff e `git diff --check` limpos;
- nota de ambiente (fora do escopo da task, ação da governança): o Docker
  Desktop do host estava pendurado há ~10h e foi reiniciado com
  autorização do proprietário antes desta task; produção e staging
  voltaram sozinhos e o Supabase local foi iniciado pela governança — o
  implementador operou com o banco já de pé, sem tocar no stack.
