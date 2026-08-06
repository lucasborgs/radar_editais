# Spec filha — ICTs, laboratórios e capacidades do PNIPE

**Status:** aprovada para implementação · **Data:** 2026-08-04  
**Documento-pai:** [`product-strategy-ecosystem-pathways.md`](product-strategy-ecosystem-pathways.md)

## 1. Objetivo

Representar ICTs, laboratórios, equipamentos e competências como capacidades de
parceria e execução conectadas a projetos e caminhos de inovação.

## 2. Fonte prioritária

O PNIPE é fonte prioritária para infraestrutura laboratorial:

`https://pnipe.mcti.gov.br/search?term=&type=LAB`

A ingestão deve preservar a URL e a evidência original, além de instituição,
laboratório, localização, equipamento, competência, condições de acesso e data
de verificação quando disponíveis.

## 3. Comportamento pretendido

O sistema permite buscar capacidades por problema ou projeto e responder:

```text
projeto → necessidade técnica → competência/equipamento
        → ICT/laboratório plausível → caminho de contato/parceria
```

A presença de uma capacidade não implica disponibilidade, preço, parceria
aprovada ou elegibilidade. Essas condições devem permanecer explícitas.

## 4. Fora de escopo

- afirmar completude nacional;
- negociar ou iniciar contato automaticamente;
- tratar laboratório como edital;
- inferir disponibilidade atual sem fonte.

## 5. Critérios de aceite

1. ICTs e laboratórios aparecem como capacidades, não como oportunidades de
   financiamento.
2. Um projeto pode recuperar competências e infraestrutura relacionadas.
3. Cada capacidade possui proveniência e data de atualização quando possível.
4. Ausência de uma ICT no índice não é apresentada como ausência no Brasil.

