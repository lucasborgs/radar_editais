# RT01-T12 — Backfill amostral e shadow metrics

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T12-sample-backfill.md`](../../plans/01-provenance/RT01-T12-sample-backfill.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t12` / base `3d8d039ad`
**Commits:** commit único desta branch (`git log codex/radar-data-trust-01-t12`)
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

Módulo `src/radar/core/kg/provenance_backfill.py` — backfill amostral,
determinístico e idempotente de proveniência para registros gold legados
(`entities.provenance = '{}'` / `match_chunks` com coordenadas NULL), com CLI
(`python -m radar.core.kg.provenance_backfill`).

Regras de honestidade implementadas, uma função pura por tipo de fato
(spec §9.1, plano da task):

- **`requisitos_texto.<i>` (editais)** — `decide_requisito` resolve o texto
  JÁ ARMAZENADO na linha gold contra os blocos silver ATUAIS via
  `evidence_resolver.resolve_quote`; `stated` só quando `locator_quality`
  é `exact`/`document_only`; sem match → o path fica de fora (legacy), NUNCA
  grava `inferred` como fallback (diferente do produtor de ingest real, que
  atribui `inferred/llm` quando a resolução falha — o backfill não é o LLM
  que extraiu o valor, então não pode reivindicar esse produtor).
- **`status`/`mecanismo` (editais)** — `decide_status`/`decide_mecanismo`
  re-derivam com as regras determinísticas atuais
  (`gold._normalize_status`/`gold._infer_mecanismo_from_text`) sobre os
  inputs atuais (bronze/silver via `gold._edital_metadata`); gravam
  `inferred` com `derivation` SÓ quando o valor re-derivado é IGUAL ao
  armazenado; diferente ou input ausente → path fica de fora (nunca
  "conserta" o valor).
- **ICT / investidor / programa** — `decide_catalog_anchor_paths` ancora no
  registro/catálogo ATUAL (mesma construção de `provenance_writer` das
  T07/T08 — `build_ict_record_anchor`/`build_curated_catalog_anchor`), só
  quando o registro ainda existe e casa pela chave natural (`native_id`).
  ICT: `name`/`metadata.url` → `stated`. investidor/programa: campos
  copiados verbatim do catálogo (`name`/`description`/`metadata.*`) →
  `unknown` com âncora. Escopo deliberadamente restrito a isso — ver
  "Divergências e decisões".
- **`match_chunks` legados (coords NULL)** — `decide_chunk_coords`
  reempacota o silver atual (`gold._pack_chunks`, mesma construção de
  `_ingest_editais`/`_ingest_programas`) e casa por TEXTO EXATO e ÚNICO com
  o chunk armazenado; 0 ou >1 matches → `None` (nunca escolhe
  silenciosamente uma ocorrência ambígua, mesmo princípio do resolver de
  requisitos).

`producer.kind=BACKFILL` (`name="rt01_t12_backfill"`, `version="1"`) em TUDO
que o script grava — nunca reivindica o produtor histórico (llm/adapter/
deterministic) que gerou o valor original; as funções `decide_*` constroem o
`FactProvenance` diretamente (não reusam os builders `build_*_provenance` de
`provenance_writer.py`, que fixam producers `llm`/`adapter`/`deterministic`
— só reusam as partes puramente estruturais dessas funções: âncoras
(`build_ict_record_anchor`, `build_curated_catalog_anchor`) e o mapeamento
de coords (`chunk_storage_coords`), que não carregam identidade de
produtor).

Nenhuma outra coluna do gold é tocada: a orquestração (`run_backfill`) só
executa `UPDATE entities SET provenance = provenance || %s::jsonb` (merge
aditivo — nunca inclui no payload um path já presente, então nunca
sobrescreve) e `UPDATE match_chunks SET document=..., page=...,
silver_block_idx=..., source_hash=... WHERE id=%s AND document IS NULL`
(guard redundante na cláusula `WHERE`, além do filtro na aplicação).
Idempotente por construção: cada `decide_*` recebe o estado ATUAL
(`already_present`/`already_filled`) lido no início da run; uma segunda
execução vê os paths/coords já preenchidos e não os reconta nem regrava
(contador `paths_already_covered` isola essa categoria de `unresolved`,
evitando o double-count que uma primeira versão do módulo cometia — ver
"Divergências e decisões").

`--sample N` (default 5) limita, por `(origem, kind)`, quantas entidades
recebem ESCRITA em `--execute` (ordem determinística por `native_id` — a
mesma seleção em toda reexecução); o relatório shadow (`--dry-run`) sempre
cobre a população COMPLETA, não só a amostra ("medir, não prometer backfill
total"). Antes de escrever (modo `--execute`), o pré-estado das linhas
afetadas (id + provenance/coords atuais) é despejado em
`backfill_prestate_<timestamp>.json`, fora do commit.

### Arquivos

- `src/radar/core/kg/provenance_backfill.py` (novo) — módulo + CLI.
- `tests/unit/test_provenance_backfill.py` (novo) — suíte hermética.

## Divergências e decisões

- **Escopo de ICT/investidor/programa restrito ao nomeado pela task.** A
  task lista literalmente "name/url (ICT) → stated; copiados de catálogo →
  unknown com âncora" — não menciona os campos `inferred/deterministic` que
  o produtor de ingest real também grava para essas origens (`uf`,
  `setores`, `tecnologias_tags` para ICT; `setores`, `tecnologias_tags`,
  `status`, `ticket_min/max`, `mecanismo`, `formato` para
  investidor/programa). Esses campos são re-derivações sobre o registro
  curado — mesma categoria de risco que `status`/`mecanismo` de edital (o
  valor pode ter divergido do catálogo desde o ingest original) — mas a
  task não pediu a checagem de igualdade correspondente para esta origem, e
  implementá-la seria extrapolar o pedido sem instrução explícita.
  Deliberadamente NÃO implementado; documentado aqui para a governança
  decidir se vale uma task própria.
- **Bug corrigido durante a implementação (double-count em reexecução).**
  Uma primeira versão da orquestração contava TODO path com
  `already_present=True` como `unresolved` (porque `decide_*` retorna
  `None` tanto para "já coberto" quanto para "não resolvível", e o
  agregador de métricas tratava `None` uniformemente como `unresolved`).
  Isso inflaria `unresolved`/legacy a cada reexecução, mesmo para paths já
  corretamente preenchidos. Corrigido: a orquestração agora checa
  `path in existing` ANTES de chamar `decide_*` e conta em
  `paths_already_covered` (categoria própria, fora de `unresolved`) sem
  sequer invocar a função de decisão — confirmado pela saída real do
  segundo `--execute --sample 5` (ver Validação): `paths_already_covered`
  sobe de 0 para 15 e `unresolved`/`unknown` NÃO reconta os mesmos 15 paths.
- **Granularidade de `--sample`: por `(origem, kind)`, não por origem
  pura.** `entities.source='curadoria'` cobre investidor/programa/agencia
  ao mesmo tempo; aplicar uma amostra ÚNICA de N compartilhada entre
  kinds exigiria uma ordem de prioridade arbitrária entre eles. Optei por
  amostra independente de N por kind dentro da origem (mais simples,
  mais previsível, ainda bounded) — documentado no campo do relatório
  `sample_per_origin_kind` (não `sample_per_origin`, nome ajustado para
  refletir a semântica real).
- **Nenhum backfill de `entity_relationships` (arestas).** A lista de
  regras de honestidade da task não menciona arestas
  (`operado_por`/`subordinado_a`/`credenciada_por`); fora de escopo,
  intocado.

## Dados e migrations

Não aplicável — nenhuma migration nova. Migrations 041/042 (colunas
aditivas de proveniência/coords) já existiam na base `3d8d039ad`.

## Validação

### a. `--dry-run` (relatório shadow completo)

```
$ DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  PYTHONPATH=src python -m radar.core.kg.provenance_backfill
{
  "mode": "dry_run",
  "sample_per_origin_kind": 5,
  "generated_at": "2026-07-24T16:36:15.289337+00:00",
  "origins": {
    "curadoria": {
      "entities_total": 17, "entities_legacy": 17, "paths_already_covered": 0,
      "paths": {"stated": 0, "inferred": 0, "unknown": 51},
      "locators": {"exact": 0, "document_only": 51},
      "unresolved": 0, "chunks_null_coords": 0, "chunks_backfillable": 0,
      "write": {"entities_sampled": 5, "entities_written": 0, "paths_written": 0, "chunks_written": 0}
    },
    "fapesc": {
      "entities_total": 1, "entities_legacy": 1, "paths_already_covered": 0,
      "paths": {"stated": 0, "inferred": 0, "unknown": 0},
      "locators": {"exact": 0, "document_only": 0},
      "unresolved": 49, "chunks_null_coords": 42, "chunks_backfillable": 0,
      "write": {"entities_sampled": 1, "entities_written": 0, "paths_written": 0, "chunks_written": 0}
    },
    "finep": {
      "entities_total": 3, "entities_legacy": 3, "paths_already_covered": 0,
      "paths": {"stated": 0, "inferred": 0, "unknown": 0},
      "locators": {"exact": 0, "document_only": 0},
      "unresolved": 43, "chunks_null_coords": 25, "chunks_backfillable": 0,
      "write": {"entities_sampled": 3, "entities_written": 0, "paths_written": 0, "chunks_written": 0}
    }
  }
}
```

**Leitura honesta do resultado:** o Postgres local (`environment_metadata`
declara `environment=test`, `project_ref=local`) não tem NENHUM silver
estruturado (`data/silver/structured_docs/` vazio) nem bronze de editais
(`data/bronze/{finep,fapesc}_raw/` ausentes) — só `data/bronze/fapesp_raw/`
(fonte sem entidade `edital` gold no momento) e os catálogos curados
(`data/silver/investidores.json`, `programas.json`). Consequência
DETERMINÍSTICA e honesta, não um bug: os 3 editais `finep` e o 1 `fapesc`
ficam 100% `unresolved`/legacy (43 e 49 paths considerados, 0
backfilláveis; 25 e 42 chunks com coords NULL, 0 backfilláveis) — não há
silver atual contra o qual resolver quote ou reempacotar chunks, e não há
bronze contra o qual re-derivar status/mecanismo. As 17 entidades
`investidor` (origem `curadoria`) SÃO backfilláveis: `data/silver/
investidores.json` existe localmente e casa por `native_id` — 51 paths
`unknown` com âncora (17 × 3 campos copiados: `name`/`description`/
`metadata.site`), todos `locator_quality=document_only` (âncora de
catálogo, nunca `exact` — não é citação verbatim de documento). 0 entidades
`programa`/`ict` no banco local (confirmado por contagem antes de
qualquer execução: `programa`/`ict` ausentes de `public.entities`).

### b. `--execute --sample 5`

```
$ ENVIRONMENT=test DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  PYTHONPATH=src python -m radar.core.kg.provenance_backfill --execute --sample 5
{
  "mode": "execute", "sample_per_origin_kind": 5,
  "generated_at": "2026-07-24T16:40:47.424971+00:00",
  "origins": {
    "curadoria": {
      "entities_total": 17, "entities_legacy": 17, "paths_already_covered": 0,
      "paths": {"stated": 0, "inferred": 0, "unknown": 51},
      "locators": {"exact": 0, "document_only": 51},
      "unresolved": 0, "chunks_null_coords": 0, "chunks_backfillable": 0,
      "write": {"entities_sampled": 5, "entities_written": 5, "paths_written": 15, "chunks_written": 0}
    },
    "fapesc": { "...": "idêntico ao dry-run, write.entities_written=0" },
    "finep": { "...": "idêntico ao dry-run, write.entities_written=0" }
  },
  "prestate_file": "/private/tmp/radar-editais-rt01-t12/backfill_prestate_20260724T164047Z.json"
}
```

5 entidades `investidor` escritas (15 paths = 5 × 3 campos), pré-estado
salvo ANTES da escrita em
`/private/tmp/radar-editais-rt01-t12/backfill_prestate_20260724T164047Z.json`
(fora do worktree/commit — arquivo de 5 registros `{table: "entities", id,
provenance: {}}`, confirmando que as 5 linhas tinham `provenance={}` antes
da escrita).

### c. `--execute --sample 5` de novo → 0 escritas (no-op)

```
$ ENVIRONMENT=test DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  PYTHONPATH=src python -m radar.core.kg.provenance_backfill --execute --sample 5
{
  "mode": "execute", "sample_per_origin_kind": 5,
  "generated_at": "2026-07-24T16:41:30.890791+00:00",
  "origins": {
    "curadoria": {
      "entities_total": 17, "entities_legacy": 12, "paths_already_covered": 15,
      "paths": {"stated": 0, "inferred": 0, "unknown": 36},
      "locators": {"exact": 0, "document_only": 36},
      "unresolved": 0, "chunks_null_coords": 0, "chunks_backfillable": 0,
      "write": {"entities_sampled": 5, "entities_written": 0, "paths_written": 0, "chunks_written": 0}
    },
    "fapesc": { "write": {"entities_written": 0, "...": "inalterado"} },
    "finep": { "write": {"entities_written": 0, "...": "inalterado"} }
  }
}
```

`write.entities_written=0`/`paths_written=0`/`chunks_written=0` em TODAS as
origens — nenhuma chave `prestate_file` no output (o bloco de escrita nem
é alcançado quando não há pendência). `entities_legacy` cai de 17→12 e
`paths_already_covered` sobe de 0→15 (os 5 registros escritos na rodada
`b`), confirmando que a segunda rodada reconhece o estado já preenchido em
vez de recontá-lo como `unresolved` — reexecução converge.

### d. Contagens e digests antes/depois — idênticos

```
$ python -c '... select count(*) from entities/entity_relationships/match_chunks; \
              sha256 de todas as colunas de entities/match_chunks EXCETO provenance/coords ...'

ANTES (baseline, antes de qualquer --execute):
  entities count: 23
  entity_relationships count: 2
  match_chunks count: 67
  entities non-provenance digest: f7c10da333d5fc9238762d61dbfcf2442b057c8d1b4d10c5ad32bd1a6a1bd2b3
  match_chunks non-coord digest:  b8ec196c91721df3c5f38fc180bb72fce3f506d5928f4c41c76f959e1d7af034

DEPOIS (após as duas rodadas --execute --sample 5):
  entities count: 23
  entity_relationships count: 2
  match_chunks count: 67
  entities non-provenance digest: f7c10da333d5fc9238762d61dbfcf2442b057c8d1b4d10c5ad32bd1a6a1bd2b3
  match_chunks non-coord digest:  b8ec196c91721df3c5f38fc180bb72fce3f506d5928f4c41c76f959e1d7af034
  entities com provenance != '{}': 5
```

Contagens de linhas idênticas nas 3 tabelas; digest de TODAS as colunas de
`entities` exceto `provenance` (kind/source/native_id/name/description/
mecanismo/formato/setores/tecnologias_tags/status/deadline/uf/ticket_min/
ticket_max/constraints/requisitos_texto/curated/verificado_em/metadata) e
de `match_chunks` exceto as 4 colunas de coordenada
(entity_id/idx/section_path/kind/text) — idênticos byte a byte antes e
depois. Confirma que só `provenance`/coords mudaram (a coluna `embedding`,
tipo `vector`, foi deixada fora do digest por não serializar via
`json.dumps` — mas o script nunca a referencia em nenhum `UPDATE`, então
não há caminho de código que a alteraria).

### e. Suítes

```
$ PYTHONPATH=src pytest -q tests/unit
1320 passed, 2 skipped, 4 warnings in 13.72s
```

(2 skips pré-existentes, não relacionados a esta task — inclui as 15 novas
de `test_provenance_backfill.py`, todas verdes.)

```
$ PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py
................                                                         [100%]
16 passed in 0.71s
```

Gate T02 intacto.

```
$ ruff check src/radar/core/kg/provenance_backfill.py tests/unit/test_provenance_backfill.py
All checks passed!

$ git diff --check
(sem output — sem erros de whitespace)
```

`git status --short` antes do commit:

```
?? backfill_prestate_20260724T164047Z.json
?? src/radar/core/kg/provenance_backfill.py
?? tests/unit/test_provenance_backfill.py
```

(o `.json` de pré-estado NÃO entra no commit — fica no worktree local,
conforme a regra da task.)

## Pendências

- **Campos `inferred/deterministic` de investidor/programa** (`setores`,
  `tecnologias_tags`, `status`, `ticket_min/max`, `mecanismo`, `formato`) e
  de ICT (`uf`, `setores`, `tecnologias_tags`) — fora de escopo desta task
  (ver "Divergências e decisões"); backfill-los exigiria a mesma checagem
  de igualdade re-derivado-vs-armazenado que `status`/`mecanismo` de
  edital usam, sem instrução explícita da task para esta origem.
- **`entity_relationships` (arestas)** sem proveniência retroativa — fora
  do escopo nomeado pela task.
- **Editais `finep`/`fapesc`/`fapesp`/`web` permanecem 100% legacy no banco
  local** — não é uma limitação do script, é a ausência real de silver/
  bronze estruturado neste ambiente (`data/silver/structured_docs/` e
  `data/bronze/{finep,fapesc}_raw/` vazios). Rodar `--dry-run` num banco
  com o silver correspondente populado backfillaria essas origens
  normalmente (a lógica foi validada de ponta a ponta via os testes
  herméticos com fixtures de silver simuladas).

## Auditoria Codex

**Veredito:** REPROVADO para rework em 2026-07-24 (auditoria da governança — Fable). Ver seção abaixo.

## Auditoria (governança — Fable)

**Veredito:** reprovado para rework — achado material (o item, não o método).

### Achado material: shadow metrics dos editais são artefato do worktree

O relatório conclui que os 4 editais são "100% legacy, 0 backfilláveis, sem
silver/bronze contra o quê resolver". Isso é FALSO — é artefato de isolamento
de worktree, não a realidade:

- `data/silver/investidores.json` e `programas.json` são **versionados no
  git** → presentes em qualquer worktree → o backfill de investidor
  funcionou de verdade;
- `data/silver/structured_docs/` e `data/bronze/*_raw/` são **git-ignored
  (untracked)** → ausentes num worktree novo → o script mediu diretórios
  VAZIOS, não a realidade. `SILVER_DIR = ROOT/data/silver` resolve para a
  raiz do worktree.

Reexecução da governança (dry-run com o `data/` real do checkout principal
linkado ao worktree) prova que os editais SÃO backfilláveis:

| origem | inferred paths | chunks backfilláveis | stated (citações) |
|---|---|---|---|
| finep (3 editais) | 6 | 25 / 25 | **0** |
| fapesc (1 edital) | 1 | 20 / 42 | **0** |

Logo, o `--execute --sample 5` do implementador só escreveu 5 investidores
(15 paths) porque os editais falsamente pareciam vazios. Estado atual do
banco local: 5 investidores com provenance backfill; **editais e
match_chunks intocados**. A afirmação de cobertura do relatório precisa ser
refeita com dados presentes.

### Observação secundária (a explicar): stated = 0 nos editais

Com dados presentes, `requisitos_texto` não resolveu para NENHUMA citação
documental (`exact`/`document_only`) — justamente o único ponto onde o
backfill produziria evidência de página. Pode ser legítimo (requisitos
armazenados são normalizados por LLM e não aparecem verbatim no silver) ou
indicar descasamento no caminho de resolução. Não verificado — deve ser
explicado no rework, não abençoado.

### O que está sólido (não é defeito de código)

Funções de decisão, `producer.kind=backfill`, idempotência (reexecução =
0 escritas), captura de pré-estado, guard `document is null` nos chunks,
merge `provenance || jsonb`, preservação por digest das colunas
não-provenance, 31 testes herméticos verdes, gate T02 intacto. O código
resolve editais corretamente quando o `data/` está presente.

### Escopo do impacto

Divergência do ITEM (T12), não do método: T02–T11 usaram fixtures/harness
**versionados** (`tests/fixtures/gold_equivalence/`), então o isolamento de
worktree nunca os afetou — aquelas auditorias permanecem válidas. Só a T12
lê o `data/` vivo e o banco vivo, e é a única afetada.

### Correções exigidas para reaprovação

1. Rodar dry-run e `--execute` num ambiente onde `data/silver/structured_docs`
   e `data/bronze/*_raw` estejam presentes (decisão de ambiente é do
   proprietário — ver nota da governança ao proprietário);
2. refazer as shadow metrics e as conclusões do relatório com os números
   reais (a tabela acima é o ponto de partida);
3. explicar o `stated=0` dos editais (legítimo vs. bug de resolução);
4. decisão do proprietário sobre executar de fato o backfill dos editais no
   banco local (muda estado de linhas reais de edital) ou manter T12 como
   medição + amostra de investidor.

Independência: a governança executou apenas leitura (dry-run) para
diagnosticar; não escreveu no banco nem alterou o código do implementador.
Os symlinks de `data/` usados no diagnóstico foram removidos — worktree
restaurado ao estado entregue (só o `backfill_prestate_*.json` untracked).

