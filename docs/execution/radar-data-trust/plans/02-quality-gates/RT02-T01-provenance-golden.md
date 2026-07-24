# RT02-T01 — Golden representativo de proveniência

**Objetivo:** montar o corpus mínimo que exercita o caminho real de resolução de
proveniência (§7.1), UM caso por tipo obrigatório enumerado na spec §7.1. Só
fixtures — nenhum código de suíte (isso é T02).

## Escopo dos casos (spec §7.1)

Um caso por tipo, cada um ancorado em blocos silver reais/fixados:

1. **trecho único e exato** — resolve `exact` com `page`/`block_idx`;
2. **trecho repetido em duas páginas** — resolve `document_only` (página ambígua);
3. **HTML sem página** — blocos `page=None` → `document_only`;
4. **valor normalizado** (moeda/data) — texto do bloco difere do valor gold;
   estado factual presente, faithfulness sobre o `quote` real;
5. **campo ausente** — fato crítico do escopo §3.1/§3.2 da spec 01 sem evidência;
6. **registro legado sem silver recuperável** — sem bloco → `unresolved`/`stated=0`
   (lacuna honesta, não mascarada — invariante spec §5.6).

`conflicting` e `retificação` (spec 01 §10.2) ficam **fora**: a própria spec 01
os encaminhou à spec 04 (nenhum produtor os emite). Ver flag de reconciliação no
relatório de fechamento (T05).

## Entrega

- `data/evaluation/golden/provenance/` com os casos acima (JSON, formato dos
  goldens existentes: `case_id`, `input`, `expected_output`, `metadata`);
- cada caso referencia blocos silver de `tests/fixtures/gold_equivalence/silver/`
  (reuso) ou fixture nova mínima quando o tipo exigir (HTML sem página, legado);
- `expected_output` declara o `locator_quality` esperado, o estado factual por
  campo crítico e o `quote` verbatim esperado — a régua vem do caso, não da suíte;
- `manifest.json` do golden (como em `golden/relevance/`) listando os casos.

## Arquivos prováveis

- `data/evaluation/golden/provenance/*.json` (novos);
- fixtures silver mínimas em `tests/fixtures/gold_equivalence/silver/…` só se um
  tipo não tiver bloco reaproveitável.

## Dependências

Nenhuma. Onda A (paralela a T03, T04).

## Gate proporcional

- validar que cada caso carrega e casa com o formato esperado por um teste de
  carga direcionado (sem rodar a suíte ainda);
- UMA fixture por caso — sem variações redundantes;
- `ruff` no escopo (se houver helper de carga em Python).

## Pare

Não fabrique `page`, `quote` ou hash para o caso legado/ausente — a ausência é o
resultado correto. Não invente threshold nem estado esperado que o pipeline real
não produz. Dúvida sobre quais campos críticos entram no caso "campo ausente"
volta ao proprietário (o escopo de campos é da spec 01 §3.1/§3.2, não desta task).
