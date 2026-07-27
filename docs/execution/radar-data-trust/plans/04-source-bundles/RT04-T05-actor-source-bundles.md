# RT04-T05 — Bundles das fontes conhecidas de atores

## Objetivo

Versionar fontes já conhecidas de ICTs, investidores, programas e agências:
EMBRAPII é `official_record`, catálogos são `curated_record`, e página oficial
só entra se já estiver no registro. Falta de conteúdo continua `unknown`.

## Dependências e pouso

Depende de T01–T02. Pousa nos JSONs/bronze já consumidos por `gold.py` e nas
âncoras de `provenance_writer.py`; não cria SourceAdapter artificial para ator.

## Arquivos prováveis

- `src/radar/core/kg/gold.py`, `provenance_writer.py` e `source_bundles.py`;
- `data/silver/investidores.json`, `data/silver/programas.json` e fixture
  EMBRAPII apenas se faltar exemplo mínimo;
- testes gold/proveniência e fixtures de T01.

## Passos delimitados

1. Construir bundle por sujeito a partir do registro existente: ICT oficial,
   investidor/programa curado, agência só do registro que já a sustenta.
2. Gravar append-only best-effort preservando source/identidade; curadoria e URL
   declarada não viram prova oficial de campo.
3. Reusar âncoras existentes e manter ausência como `unknown`/ausente. Não criar
   chunks, RAG, descrição sintética, LLM ou fato sem mudança material.
4. Preservar `operador` como metadado/relação existente; empresa não vira
   `agencia` nem surge actor kind novo.

## Testes proporcionais

- uma fixture por kind, incluindo ator incompleto; hash/idempotência e
  proveniência `unknown`; testes direcionados, `ruff` e diff check.

## Pare

Página que exige rede/crawler, identidade/papel incerto, novo kind ou campo só
preenchível por inferência requer decisão de produto; não fabricar conteúdo.

## Não objetivos

Sem nova relação de negócio, página completa obrigatória, RAG de ator,
classificador de oportunidade, LLM, backfill ou fila.

## Relatório esperado

`reports/04-source-bundles/RT04-T05-report.md`: fixtures por kind, papéis reais,
campos `unknown`, relações preservadas, commit/base, testes e ambiente.
