# Spec filha — Descoberta assistida por Deep Research

**Status:** aprovada para implementação · **Data:** 2026-08-04  
**Documento-pai:** [`product-strategy-ecosystem-pathways.md`](product-strategy-ecosystem-pathways.md)

## 1. Objetivo

Usar Deep Research para ampliar descoberta e investigação de fontes
heterogêneas, mantendo revisão humana, proveniência e o pipeline canônico.

## 2. Arquitetura

```text
Internet → pesquisa → pacote de evidências → staging → revisão
         → documento canônico → gold/KG → catálogo/Radar/RAG
```

Deep Research pode descobrir fontes, comparar documentos e propor fatos
estruturados. Nunca publica diretamente no catálogo ou no KG.

Cada resultado deve preservar URLs, trechos citados, data, fatos extraídos,
conflitos, campos ausentes, confiança e relação com a fonte original.

## 3. Prioridades do piloto

- linhas de crédito e produtos de inovação sem edital;
- FAPs com páginas ou documentos pouco estruturados;
- desafios corporativos;
- aceleradoras e incubadoras;
- ICTs e infraestrutura laboratorial do PNIPE.

## 4. Critérios de aceite

1. O piloto compara Deep Research com a descoberta atual em cobertura, precisão,
   duplicação, atualidade, custo e citações.
2. Resultados entram em staging e exigem gate humano.
3. Fatos contraditórios não são resolvidos silenciosamente.
4. A promoção usa os documentos e ingestões canônicas existentes.
5. Uma falha do Deep Research não interrompe a descoberta determinística.

