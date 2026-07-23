# RT00-T02 — Goldens representativos

**Objetivo:** criar uma amostra pré-beta pequena, revisável e separada por
`kind`, sem buscar cobertura exaustiva.

## Escopo

- reaproveitar casos válidos do golden atual de triagem;
- adicionar casos mínimos de ICT EMBRAPII, investidor, programa e agência;
- cobrir `in_scope`, `out_of_scope` e `needs_review`;
- registrar fonte, label humano e reason codes.

## Entrega

- datasets versionados no seam de goldens;
- manifesto com IDs esperados e distribuição por `kind`;
- nenhuma saída LLM usada como label final.

## Validação

- loader hermético e teste de completude dos IDs;
- revisão humana dos casos novos pelo proprietário.

## Pare

Não rotule por inferência um caso cujo material oficial seja insuficiente;
marque `needs_review` e peça decisão de produto quando necessário.
