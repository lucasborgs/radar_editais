# Mini-spec: wiki pages para entidades (ICT / investidor) no KG

**Status:** rascunho para decisão (escopado 2026-06-13). Não implementado.

## Problema

Editais têm wiki page individual por nó (`data/knowledge_graph/wiki/<fonte>/<id>.json`),
lida pelo `kg_store.load_wiki_page` e raciocinada pelo `KGMatchService`/explore.
**Entidades não.** Hoje:

| | Representação | Wiki page? | Match | Explore | Radar |
|---|---|---|---|---|---|
| ICT (EMBRAPII) | `icts.json` consolidado (build_ict_graph) | não | `ict_match` | sim (explore_tools) | **não** |
| Investidor | `investidores.json` curado à mão | não | `investor_match` | — | sim |

O schema **já declara** os node types (WIKI.md §6.1.2 `ict`, §6.1.3 `investidor`;
`wiki_schema.node_types()` com folders/tags). Falta a **materialização como nó**:
emissão de wiki page + carregamento + consumo uniforme. A ponte entidade↔evento
é por **tema** (§6.1.2: "não há aresta direta edital↔ICT" — casa via
`edital.themes ∩ ict.themes`), mesma lógica já aplicada no export Obsidian.

## Objetivo

Entidades viram nós de primeira classe, em paridade com editais: página de
detalhe abrível, raciocínio nó-a-nó no KG agent, e ICT entrando no radar.

## Escopo

1. **Emissão de wiki page por entidade.**
   - ICT: `build_ict_graph` passa a emitir `wiki/ict/<slug>.json` por unidade
     (além do `icts.json` consolidado, que segue como índice). Campos do §6.1.2:
     `id, name, kind, themes, areas_raw, about/summary, contact, url, address`.
   - Investidor: novo passo de build (ex.: `pipeline/build_investidor_graph.py`)
     sincroniza `investidores.json` (curado) → `wiki/investidor/<slug>.json`.
     Campos do §6.1.3: `id, name, tese, tese_themes, tese_keywords, setores,
     estagio_alvo, ticket_range, lead_follow, fund_status, site`.
   - Seguir o padrão `wiki/<folder>/<slug>.json` derivado de `node_types()[*].folder`
     (sem hardcode de pasta — schema é autoritativo).

2. **`kg_store`: carregamento.** Generalizar `load_wiki_page(id)` para resolver
   ids de entidade (`embrapii:…`, `investidor:…`) ao path da wiki page por
   node_type. Manter `load_icts`/`load_investidores` como índices.

3. **Consumo uniforme.** `KGMatchService`/explore conseguem "abrir" um nó de
   entidade (detalhe) como abrem um edital. Hoje o explore chega em ICT via
   `explore_tools→ict_match`; passa a ler a wiki page.

4. **ICT no radar.** `radar_service` normaliza `ict_match` ao item comum do radar
   (hoje só `investor_match` cobre o quadrante entidade). RRF + floor de score
   como o investidor (entidade não tem gate — §scorer LLM ancora alto). É a
   provável lacuna de produto: ICT só aparece no explore, não no ranking.

5. **Guard de schema.** `tests/test_wiki_schema_consistency.py` valida doc↔código;
   qualquer campo novo da wiki page de entidade tem que estar no WIKI.md.

## Fora de escopo / não-objetivos

- **Chunk+embed de entidades** — confirmado desnecessário (entidades não entram
  na escrita; o `mode="pitch"` para `investidor:` é latente, não fluxo ativo).
- Enriquecimento LLM tipo card de edital — entidades já são estruturadas na fonte.

## Riscos / interações

- Não pode quebrar radar (investidor), hybrid match nem explore atuais — aditivo.
- **Data plane:** wiki pages são lidas do disco localmente; a migração JSONB
  `kg_artifacts` (pendente, ver [[project-data-plane-prod]]) muda o seam de
  leitura em prod — a emissão de wiki page de entidade tem que respeitar o mesmo
  `kg_store` para não criar um segundo caminho de leitura.

## Perguntas em aberto

1. ICT no radar muda a mistura evento-vs-entidade — precisa de re-eval do radar
   (suíte matching) antes de mergear? (provável que sim — gate.)
2. Slug de entidade: reusar `id_to_slug` (`embrapii:x`→`embrapii-x`) ou folder
   por node_type? (o export Obsidian já usa `id_to_slug`.)
3. Investidor é curado à mão — o build sincroniza one-way (json→wiki) ou a wiki
   page vira a fonte de verdade editável?

## Fases sugeridas

1. ICT: emissão de wiki page + `kg_store` load + ICT no radar (+ eval gate).
2. Investidor: build de sync + wiki page + paridade no explore.
3. Export Obsidian já consome entidades (feito); revisar se passa a ler as wiki
   pages em vez do `icts.json` consolidado.
