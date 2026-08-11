# Pacote T07-A v2 — shadow e diagnóstico factual

**Status:** Aguardando revisão humana dos goldens.

Este pacote é uma execução shadow local histórica do produtor `text-v3`, sobre
documentos públicos já adquiridos. A release RT06/RT07 incrementa
explicitamente a identidade do produtor para `text-v9`; os artifacts deste
pacote permanecem imutáveis como registro da avaliação T07-A v2. A unidade é
sujeito × documento × campo; cada linha
aparece em `review_rows.jsonl`.

- `manifest.json`: corpus, hashes, schema, produtor, targets e suíte diagnóstica.
- `summary.json`: estados factuais, schema, evidências, conflitos e métricas operacionais.
- `T07-A-human-review.md`: claims `stated`, conflitos materiais e erros detectados; nenhuma decisão está aprovada.
- `review_examples.json`: exemplos fixos para calibração inicial, todos pendentes.
- `artifacts/`: um artifact sanitizado por documento, sem prompts ou respostas brutas.

`unknown` permanece lacuna e não exige revisão humana isoladamente. O legado não
é baseline, golden ou critério de equivalência. RT04, RT05, KG, Knowledge,
consultoria e Writing não foram promovidos ou alterados. O smoke local confirmou
o carregamento do `SourceBundle` antes do read model; a ausência de bundle
continua fail-closed.
