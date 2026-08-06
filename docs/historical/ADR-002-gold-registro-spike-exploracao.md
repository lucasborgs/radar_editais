# ADR-002 — Gold como sistema de registro; KG (spike) como camada de exploração

> Data: 2026-08-01
> Status: Aceito (nesta branch `spike/kg-structure-aware`; não mergeada)
> Escopo: Posição de arquitetura de dados e produto sobre o KG da nova
> ontologia/schema (spike) vs. gold + reprojeção.

---

## Contexto

O spike do KG estrutura-consciente
([`src/radar/core/kg/spike/SPEC.md`](../../src/radar/core/kg/spike/SPEC.md))
construiu um property graph em schema isolado (`kg_spike`) reprojetado do gold.
A pergunta de fundo era se essa topologia deveria **substituir** o gold
(entities/entity_relationships/match_chunks) como sistema de registro do
catálogo e do match, ou se o grafo é uma **camada derivada** para exploração.

Para responder com evidência, rodou-se uma célula A/B no funil de match v3:
baseline (texto puro) vs. boosted (texto + multiplicador estrutural dos
vizinhos `similar_a` dos matches fortes), sobre o golden de matching.
Detalhes e números na SPEC §16.

## Decisão

1. **Gold permanece o sistema de registro** do catálogo e do match. O KG do
   spike é **derivado** dele e vive como **camada de exploração**.
2. **Integração da topologia `similar_a` no funil de match está encerrada por
   evidência** (não por default): a célula A/B não apresentou lift (mrr e
   recall@10 idênticos ao baseline) e piorou levemente a janela de avaliação
   (unjudged@8 subiu). O sinal é redundante — `similar_a` é cosseno dos mesmos
   embeddings que dirigem o match de texto.
3. **A Fase 2 (extração LLM de relações não-deriváveis) fica em aberto**, como
   única frente capaz de gerar sinal estrutural novo. Se ela provar valor na
   exploração, reabre-se a avaliação (primeiro exploração, e só depois, com
   nova evidência, match). Até lá, nenhuma migração é justificada.
4. **Achado de qualidade de dados registrado:** o golden de matching, pinado em
   2026-07-05, está defasado contra o corpus atual (recall@10 baseline 0.369 <
   piso 0.55 por drift de relevantes para NEEDS_REVIEW/CLOSED, não por
   regressão do match). **Re-curadoria do golden é pré-requisito** para que
   células futuras e o gate `matching` sejam confiáveis.

## Consequências

- **Backlog:** integração do grafo no `match_v3` fica pendente de nova
  evidência; nada muda no runtime de produção (match, catálogo, explore sem
  flag).
- **Explore:** as tools `graph_explore`/`graph_reason`/`graph_community`
  permanecem atrás de `KG_SPIKE_ENABLED=1` (Design B, perfil efêmero).
- **Fase 2:** prossegue de forma independente; gate de evidência ≥3 (§9.4)
  decide a promoção de predicados novos a `core=true`.
- **Golden:** re-curadoria entra no backlog de qualidade de dados.

## Revisão futura

Esta decisão deve ser revisada se a Fase 2 produzir relações que o cosseno dos
embeddings não captura E essa topologia nova melhorar métricas de produto
(exploração, e posteriormente match) em avaliação com golden re-curado.
