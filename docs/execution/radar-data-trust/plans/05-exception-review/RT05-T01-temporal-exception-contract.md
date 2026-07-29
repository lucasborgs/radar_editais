# RT05-T01 — Contrato temporal e de exceção

## Objetivo

Definir o contrato puro para validade de oportunidade e exceção temporal, sem
banco, rota, gold ou consumidores. A fixture Finep/Eureka fixa `ABERTA` sem
prazo como `needs_review`.

## Dependências

Spec 05 aprovada. Reutiliza `FactProvenance`, `EvidenceRef`, `ReviewInfo` e
bundles das Specs 01/04; não os altera sem divergência comprovada.

## Arquivos prováveis

- `docs/domain/schema.md`;
- `src/radar/domain/data_quality.py` (novo) e `src/radar/domain/__init__.py`;
- `tests/fixtures/data_quality/finep_eureka.json` e
  `tests/unit/test_temporal_exception_contract.py` (novos).

## Passos

1. Declarar `TemporalMode`, `ValidityState`, os seis `issue_code` da spec,
   rascunho de exceção e decisão de revisão, com valores canônicos e Pydantic
   estrito; não criar score ou taxonomia aberta.
2. Implementar função determinística que recebe facts/evidências, `as_of` e
   timezone. Aplicar a tabela §4.1: prazo, fechamento, continuidade explícita,
   ausência e conflito; conflito tem precedência.
3. Reconciliar no schema a regra `deadline >= hoje` e o fim do dia em São
   Paulo. A função recebe o relógio; não usa `date.today()` no núcleo.
4. Criar fixture sanitizada Finep/Eureka e casos locais materiais, sem rede,
   HTML integral ou URL capturada.

## Invariantes

- Prazo nulo é `unknown`, não `continuous`; continuidade requer evidência
  oficial recuperável.
- Não fabricar data, horário, status ou citação.
- Esta task não persiste exceção nem muda comportamento produtivo.

## Testes mínimos

- prazo futuro, vencido e igual a `as_of`; contínuo comprovado; fechado sem
  prazo; Finep/Eureka; conflito;
- rejeitar continuidade sem evidência, código desconhecido e dado inválido;
- `ENVIRONMENT=test pytest -q tests/unit/test_temporal_exception_contract.py`,
  `ruff check` no escopo e `git diff --check`.

## Critérios de aceite

- Finep/Eureka produz `unknown`, `needs_review` e
  `temporal_status_without_basis`;
- somente continuidade comprovada resulta ativa sem deadline;
- a regra do prazo que vence hoje é única e documentada.

## Proibições

Sem migration, fila, repositório, API, frontend, alteração em `gold.py` ou
`match_v3.py`, scraper, LLM, OCR/visão, rede ou backfill.

## Pare se

For necessário deduzir continuidade por ausência, escolher precedência sem
evidência recuperável ou alterar `FactProvenance` de forma incompatível.
