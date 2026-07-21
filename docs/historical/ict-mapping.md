# Spec — Mapeamento de ICTs

> **Objetivo:** adicionar ao KG as instituições de C&T (ICTs) que muitos editais FINEP/FAPESP exigem como parceiras, para que o sistema possa sugerir parceiros compatíveis por afinidade temática. ICT **não lança** edital — apenas participa de projetos.
> **Base:** branch `test-integration`. **Data:** 2026-06-03.
> **Pré-leitura:** [docs/domain/schema.md §6 (schema do grafo)](../domain/schema.md), [docs/domain/schema.md §10 (adicionar fonte)](../domain/schema.md), [docs/domain/schema.md §12.4 (source adapters)](../domain/schema.md).

## Premissa que muda o desenho

**ICT não é edital.** Não tem PDF, status, mechanism, vigência nem `fonte` de fomento. Não flui pelo ETL `extractor → structurer → wiki page`. É um **tipo de nó novo**, com pipeline de ingestão próprio, que se conecta ao grafo de editais **pela ponte do nó `tema` que já existe**:

```
edital --edital_has_theme--> tema <--ict_has_expertise-- ict
```

Não há aresta direta edital↔ICT. Editais exigem *uma* ICT (não uma nomeada); o matchmaking é **computado** por interseção temática. Isso evita arestas espúrias e reusa o hub `tema`.

## Decisões travadas

| # | Tópico | Decisão |
|---|--------|---------|
| 1 | É nó ou tag? | **Nó** (`ict`) — é hub de navegação com identidade própria (pivota-se por ele para descobrir editais candidatáveis) |
| 2 | Semente | **Registros conhecidos**, não lista única (não existe): **EMBRAPII** (≈80 unidades, estruturado) + **PNIPE/MCTI** (laboratórios) |
| 3 | Ponte com editais | Via `tema` (computada), **sem** aresta direta edital↔ICT |
| 4 | Flag no edital | Tag `requires_ict_partner` no edital (booleana), derivada na extração — não é nó |
| 5 | Pipeline | **Determinístico** (crawl de lista conhecida + 1 passo LLM leve de normalização), **não** agente |
| 6 | Faseamento | EMBRAPII primeiro (prova o tipo de nó end-to-end), PNIPE depois (volume/ruído) |

---

## Schema (mudança de doc primeiro — CLAUDE.md)

Toda mudança abaixo vai em [docs/domain/schema.md](../domain/schema.md) **antes** do código, validada por tests/test_wiki_schema_consistency.py.

### Novo tipo de nó (§6.1)
```yaml
node_types:
  ict:
    folder: icts
    tags: [ict, "kind/<kind>", "tema/<slug>"]
    emoji: "🔬"
```
- `kind` ∈ {`embrapii_unit`, `laboratorio`, `instituto`, `universidade`} — absorve a definição ampla de "ICT". Vem da fonte (EMBRAPII→`embrapii_unit`; PNIPE→`laboratorio`) ou de heurística.

### Novo tipo de link (§6.2)
```yaml
link_types:
  ict_has_expertise:
    from: ict
    to: tema
    section: "## Áreas de Atuação"
  aggregator_lists_ict:
    from: [tema]
    to: ict
    section: "## ICTs"
```

### Campos da wiki page de ICT
| Campo | Origem | Notas |
|-------|--------|-------|
| `name` | bronze | nome da unidade/lab |
| `kind` | adapter | ver acima |
| `source` | adapter | `embrapii` \| `pnipe` |
| `url` | bronze | página canônica da instituição |
| `about` | bronze | texto "Sobre" |
| `contact` | bronze | responsável, email, telefone, site |
| `address` | bronze | sede |
| `themes` | **LLM** | mapeia "área de atuação"/"técnicas" cruas → slugs canônicos de `tema` |
| `techniques_raw` | bronze | principais técnicas (texto livre, não normalizado) |

**Invariante:** a normalização LLM só mapeia áreas cruas → `tema` canônico e gera um `summary`. Não inventa fato sobre a instituição. Mesmo espírito "burro" do structurer (docs/domain/schema.md §11).

---

## Ingestão — pipeline dedicado

### Bronze
Extractors novos, fora do fluxo de edital. Saída em `bronze_data/ict_raw/`.

- `pipeline/extractors/ict_embrapii.py` — lista [nossas-unidades](https://embrapii.org.br/nossas-unidades/#filter-units) → segue para páginas individuais (ex.: `/unidades/<slug>/`). Estruturado, baixo volume. Reusa `BaseScraper` ([pipeline/extractors/base.py](../../pipeline/extractors/base.py)).
- `pipeline/extractors/ict_pnipe.py` — [pnipe.mcti.gov.br/search](https://pnipe.mcti.gov.br/search). Metadados ricos por lab (Sobre, Endereço, Contato, área de atuação, principais técnicas). **Alto volume e ruidoso** → exige estratégia de filtro (por área/UF) e paginação. Fase B.

Schema bronze (comum às duas fontes):
```json
{
  "name": "...", "source": "embrapii|pnipe", "url": "...",
  "about": "...", "address": "...",
  "contact": {"responsavel": "...", "email": "...", "telefone": "...", "site": "..."},
  "areas_raw": ["...", "..."], "techniques_raw": ["..."]
}
```

### Silver/normalização
1 passo LLM por ICT (leve — é metadado HTML, não PDF): `areas_raw + techniques_raw → themes` (slugs canônicos do vocabulário de `tema`) + `summary`. Reaproveita o cliente/padrão de [core/content_library.py](../../core/services/content_library.py) `_enrich`/[core/ingestion/structurer.py](../../core/ingestion/structurer.py). Cache por hash do bronze (não re-chamar LLM em ICT inalterada).

### KG build
pipeline/build_knowledge_graph.py: adicionar nós `ict` + arestas `ict_has_expertise` ao `index.json` e gerar wiki pages em `icts/`. Os nós `tema` referenciados passam a listar ICTs (`aggregator_lists_ict`).

### Dedup
Mesma instituição pode aparecer em EMBRAPII **e** PNIPE. Dedup por nome normalizado + (quando houver) CNPJ. Preferir o registro mais rico; manter `source` como lista se aparecer em ambos.

---

## Payoff — matchmaking (Fase C, fora do MVP da ingestão)

Com os nós no grafo:
- Tag `requires_ict_partner` no edital + interseção `edital.themes ∩ ict.themes` → ranking de parceiros sugeridos.
- Nova tool de exploração `find_ict_partners(edital_id)` em [core/agent_tools/explore_tools.py](../../core/llm/agent_tools/explore_tools.py) (espelha `find_analogues`), para o `KGMatchService` sugerir ICTs.

Especificar em spec separada após a ingestão estar verde.

---

## Faseamento

| Fase | Escopo | Gate |
|------|--------|------|
| **A** | Schema (docs/domain/schema.md) + `ict_embrapii` + normalização + KG build + dedup | validador verde; nós `ict` navegáveis no grafo ligados a `tema` |
| **B** | `ict_pnipe` com estratégia de filtro/paginação | volume controlado; precisão da extração amostrada manualmente |
| **C** | Flag `requires_ict_partner` + tool `find_ict_partners` + matchmaking | spec própria |

## Riscos

- **PNIPE é enorme e heterogêneo** (toda a infraestrutura de C&T do país). Sem filtro vira mega-hub ruidoso. Mitigação: começar por EMBRAPII; PNIPE só com filtro por área/relevância.
- **Definição ampla de "ICT"** → `kind` no nó absorve a variação; não tentar uma taxonomia perfeita no MVP.
- **Scraping**: respeitar robots.txt / rate-limit; PNIPE pode exigir Playwright se a busca for client-side (ADR-001 já prevê Playwright on-demand).
- **Vocabulário de `tema`**: áreas de ICT podem não existir no vocabulário atual (focado em editais). Pode ser necessário expandir o vocabulário de `tema` — mudança de schema, não de código.

## Critérios de aceitação (Fase A)

- `docs/domain/sources/` ou docs/domain/schema.md documentam `ict` + `ict_has_expertise`; `pytest tests/test_wiki_schema_consistency.py` verde.
- `python -m radar.pipeline.extractors.ict_embrapii` produz `bronze_data/ict_raw/embrapii_*.json`.
- `build_knowledge_graph` emite nós `ict` no `index.json` e wiki pages em `icts/`, com arestas para `tema`.
- Dedup testada (mesma ICT em 2 fontes → 1 nó).
- Nenhuma ICT vira `edital`; nenhum edital ganha aresta direta para ICT.
