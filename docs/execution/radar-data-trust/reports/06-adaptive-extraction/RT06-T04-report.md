# RT06-T04 — Diagnóstico de layout/OCR/visão

**Status:** `passed`
**Decisão:** `no_escalation`
**Plano:** [RT06-T04](../../plans/06-adaptive-extraction/RT06-T04-layout-ocr-diagnosis.md)

## Método e resultado

- Foram usados quatro documentos representativos locais: FINEP e FAPESC em
  PDF textual, FAPESP em HTML e Web em HTML.
- A rota textual canônica foi exercitada com um cliente LLM falso somente na
  fronteira da chamada; a evidência foi escolhida a partir do texto enviado,
  não injetada por um extractor que devolvesse uma quote pré-selecionada.
- O bucket `spec06_signals.layout_or_ocr_candidates` foi calculado junto dos
  demais buckets RT05. No corpus disponível, todos os buckets diagnósticos
  ficaram vazios e nenhum caso material reproduzível justificou escalada.

## Decisão

Conclusão reproduzível: **nenhuma necessidade medida no corpus disponível**.
Não há ganho medido que justifique adicionar layout, OCR, visão ou novas
dependências nesta etapa. T05 fica `not_applicable` e não implementada.

## Limitações

O corpus local não prova cobertura de documentos escaneados nem substitui um
golden humano. Uma nova amostra material deve reabrir o diagnóstico.
