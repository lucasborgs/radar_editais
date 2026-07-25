# RT02-T04 — Sinal E2E `e2e_health` diagnóstico

**Objetivo:** registrar um sinal de saúde ponta a ponta (§7.2): um caminho
mínimo e determinístico descoberta→gold→consumo que confirma que as camadas
conectam e que um fato sobrevive end-to-end **com sua proveniência** — não uma
matriz de casos.

## Entrega

Um módulo `src/radar/core/eval/e2e_health.py` com uma `Suite`:

- **`task`**: exercita um caminho mínimo sobre fixture/banco local — um fato
  conhecido atravessa gold→consumo preservando estado factual e coordenadas de
  origem (as verificáveis pelo pipeline atual);
- **sinais diagnósticos** (não thresholds): camadas conectaram? o fato
  sobreviveu? a proveniência veio junto? — booleans/contagens agregados;
- **determinismo:** sem LLM, sem rede, sem prod. Roda contra fixtures/banco local;
  `prereqs` declara e pula honestamente se o pré-requisito local faltar (nunca
  falha obscura no meio da rodada);
- **classificação:** `classification="diagnostic"`, `criteria=()`, `version="1"`;
- registrar UMA linha em `src/radar/core/eval/registry.py`.

Reusa fixtures existentes (`tests/fixtures/gold_equivalence/…`) e um edital já
presente no golden — não cria pipeline novo nem corpus grande.

## Arquivos prováveis

- `src/radar/core/eval/e2e_health.py` (novo);
- `src/radar/core/eval/registry.py` (uma linha — ponto de pouso compartilhado com T02);
- `tests/unit/test_eval_e2e_health.py` (teste direcionado).

## Dependências

Nenhuma (só o harness). Onda A. Ponto de pouso `registry.py` compartilhado com
T02 — land sequencial.

## Gate proporcional

- `python -m radar.core.eval run e2e_health` roda local e grava resultado;
- rodar **2x** e confirmar agregado estável (determinística);
- teste unit direcionado + `ruff` no escopo.

## Pare

Não construa uma matriz de casos nem um segundo pipeline — é um sinal mínimo.
Nada de LLM/rede/prod sem autorização. Sem threshold, sem `gate`. Se o caminho
mínimo exigir tocar um consumidor (matching/RAG/ranking), PARE — a avaliação
observa, não altera (invariante §5.7). Camada que não conecta é sinal a
reportar, não a contornar.
