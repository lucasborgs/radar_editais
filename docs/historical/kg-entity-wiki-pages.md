# Spec: KG cross-dimensional + qualidade da torneira

**Status:** ativa (2026-06-13). Reescreve a mini-spec de "wiki pages de entidade"
para o alvo real, definido pelo feedback do grafo Obsidian: **o grafo vira uma
base de conhecimento que o chat de Descoberta consulta através de TODAS as
dimensões** (editais, desafios, ICTs, investidores). Ciclo combinado Track 1+2
(decisão de Lucas, 2026-06-13).

## Norte (ponto 4)

O chat de Descoberta (explore, ANTES do match) deve responder, por tema/setor,
através das quatro dimensões. Ex.: "quais oportunidades em agronegócio?" →
editais + desafios abertos + ICTs parceiras + investidores com tese no setor.

Hoje o explore tem 4 tools (`list_editais`, `get_edital`, `find_analogues`,
`find_ict_partners`): alcança **editais** e "ICTs parceiras *de um edital*", mas
não consulta ICTs por tema de forma autônoma e **não conhece investidores**.

## Estado atual (verificado)

| Dimensão | Representação | Wiki page? | Match | Explore |
|---|---|---|---|---|
| Edital/desafio/programa | `wiki/<fonte>/<id>.json` + index.json | sim | radar+hybrid | sim |
| ICT (EMBRAPII) | `icts.json` consolidado | não | `ict_match` | só "parceiras de edital" |
| Investidor | `investidores.json` curado | não | `investor_match` no radar | **ausente** |

Ponte = nó `tema` (§6.1.2: sem aresta direta edital↔ICT). Mas o normalizador
fino→macro do `build_ict_graph` deixou **140 áreas sem tema-macro** (→ ICTs
órfãos no grafo, ex.: as unidades de mineração). A ponte é lossy.

---

## Track 1 — Base cross-dimensional pro chat

### Fase 0 — Limpeza de grafo (sem eval gate)
- **público-alvo: nó → tag.** É enum de baixa cardinalidade — vira tag
  (`publico/<slug>`) como mechanism/ano/trl (§6.1.1). Remove o folder `publicos/`
  do export, o `publico_index` como nós, e o nó no KG. WIKI.md é autoritativo →
  editar lá primeiro; `test_wiki_schema_consistency` guarda.
- **Nó-pai de fonte para entidades.** Editais têm nó de fonte (FINEP/FAPESP/WEB
  via `source_of`); ICTs e investidores ganham o seu (EMBRAPII, INVESTIDORES) —
  no export e como agrupador no KG.
- **Cobertura do normalizador de tema.** Reduzir as 140 áreas órfãs: melhorar o
  prompt fino→macro (ex.: mineração → "materiais, química e manufatura avançada")
  e/ou revisar se o vocab de 7 temas-macro é granular o bastante (gatilho de
  evolução de `tema_vocab`, ver PR #16). Meta: nenhum ICT órfão por tema mapeável.

### Fase 1 — Entidades como nós de primeira classe
- `build_ict_graph` emite `wiki/ict/<slug>.json` por unidade (campos §6.1.2);
  `icts.json` segue como índice.
- Novo sync `investidores.json` → `wiki/investidor/<slug>.json` (campos §6.1.3).
- `kg_store`: `load_wiki_page` resolve ids de entidade por node_type; índice de
  tema unificado spanning editais+desafios+ICTs+investidores.
- Export passa a ler as wiki pages de entidade (hoje lê `icts.json`).

### Fase 2 — Tools cross-dimensionais do explore (o norte)
- `oportunidades_por_tema(tema|setor)` → agrega as 4 dimensões num resultado.
- `list_icts(tema)` e `list_investidores(tema|tese)` standalone (hoje só
  "parceiras de um edital" / nada).
- Wiring no `KGMatchService`/explore: o chat de Descoberta responde cross-dim.
- Sem eval gate de match (é explore/leitura), mas validar com perguntas-golden.

### Fase 3 — ICT no radar (EVAL-GATED)
- `radar_service` normaliza `ict_match` ao item comum (hoje só `investor_match`
  cobre entidade). RRF + floor como investidor.
- **GATE: `python -m radar.core.eval matching` antes de mergear** — muda a mistura
  evento-vs-entidade do ranking.

---

## Track 2 — Qualidade da torneira

### Fase 4 — Dedup e triagem
- **Excluir FINEP/FAPESP da descoberta web**: pular domínios finep.gov.br /
  fapesp.br (e outros com extrator dedicado) no `opportunity_discovery` — eles já
  entram pelo ETL próprio; na web viram ruído/duplicata (ex.: notícia dos R$3,3bi).
- **Triagem notícia-vs-oportunidade**: apertar o filtro pra rejeitar páginas que
  são anúncio/notícia sem chamada acionável (sem prazo/elegibilidade/inscrição).

### Fase 5 — Profundidade em hubs
- Páginas-hub de inovação aberta (ex.: tupy.com.br/inovacao-aberta) listam
  desafios reais em links-filhos não explorados → o nó-hub fica pobre. Crawl de
  **1 nível** pra extrair cada desafio como nó próprio (node_type `desafio`).
  Reusa o agente de research (research_tools) com orçamento limitado.

---

## Riscos / interações
- Não quebrar radar (investidor), hybrid, explore atuais — tudo aditivo.
- **Data plane:** wiki pages lidas do disco; a migração JSONB `kg_artifacts`
  (pendente, [[project-data-plane-prod]]) muda o seam em prod — emissão de wiki
  page de entidade respeita o mesmo `kg_store`.
- Fase 3 muda ranking → eval gate obrigatório.
- Chunk/embed de entidades **fora de escopo** (não entram na escrita).

## Sequência sugerida
Fase 0 (limpeza, baixo risco, melhora o grafo já) → 1 (nós) → 2 (chat cross-dim,
o valor pro usuário) → 4+5 (torneira, paralelizável) → 3 (ICT no radar, eval-gated, por último).
