# RT04-T01 — Contrato `SourceBundle` e fixtures representativas

## Objetivo

Definir o envelope puro `SourceBundle`, seus vocabulários, validação e hashes
SHA-256 determinísticos. Fixar fixtures mínimas: Web portal + desafio, FAPESC
base + retificação e ator com material insuficiente. Não persistir nem alterar
produtores/consumidores.

## Dependências e pouso

Somente a Spec 04. Pousa no Documento Canônico de `SourceAdapter`; vocabulário
normativo vai para `docs/domain/schema.md`, não para constante paralela.

## Arquivos prováveis

- `docs/domain/schema.md` e, se necessário, `docs/domain/sources/fapesc.md`;
- `src/radar/core/kg/source_bundles.py` (novo, tipos/validador/hash puros);
- `tests/fixtures/source_bundles/` e `tests/unit/test_source_bundles.py`.

## Passos delimitados

1. Validar versão, kind/ID canônico, fonte, coleta, produtor, status e um
   documento válido; aceitar somente papéis/estados da spec.
2. Normalizar envelope/documentos de modo estável; recoleta idêntica mantém
   hash, conteúdo/papel/autoridade materialmente alterados criam hash novo.
3. Criar as três fixtures obrigatórias e rejeições para hash/papel/estado/ID
   inválidos e documento vazio. `partial` é válido, mas não ganha semântica
   de projeção nesta task.

## Testes proporcionais

- fixtures válidas/inválidas, estabilidade e mudança material do hash;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_bundles.py`;
- `ruff check` no escopo e `git diff --check`.

## Pare

Novo actor kind, URL/página inventada, lista normativa em Python, precedência ou
mudança de `FactProvenance` retornam à governança/T06.

## Não objetivos

Sem tabela, dual-write, adapter, composição, proveniência, rede, crawler, LLM,
OCR/visão ou backfill.

## Relatório esperado

`reports/04-source-bundles/RT04-T01-report.md`: vocabulário, IDs das fixtures,
hashes/validação, commit/base, testes e ambiente hermético.
