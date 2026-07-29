# RT05-T04 — Serviço de revisão e projeção temporal

## Objetivo

Implementar decisão humana e a projeção temporal única: fato original,
exceção e revisão válida. Não expõe endpoint nem muda telas.

## Dependências

RT05-T01 a T03.

## Arquivos prováveis

- `src/radar/core/services/data_quality_reviews.py` (novo) e repositório T02;
- `src/radar/core/services/temporal_quality.py`;
- `src/radar/domain/provenance.py` somente se extensão aditiva estrita for
  indispensável;
- `tests/unit/test_data_quality_reviews.py` e
  `tests/unit/test_temporal_validity_projection.py` (novos).

## Passos

1. Validar `confirm`, `correct`, `mark_unknown` e `confirm_continuous` contra
   campo, valor e evidência existente. Ação válida grava revisão append-only
   com `actor_id`, data e versão do contrato.
2. Derivar projeção: confirmação aceita fato verificável; correção produz
   proveniência humana vinculada a revisão/valor anterior; nova versão material
   não herda override e volta à avaliação.
3. Expor read model interno com estado, modo, valor seguro e referência. Não
   gravar cópia em `entities`.
4. Ausência, exceção aberta e falha de leitura de fato temporal revalidado são
   conservadoramente `needs_review`.

## Invariantes

- Correção/continuidade exigem evidência documental versionada; URL ou texto
  colado não é evidência.
- `mark_unknown` não vira contínuo; `confirm` não troca valor.
- Override é projeção, não mutação de bundle, bronze, gold histórico ou saída
  do produtor.

## Testes mínimos

- quatro decisões válidas; correção/continuidade sem evidência rejeitadas;
- Finep/Eureka permanece `needs_review`; correção muda só projeção; fingerprint
  novo reabre;
- proveniência humana preserva revisão e derivação;
- testes T01–T03 relevantes, `ruff check` e `git diff --check`.

## Critérios de aceite

- uma única função/serviço devolve validade corrente;
- revisão permanece recuperável e não reescrita;
- nenhum consumidor muda antes de T07.

## Proibições

Sem router, frontend, alteração de `entities`, match, prompt, OCR/visão, LLM,
rede ou feedback automático.

## Pare se

Uma revisão exigir sobrescrever histórico, `ReviewInfo` não puder representar
autoria, ou versão nova não puder ser distinguida por entrada material.
