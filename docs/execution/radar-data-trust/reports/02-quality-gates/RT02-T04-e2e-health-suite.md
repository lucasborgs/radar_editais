# RT02-T04 — Sinal E2E `e2e_health` diagnóstico

**Status:** `passed` (implementação; auditoria da governança pendente)
**Plano:** [`plans/02-quality-gates/RT02-T04-e2e-health-suite.md`](../../plans/02-quality-gates/RT02-T04-e2e-health-suite.md)
**Branch/commit-base:** `codex/radar-data-trust-02-t04` / `37f34a74d`
**Commits:** `ba136c695` (`feat(data-trust): e2e_health diagnostic eval signal`),
`641b6e648` (sinais E2E de state/producer observados)
**Implementador/modelo:** claude-sonnet, worktree isolado
**Auditoria Codex:** aprovada em 2026-07-24

## Realizado

- `src/radar/core/eval/e2e_health.py` (NOVO) — `Suite` diagnóstica que
  exercita um caminho mínimo e determinístico **descoberta→gold→consumo**:
  1. **descoberta/estrutura**: o silver já materializado da fixture
     `tests/fixtures/gold_equivalence/silver/structured_docs/finep/602.jsonl`
     (mesma fixture do golden de proveniência T01/T05), tratado como o
     produto discovered+structured que o gold consome — nenhum scraper/
     classificador roda;
  2. **gold**: `radar.core.kg.gold.ingest_all(sources=["edital"],
     edital_ids=["finep:602"])` — o ingest REAL, não uma reimplementação —
     rodando sob os seams herméticos de infraestrutura já construídos por
     `tests/helpers/gold_projection.py` (RT01-T02: DB in-memory, tagger LLM
     fixo, embeddings sintéticos). `_run_gold_capture()` reusa
     `GoldCaptureHarness` via import tardio (dentro do `task`, para não
     acoplar o import-time do pacote `radar.core.eval` a `tests/`) e só
     sobrepõe LOCALMENTE (subclasse, mesmo padrão de
     `test_gold_provenance_dualwrite.py::_ProvenanceCapturingHarness`) a
     captura de `entities.provenance` por chave natural + o texto do
     requisito produzido pelo stub de `constraints_producer` (trocado por um
     trecho FIXO e **verbatim no silver real** — "A duração máxima de cada
     projeto será de 2 (dois) anos.", bloco `idx=47`, página 5, doc
     `Edital.pdf` — o stub compartilhado do T02 usa outro texto, correto
     para o gate de equivalência estrutural, mas não verbatim nesta fixture,
     logo incapaz de provar aqui a sobrevivência de um fato `stated`);
  3. **consumo**: `radar.core.kg.provenance_read.public_provenance` (RT01-T10
     — a mesma função pura que um consumidor real, API/Explore, chamaria)
     projeta o envelope público a partir da `entities.provenance` capturada;
     em paralelo, `radar.core.kg.evidence_resolver.resolve_quote` re-resolve
     o MESMO quote de forma INDEPENDENTE contra o silver cru (leitura direta
     do `.jsonl`, sem depender do ingest), confirmando que a coordenada
     (`document`/`page`) exposta ao consumidor não divergiu da produzida
     pelo gold.
- Sinais diagnósticos agregados (booleans/contagens, `mean_*` — 1 caso, então
  `mean` = o próprio valor 0/1): `gold_ran`, `known_fact_stated`,
  `fact_state_present`, `producer_complete`, `consumption_present`,
  `quote_survives`, `coordinates_match`,
  `citation_count`, `layers_connected` (AND de todos os anteriores),
  `operational_error`. Nenhum threshold: `classification="diagnostic"`,
  `criteria=()`, `version="1"`.
- `prereqs`: verifica `import pytest` (extra dev, necessário só para o
  harness hermético — `pytest.MonkeyPatch.context()`), a existência do
  `.jsonl`/`.meta.json` da fixture finep/602 e do próprio
  `tests/helpers/gold_projection.py`; retorna motivo e pula honestamente
  (nunca falha obscura) se qualquer um faltar.
- `src/radar/core/eval/registry.py` — UMA linha aditiva: import de
  `e2e_health` + entrada `e2e_health.SUITE.name: e2e_health.SUITE` no dict
  `SUITES`. Nenhuma outra linha tocada (o ponto de pouso é compartilhado com
  T02, que adiciona a sua própria entrada `provenance` separadamente).
- `tests/unit/test_eval_e2e_health.py` (NOVO) — forma da `Suite` (1 caso,
  diagnóstica, `criteria=()`), `prereqs` (passa com fixtures presentes; pula
  honestamente quando a fixture não existe), caminho feliz completo via
  `run_suite` real (status `diagnostic`, todos os sinais em `1.0`,
  `operational_error=0.0`, coordenadas/quote conhecidos no `item_results`),
  determinismo (duas rodadas → agregados idênticos), e um teste adversarial
  que confirma que uma desconexão real (path de proveniência inexistente) é
  **reportada** como `False` — não mascarada nem levantada como exceção.
- `tests/unit/test_eval_harness.py` — ajuste de UMA linha em
  `test_suites_registered` (adiciona `"e2e_health"` ao set esperado):
  consequência direta e necessária de registrar a suíte nova; arquivo
  compartilhado de infraestrutura do harness, não pertence a T01/T03.

## Divergências e decisões

- **Import de `tests/helpers/gold_projection.py` a partir de `src/`
  (decisão deliberada, não um desvio silencioso).** A alternativa seria
  duplicar a lógica de stub (patches de `gold.psycopg`/`_upsert_entity`/
  `_upsert_rel`/`_replace_match_chunks`/tagger/embeddings — ~130 linhas
  cuidadosamente corretas, com correlação FIFO produtor→entidade) dentro de
  `e2e_health.py`. Isso seria exatamente o "segundo pipeline" que o
  enunciado proíbe. Em vez disso, reuso o harness já construído e
  documentado pela própria T01 para uso FORA de pytest (ver docstring de
  `tests/helpers/gold_projection.py::regenerate_baseline`, que já ensina o
  comando `PYTHONPATH=src python -c "from tests.helpers.gold_projection
  import regenerate_baseline; regenerate_baseline()"` — a mesma forma de
  import, rodando fora de pytest, que este módulo usa). O import é TARDIO
  (dentro de `_run_gold_capture`, não no topo do módulo), então
  `radar.core.eval.registry`/outros consumidores do pacote `eval` não
  carregam `tests/` a menos que a suíte `e2e_health` realmente rode.
  `tests/helpers/gold_projection.py` e o teste T05 (dono, intocado) NÃO
  foram modificados — só subclassados localmente.
- **`stub_produce_from_text` sobreposto localmente para um trecho
  verbatim conhecido.** O stub compartilhado do harness T02
  (`GoldCaptureHarness.stub_produce_from_text`) devolve um requisito FIXO
  ("Empresa brasileira em operação.") que não é substring exata de nenhum
  bloco do silver finep/602 — correto para aquele gate (equivalência
  estrutural, não prova de proveniência), mas resolveria `inferred`, não
  `stated`, se reusado aqui sem alteração. Minha subclasse local
  (`_KnownFactHarness`, só em `e2e_health.py`) sobrepõe apenas esse método
  para devolver o trecho conhecido e verbatim já usado por
  `test_gold_provenance_dualwrite.py` — mesmo padrão de subclasse local que
  aquele arquivo já usa para `stub_upsert_entity`. `constraints`/
  `exclusoes`/`publico_alvo` ficam vazios nesta sobreposição — irrelevantes
  para o sinal (o requisito é o único fato observado).
- **Nenhum consumidor produtivo foi tocado** (invariante §5.7): a suíte só
  chama `gold.ingest_all` (sob stubs herméticos, nunca contra banco real) e
  `provenance_read.public_provenance` (função pura de leitura, já existente
  desde RT01-T10) — nenhum matching/RAG/ranking/prompt/modelo entra no
  caminho.
- **`tests/unit/test_eval_harness.py::test_suites_registered` precisou de
  ajuste** porque afirma o SET EXATO de nomes registrados em `SUITES` — ao
  adicionar `e2e_health`, o teste original falha por desenho (é exatamente
  o que ele existe para detectar). Corrigido com uma linha; não há outra
  forma de registrar a suíte sem tocar esse teste.
- Nenhuma outra divergência do plano. `e2e_health.py` e o teste novo são os
  únicos arquivos genuinamente novos; `registry.py` recebe a linha prevista
  pelo plano; o ajuste em `test_eval_harness.py` é a única mudança fora do
  previsto, e é mínima e mecânica.

## Dados e migrations

- Nenhuma migration nova. Nenhum banco tocado: `gold.ingest_all` roda
  inteiramente sob os stubs do harness (psycopg substituído por um fake
  in-memory) — zero I/O de rede ou disco fora da leitura das fixtures
  versionadas em `tests/fixtures/gold_equivalence/`.

## Validação

| Comando/verificação | Resultado |
|---|---|
| `PYTHONPATH=src .venv/bin/python -m radar.core.eval run e2e_health` (1ª rodada) | `status=diagnostic`, `n_cases=1`, state presente e producer completo, além dos demais sinais, em `1.0`; `mean_operational_error=0.0` |
| mesma rodada, 2ª execução | agregado **idêntico** byte a byte ao da 1ª (`mean_gold_ran/known_fact_stated/fact_state_present/producer_complete/consumption_present/quote_survives/coordinates_match/citation_count/layers_connected=1.0`, `mean_operational_error=0.0`); confirma determinismo |
| `PYTHONPATH=src pytest -q tests/unit` | `1330 passed, 2 skipped` (2 skips pré-existentes, não relacionados) — inclui `tests/unit/test_eval_e2e_health.py` (9 testes) e o ajuste em `test_eval_harness.py` |
| `ruff check src/radar/core/eval/e2e_health.py src/radar/core/eval/registry.py tests/unit/test_eval_e2e_health.py tests/unit/test_eval_harness.py` | `All checks passed!` |
| `git diff --check` | saída vazia (sem trailing whitespace/conflitos) |
| `git diff 37f34a74d --stat` | `registry.py` (+2), `test_eval_harness.py` (+1/-1) — mudados; `e2e_health.py` e `test_eval_e2e_health.py` novos (`??`, confirmados via `git status --porcelain`) |

Escopo do diff confirmado exatamente como esperado pelo plano: nenhum
arquivo de T01/T03 (goldens de proveniência, `triage`/`structurer`/etc.,
`tests/helpers/gold_projection.py`) foi tocado.

## Pendências

- Nenhuma pendência de implementação. A suíte roda local, é determinística e
  não bloqueia CI (`diagnostic`).
- O mapa por camada (§7.4, RT02-T05) ainda não incorporou este resultado —
  fora do escopo desta task, cabe à T05.

## Auditoria Codex

**Veredito:** aprovado
