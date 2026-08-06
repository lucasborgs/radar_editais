# Spec filha — Escopo do catálogo e desativação de investidores

**Status:** aprovada para implementação · **Data:** 2026-08-04  
**Documento-pai:** [`product-strategy-ecosystem-pathways.md`](product-strategy-ecosystem-pathways.md)

## 1. Objetivo

Reconciliar o catálogo atual com os domínios ativos da estratégia e retirar
investidores das superfícies operacionais.

## 2. Comportamento pretendido

O catálogo classifica cada registro em pelo menos um dos domínios:

- financiamento/apoio público;
- crédito para inovação;
- desafio corporativo;
- aceleradora;
- incubadora;
- ICT/laboratório/parceiro tecnológico.

Registros de investidores privados não aparecem em catálogo público, Explorar,
Radar, matching, prompts ou recomendações. Dados históricos podem permanecer
armazenados, desde que marcados como inativos e excluídos das consultas ativas.

Cada fonte deve declarar cobertura, estado de ativação, última verificação,
proveniência e qualidade conhecida.

## 3. Fora de escopo

- apagar dados históricos sem plano de migração;
- criar novos conectores de fonte;
- reescrever o matching por domínio; e
- alterar a escrita de propostas.

## 4. Critérios de aceite

1. Uma consulta ativa não retorna entidade classificada exclusivamente como
   investidor.
2. O catálogo distingue crédito de subvenção, bolsa e outros apoios.
3. Todas as FAPs permanecem representadas no registro de cobertura, mesmo quando
   ainda não houver ingestão completa.
4. Cada registro ativo mostra sua natureza e canal de acesso quando conhecido.
5. Testes cobrem exclusão de investidores em API, busca e matching.

