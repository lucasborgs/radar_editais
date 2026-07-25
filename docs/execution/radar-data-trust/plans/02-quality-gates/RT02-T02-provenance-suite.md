# RT02-T02 — Suíte `provenance` diagnóstica

**Objetivo:** registrar no harness existente a suíte que faltou da spec 01 §10
(a dívida encaminhada em `reports/01-provenance/README.md` §4 item e). Roda o
caminho real de resolução sobre o golden de T01 e agrega a métrica versionada
que hoje não existe.

## Entrega

Um módulo `src/radar/core/eval/provenance.py` com uma `Suite` que reusa o
caminho produtivo — sem harness paralelo, sem novo runner:

- **`task`**: para cada caso do golden, roda `evidence_resolver` (módulo puro,
  `src/radar/core/kg/evidence_resolver.py`) + a projeção de proveniência do gold
  (`src/radar/core/kg/gold.py`; stub de projeção em `tests/helpers/gold_projection.py`);
- **`evaluators`/`run_evaluators`** produzem os três sinais da spec §7.1:
  - taxa de resolução de locator (`exact` / `document_only` / `unresolved`);
  - completude de proveniência por campo crítico (estado factual + produtor);
  - faithfulness do trecho (`quote` é substring verbatim do bloco silver — reusa
    a checagem já existente, não reimplementa);
- **classificação:** `classification="diagnostic"`, `criteria=()`,
  `version="1"`. Nenhum threshold. `run` nunca bloqueia.
- registrar UMA linha em `src/radar/core/eval/registry.py` (import + entrada no
  dict `SUITES`).

Determinística por desenho (sem LLM/rede/DB — blocos silver chegam de fixture).
`prereqs` confirma apenas presença do golden/fixtures.

## Arquivos prováveis

- `src/radar/core/eval/provenance.py` (novo);
- `src/radar/core/eval/registry.py` (uma linha — ponto de pouso compartilhado com T04);
- `tests/unit/test_eval_provenance.py` (teste direcionado da suíte).

## Dependências

T01 (golden). Ponto de pouso `registry.py` compartilhado com T04 — land sequencial.

## Gate proporcional

- `python -m radar.core.eval run provenance` roda local e grava
  `eval_results/*.json` com métrica agregada;
- rodar **2x** e confirmar agregado estável (é determinística);
- teste unit direcionado + `ruff` no escopo;
- confirmar que `registry.py` continua carregando todas as suítes.

## Pare

Não adicione `criteria`/threshold nem marque `candidate`/`gate` (isso é decisão
do proprietário com baseline aceito — fora desta spec). Não reimplemente o
resolver nem a checagem de faithfulness; reuse. Não altere `gold.py`,
`evidence_resolver.py` nem qualquer produtor — a suíte observa, não muda o
pipeline. Lacuna medida (ex.: `stated=0` no caso legado) é resultado honesto.
