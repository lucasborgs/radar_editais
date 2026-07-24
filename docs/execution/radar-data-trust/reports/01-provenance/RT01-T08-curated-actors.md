# RT01-T08 — Investidores, programas e agências

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T08-curated-actors.md`](../../plans/01-provenance/RT01-T08-curated-actors.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t08` / base `5017b052a`
**Commits:** nenhum — mudanças em staging (`git add`), commit fica para depois da auditoria da governança
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado

## Realizado

Estendida a tabela de fatos aprovada (spec §3.2/§4/§6.4, "Investidores e
programas existentes") aos 3 kinds curados (`investidor`, `programa`,
`agencia`) que ainda tinham `provenance = {}`. Princípio seguido literalmente:
**curado != validado** — todo campo copiado verbatim de `investidores.json`/
`programas.json` recebe `state=unknown` (nunca `stated`), mesmo carregando uma
âncora de evidência resolvida.

### `src/radar/core/kg/provenance_writer.py` (nova seção, ao final do módulo, antes de `__all__`)

Builders puros (sem I/O), seguindo exatamente o estilo já usado pela T05/T06:

- `_canonical_record_hash(record)` — `"md5:" + md5(json.dumps(record,
  sort_keys=True, ensure_ascii=False))`;
- `build_curated_catalog_anchor(record, *, document, source_url=None)` — UM
  `EvidenceRef` por entidade, `locator_quality=document_only`, `source="curadoria"`,
  sem `quote`, `canonical_content_hash` = hash acima;
- `build_catalog_copied_provenance(anchor)` — `unknown/human(name="curadoria")`,
  `evidence_refs=[anchor]`; usado por `name`/`description`/`metadata.*`
  copiados verbatim;
- `build_curated_derived_provenance(*, producer_name, rule, inputs)` —
  `inferred/deterministic`, sem refs; genérico, reusado por
  `setores`/`tecnologias_tags`/`status`/`ticket_min`/`ticket_max`/`mecanismo`/
  `formato` (cada chamador passa seu próprio `rule`/`inputs`);
- `build_programa_requisito_provenance(*, model)` — `inferred/llm`, **sem**
  `evidence_resolver.resolve_quote` (programas não têm blocos silver — tabela
  de fatos da task proíbe inventar resolução; nunca `stated`);
- `build_programa_operado_por_provenance()` — `inferred/deterministic`, regra
  `_split_operador:v1`, `inputs=["record.operador"]`;
- `build_agencia_name_provenance()` — `inferred/deterministic`, regra
  `_canon_agency:v1`, `inputs=["operador|source"]` (valor literal da tabela de
  fatos: a canonicalização roda tanto sobre operador de programa quanto sobre
  fonte de edital, o chamador não distingue qual disparou);
- `build_investidor_fact_provenance(record, *, setores, tecnologias_tags,
  status, ticket_min, ticket_max)` — compõe o dict `path -> FactProvenance`
  de UM investidor; "campo sem valor -> sem entrada" checado por presença no
  `record` cru (copiados) ou `is not None` (derivados);
- `build_programa_fact_provenance(record, *, ...)` — idem para programa;
  `constraints` REUSA `build_constraints_provenance` (já existente, intocada)
  quando não vazio; um `requisitos_texto.<i>` por item quando presente.
- `CURATED_INVESTIDORES_DOCUMENT = "data/silver/investidores.json"`,
  `CURATED_PROGRAMAS_DOCUMENT = "data/silver/programas.json"`.

### `src/radar/core/kg/gold.py` (único arquivo produtivo além do writer)

- `_ingest_investidores`: computa `setores`/`tags`/`status`/`tmin`/`tmax`
  antes do upsert (mesmos valores, só extraídos de expressões inline para
  variáveis reusadas), monta `entity_provenance` via
  `provenance_writer.build_investidor_fact_provenance` e passa
  `provenance=entity_provenance` ao `_upsert_entity`;
- `_ingest_programas`: idem, mais `operado_por_provenance =
  build_programa_operado_por_provenance().model_dump(...)` passado a
  `_upsert_rel(cur, eid, aid, "operado_por", provenance=operado_por_provenance)`;
- `_get_agency`: `_upsert_entity` ganha
  `provenance={"name": build_agencia_name_provenance().model_dump(...)}`.
- `_ingest_icts` **não foi tocada** (função da task irmã T07, paralela).

### `tests/unit/test_gold_provenance_sources.py`

`test_non_edital_entities_still_empty` restrita para excluir APENAS
`investidor`/`programa`/`agencia` (agora com provenance própria) — `ict`
continua exigido vazio nesta branch (T07 cuida da exclusão dele em paralelo).

### `tests/unit/test_gold_provenance_curated.py` (novo)

23 testes herméticos, dois blocos:

**(a) builders isolados** — âncora (`document_only`, hash `md5:` recalculado
e comparado, muda com o conteúdo do registro, `source_url=None` quando
ausente), campo copiado (`unknown/human`, nunca `stated` — adversarial),
campo derivado (`inferred/deterministic`), requisito de programa
(`inferred/llm`, sem refs), aresta `operado_por` de programa, `name` de
agência, e composição completa de investidor/programa (todos os paths
esperados presentes, omissão correta quando ticket/site/constraints/
requisitos ausentes, mesmo objeto de âncora compartilhado entre
name/description/metadata.site).

**(b) captura via harness T02** (subclasse local, mesmo padrão de
`test_gold_provenance_dualwrite.py`/`test_gold_provenance_sources.py` —
`gold_projection.py` intocado):

| Teste | O que prova |
|---|---|
| `test_investidor_has_expected_paths` | investidor do fixture (Indicator Capital): `name`/`description`/`metadata.site` unknown+âncora; `setores`/`tecnologias_tags`/`status` inferred; sem `ticket_min`/`ticket_max` (ticket_range null no fixture) |
| `test_programa_has_expected_paths` | programa do fixture (Centelha): 4 campos copiados unknown+âncora; `setores`/`tags`/`status`/`ticket_min`/`ticket_max`/`mecanismo`/`formato` inferred |
| `test_agencies_have_minimal_provenance` | toda agência tem só `{"name": ...}` inferred/deterministic |
| `test_programa_operado_por_edges_have_provenance` | aresta `programa:centelha->agencia:mcti` tem provenance inferred/deterministic |
| `test_editais_preserve_t06_coverage` | as 4 fontes de edital (finep/fapesp/fapesc/web) continuam com provenance — sem regressão |
| `test_ict_still_has_no_provenance` | entidade E aresta `credenciada_por` de ICT seguem vazias nesta branch |
| `test_no_catalog_field_is_stated` | adversarial: nenhum path de investidor/programa/agencia é `stated` |
| `test_two_captures_deterministic` | capturas repetidas produzem o mesmo resultado |

## Divergências e decisões

| Item | Decisão |
|---|---|
| `test_gold_provenance_dualwrite.py::test_actor_edges_have_empty_provenance_until_t07_t08` quebra para a aresta `operado_por` de programa | **Esperado e documentado, não corrigido** — o próprio nome do teste ("until_t07_t08") o marca como expirando nesta task. Mesmo padrão da transição T05→T06 (ver relatório T06, seção "Divergências"): a task autorizava tocar SOMENTE `test_gold_provenance_sources.py`; `test_gold_provenance_dualwrite.py` não está na lista de "Arquivos" da task e não foi modificado. A aresta `credenciada_por` de ICT (T07, paralela) continua vazia — a falha é restrita à linha de programa. Cobertura equivalente para o contrato vigente já existe em `test_gold_provenance_curated.py::test_programa_operado_por_edges_have_provenance`. Fica para a governança decidir se remove/reescreve esse teste supersedido, como fez ao pousar a T06. |
| `metadata.site` de investidor / `metadata.operador`,`metadata.beneficio`,`metadata.elegibilidade` de programa: incluir provenance só quando o campo bruto do `record` é truthy | Segue a regra global da task ("Campo sem valor -> sem entrada"), não só as linhas que a mencionavam explicitamente (constraints/requisitos). Evita gerar uma entrada `unknown` para um path que na prática nunca teve valor. |
| `status`/`setores`/`tecnologias_tags` sempre incluídos (mesmo quando a lista normalizada é `["Multissetorial"]`/`[]`) | Mesma convenção de `build_edital_fact_provenance` (T05): esses campos sempre têm uma saída computada (nunca `None`), então sempre entram — só ticket/mecanismo/formato/constraints/requisitos têm ausência real de valor. |
| `constraints` de programa reusa `build_constraints_provenance` sem alteração, mesmo com `derivation.inputs=["silver.eligibility_sections"]` fixo (programa não tem silver) | Instrução explícita da task ("REUSAR `build_constraints_provenance` existente"). Não criei variante nem toquei a função — o `inputs` levemente impreciso para o caso programa é um custo aceito explicitamente autorizado, não uma decisão minha. |
| `agencia`: âncora de catálogo NÃO usada (`name` recebe só `build_curated_derived_provenance`, sem `EvidenceRef`) | Agência não tem registro JSON próprio — é derivada de um token de `operador`/`source`, nunca copiada verbatim de um catálogo dedicado. Consistente com a linha da tabela de fatos ("agência: name — inferred/deterministic ... sem refs"). |

## Dados e migrations

Nenhuma migration nova. Nenhuma fixture nova (usa
`tests/fixtures/gold_equivalence/` já existente — 1 investidor, 1 programa,
1 ICT, 4 editais). As colunas `provenance::jsonb` em `entities` e
`entity_relationships` já existiam (migration 036/042, T04-T06).

## Validação

| Comando/verificação | Resultado |
|---|---|
| `PYTHONPATH=src pytest -q tests/unit/test_gold_provenance_curated.py` | 23 passed |
| `PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py` (GATE) | 16 passed — **byte-idêntico ao baseline, não regenerado** |
| `PYTHONPATH=src pytest -q tests/unit/test_gold_provenance_sources.py tests/unit/test_gold_provenance_dualwrite.py tests/unit/test_provenance.py tests/unit/test_evidence_resolver.py tests/unit/test_etl_gold_pipeline.py tests/unit/test_gold_mappers.py` | 132 passed, 1 failed (`test_actor_edges_have_empty_provenance_until_t07_t08` — esperado, ver "Divergências") |
| `ruff check src/radar/core/kg/gold.py src/radar/core/kg/provenance_writer.py tests/unit/test_gold_provenance_sources.py tests/unit/test_gold_provenance_curated.py` | All checks passed! |
| `git diff --check` | sem output — sem erros de whitespace |
| `git diff 5017b052a --stat` | `gold.py` 57 ++/--; `provenance_writer.py` 258 ++ (nova seção); `test_gold_provenance_sources.py` 7 ++/--; `test_gold_provenance_curated.py` novo (não rastreado, ~330 linhas) |
| `git diff 5017b052a --stat -- tests/helpers/gold_projection.py tests/unit/test_gold_equivalence.py src/radar/core/kg/equivalence.py tests/fixtures/gold_equivalence/` | vazio — **gate intocado** |
| `grep _ingest_icts` no diff de `gold.py` | vazio — **função da T07 intocada** |

### Saída completa dos 3 comandos de gate

```
$ PYTHONPATH=src pytest -q tests/unit/test_gold_provenance_curated.py
.......................                                                  [100%]
23 passed in 0.68s

$ PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py
................                                                         [100%]
16 passed in 0.42s

$ PYTHONPATH=src pytest -q tests/unit/test_gold_provenance_sources.py tests/unit/test_gold_provenance_dualwrite.py tests/unit/test_provenance.py tests/unit/test_evidence_resolver.py tests/unit/test_etl_gold_pipeline.py tests/unit/test_gold_mappers.py
1 failed, 132 passed in 1.17s
FAILED tests/unit/test_gold_provenance_dualwrite.py::TestHarnessCapturedProvenance::test_actor_edges_have_empty_provenance_until_t07_t08
```

## Pendências

- `test_gold_provenance_dualwrite.py::test_actor_edges_have_empty_provenance_until_t07_t08`
  precisa ser removido/reescrito pela governança (mesma sequência do
  "land RT01-T06 audit corrections") para refletir que a aresta `operado_por`
  de programa agora tem provenance — fora do escopo de arquivos autorizado
  para esta task.
- Convergência de escopo com a T07 (paralela): quando T07 remover o `ict`
  da exceção em `test_non_edital_entities_still_empty`, o merge das duas
  branches precisa reconciliar essa mesma linha (a task já avisou que isso é
  "conflito de pouso da governança").

## Auditoria Codex

**Veredito:** pendente

- **Arquivos tocados**: `src/radar/core/kg/gold.py` (modificado, só
  `_ingest_investidores`/`_ingest_programas`/`_get_agency`),
  `src/radar/core/kg/provenance_writer.py` (modificado, nova seção ao final),
  `tests/unit/test_gold_provenance_sources.py` (modificado, 1 asserção),
  `tests/unit/test_gold_provenance_curated.py` (novo);
- **Gates intocáveis**: `tests/helpers/gold_projection.py`,
  `tests/unit/test_gold_equivalence.py`, `src/radar/core/kg/equivalence.py`,
  `tests/fixtures/gold_equivalence/**` — nenhum modificado; baseline não
  regenerado; 16/16 passam;
- **`_ingest_icts` (T07)**: não tocada — confirmado por `grep` no diff;
- **Sem alteração de**: prompts LLM, migrations, RLS, score de confiança,
  estado factual novo além do previsto na tabela de fatos, consumidor lendo
  provenance, banco remoto/rede/LLM real;
- **Hashes**: prefixo `md5:` real (`hashlib.md5` sobre JSON canônico
  `sort_keys=True, ensure_ascii=False`), nunca re-hasheado para simular
  outro algoritmo, nunca fabricado;
- **Proibições respeitadas**: sem fabricação de coordenada/página/hash; sem
  `stated` em campo de catálogo (adversarial testado); nenhum builder além
  dos previstos na tabela de fatos da task; `constraints` reusa o builder
  existente sem modificá-lo, como instruído.
