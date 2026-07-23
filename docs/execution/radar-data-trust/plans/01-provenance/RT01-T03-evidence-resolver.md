# RT01-T03 — Resolver de evidência

**Objetivo:** localizar quote no Documento Canônico/silver sem escolher
silenciosamente ocorrências ambíguas.

## Entrega

- resolução exata para documento, página e bloco;
- resultados `exact`, `document_only` e `unresolved`;
- detecção de trecho repetido e HTML sem página;
- hashes/versões preservados.

## Validação

- testes com trecho único, repetido, ausente e HTML;
- nenhuma chamada externa.

## Spike

Só usar spike se a ambiguidade exigir comparar algoritmos. Código aprovado deve
ser reimplementado no pacote produtivo.
