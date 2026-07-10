# Spec: Migração Hypergraph → SQL Relacional

- **Status:** Aprovada
- **Data:** 2026-07-09
- **Autores:** Lucas + Claude

## 1. Contexto e Motivação

O sistema atual tem duas representações paralelas do conhecimento:

- **Hypergraph** (JSON em `data/knowledge_graph/hypergraphs/`) — navegação do ExploreAgent, catálogo, BFS
- **Postgres** (silver/gold chunks, pgvector) — match semântico via cosseno

A manutenção de ambos duplica esforço, e o hypergraph não escala (carrega tudo em memória, ~2k nós Conceito com 7-8% de ruído).

**Objetivo:** Unificar em Postgres como source of truth única. Match já está livre de hypergraph (sprint anterior). Resta migrar catálogo, BFS e escrita de ingestão.

**Data analysis (jul/2026):** Dos ~2.183 nós Conceito, 92,3% (~2.025) são termos limpos, multi-word, single-file. 7,7% são ruído (OCR, seções, genéricos). Conclusão: **não perder os 92% bons** — eles viram `tecnologias_tags[]` nas entities.

## 2. Decisões Arquiteturais

| Decisão | Escolha | Justificativa |
|---------|---------|---------------|
| `setores` | Taxonomia fechada (16 itens) | Transversalidade controlada, filtro determinístico, LLM escolhe da lista |
| `tecnologias_tags` | Folksonomia canônica | 93,5% dos conceitos são single-file/multi-word — taxonomia os perderia |
| Arestas semânticas | Morrem → viram RAG | `abrange_tema`, `viabiliza`, etc. não valem a manutenção; a query pergunta ao LLM |
| Arestas estruturais | Migram para `entity_relationships` | `operado_por`, `exige_parceria_com`, `vinculado_a` — navegação determinística |
| BFS | CTE recursiva em SQL | Mais rápida, determinística, sem carregar JSON |
| Bridge API | `entity_catalog.py` (mesma interface de `hypergraph_catalog`) | Migração incremental, consumer não quebra |

## 3. Schema (Migration 036)

### 3.1 `entities`

```sql
CREATE TABLE entities (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type            text NOT NULL CHECK (type IN ('oportunidade', 'ator', 'conceito')),
    source          text NOT NULL,  -- finep, fapesp, fapesc, ict, investidores, programas
    native_id       text NOT NULL,  -- id original no hypergraph/silver
    name            text NOT NULL,
    setores         text[] NOT NULL DEFAULT '{}' CHECK (array_length(setores, 1) BETWEEN 1 AND 3),
    tecnologias_tags text[] NOT NULL DEFAULT '{}',
    properties      jsonb NOT NULL DEFAULT '{}',
    embedding       vector(1536),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, native_id)
);

CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_entities_source ON entities(source);
CREATE INDEX idx_entities_setores ON entities USING GIN(setores);
CREATE INDEX idx_entities_tecnologias_tags ON entities USING GIN(tecnologias_tags);
```

### 3.2 `entity_relationships`

```sql
CREATE TABLE entity_relationships (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id   uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id   uuid NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    type        text NOT NULL CHECK (type IN (
                    'operado_por', 'exige_parceria_com',
                    'vinculado_a', 'subordinado_a'
                )),
    properties  jsonb NOT NULL DEFAULT '{}',
    UNIQUE (source_id, target_id, type)
);

CREATE INDEX idx_er_source ON entity_relationships(source_id);
CREATE INDEX idx_er_target ON entity_relationships(target_id);
CREATE INDEX idx_er_type ON entity_relationships(type);
```

### 3.3 Setores (taxonomia fechada)

```
Agro, Saúde, Energia, TIC, Bioeconomia, Defesa,
Mobilidade, Urbano, Educação, Química, Materiais,
Sustentabilidade, Marítimo, Social, Finanças, Multissetorial
```

Total: **16 setores**. CHECK em aplicação: `1 <= len(setores) <= 3`.

## 4. Extração LLM (`gold.py` — `ingest_all()`)

### 4.1 Prompt v3

Input: Texto completo do silver structured_docs (seções 1-4: objetivos, temas, elegibilidade, mecanismo).

```
Classifique a oportunidade em 1 a 3 setores mais relevantes da lista:
Agro, Saúde, Energia, TIC, Bioeconomia, Defesa, Mobilidade, Urbano,
Educação, Química, Materiais, Sustentabilidade, Marítimo, Social,
Finanças, Multissetorial

Extraia tecnologias-chave e jargões (incluindo termos -tech como
healthtech, agritech, fintech) como tags livres. Máximo 8.

Responda JSON:
{"setores": ["Agro", "TIC"], "tecnologias_tags": ["agricultura de precisão", "iot", "healthtech"]}
```

### 4.2 Fluxo de `ingest_all()`

```
silver structured_docs → [por edital]:
  1. Extrair metadados (título, agência, url, prazos, orçamento)
     → INSERT entities (type=oportunidade, setores=[], tecnologias_tags=[])
  2. Chamar LLM v3 (setores + tecnologias_tags)
     → UPDATE entities SET setores=$1, tecnologias_tags=$2
  3. Aplicar anti_class_verdict nas tecnologias_tags
     → remover genéricos, métricas, legais
  4. Extrair atores (agência operadora, ICTs parceiras obrigatórias)
     → INSERT entities (type=ator)
     → INSERT entity_relationships (type='operado_por' | 'exige_parceria_com')
  5. Se subordinado_a (subprograma → programa):
     → INSERT entity_relationships (type='subordinado_a' | 'vinculado_a')
```

### 4.3 Tratamento de ruído

Reusa `anti_class_verdict()` de `canonicalize.py` (~130 genéricos + regex de métrica/legal) como filtro pós-LLM nas `tecnologias_tags`. O que for filtrado não persiste.

## 5. Bridge API (`entity_catalog.py`)

### 5.1 Interface

```python
def list_editais(status=None, setor=None, limit=200) -> list[dict]
def get_edital(edital_id: str) -> dict | None
def get_opportunity(opp_id: str) -> dict | None
def list_entity_catalog(key: str, setor="", limit=50) -> list[dict]
def get_stats() -> dict
def investment_offer(opp_id, graphs=None) -> tuple | None
def programa_node(opp_id, graphs=None) -> tuple | None
def investment_offers_by_fund(graphs=None) -> dict[str, dict]
def list_opportunities(tipo=None, limit=200) -> list[dict]
def get_node_neighborhood(node_id, depth=2) -> dict
```

Assinaturas idênticas a `hypergraph_catalog.py` para compatibilidade.

### 5.2 BFS (CTE Recursiva)

```sql
WITH RECURSIVE neighborhood AS (
    SELECT id, type, name, setores, tecnologias_tags, 0 AS depth
    FROM entities WHERE id = $1
    UNION
    SELECT e.id, e.type, e.name, e.setores, e.tecnologias_tags, n.depth + 1
    FROM neighborhood n
    JOIN entity_relationships r ON r.source_id = n.id OR r.target_id = n.id
    JOIN entities e ON e.id = CASE WHEN r.source_id = n.id THEN r.target_id ELSE r.source_id END
    WHERE n.depth < $2
)
SELECT DISTINCT * FROM neighborhood ORDER BY depth, name;
```

## 6. Plano de Migração

### Fase 0 — Schema (1 PR)

- Criar `supabase/migrations/036_gold_entities.sql`
- Rodar `supabase db push`

### Fase 1 — Extração (1 PR)

- Criar `core/kg/gold.py` com `ingest_all()`
- Rodar `ingest_all()` em batch (checkpoint a cada 5 editais, resiliente a erro)
- Verificar: `SELECT count(*) FROM entities WHERE setores != '{}'` > 0

### Fase 2 — Bridge API + Consumidores (3 PRs)

**PR-A (Grupo A — só leem `list_editais`/`get_edital`):**

- Criar `core/kg/entity_catalog.py`
- Migrar: `writing_session`, `checklist_service`, `writing_tools`, `critic_agent`, `planning_node`, `backend/routers/applications`, `backend/routers/writing`
- Trocar imports em cada um

**PR-B (Grupo B — catalog + BFS + neighborhood):**

- Migrar: `explore_tools.py`, `opportunity_service.py`, `hypergraph_match.py`, `source_docs.py`, `temporal.py`, `backend/routers/explore`, `backend/routers/catalog`

**PR-C (Grupo C — match_verdict + tasks):**

- Migrar: `match_verdict.py`, `tasks.py`
- Remover último `load_all_hypergraphs()` de consumers

### Fase 3 — Limpeza (1 PR)

- Deletar `hypergraph_catalog.py`
- Remover `canonicalize.py` (canonicalização agora é inline no prompt v3 + filtro pós-LLM)
- Remover `kg_store.save_hypergraphs()`
- Remover `load_all_hypergraphs()` de `kg_store.py` (se只剩 scripts)
- Remover `scripts/canonicalize_concepts.py`
- Atualizar `WIKI.md`: bloco `hypergraph_schema` substituído por `setores_taxonomia`

## 7. Riscos e Mitigações

| Risco | Mitigação |
|-------|-----------|
| LLM v3 produz setores fora da lista | Post-validar no código + fallback para `Multissetorial` |
| Perda de fidelidade na BFS sem arestas semânticas | Aceito por design — arestas semânticas viram RAG (ExploreAgent pergunta ao LLM) |
| Inconsistência entre hypergraph e SQL durante migração | Bridge API lê de SQL; hypergraph não é mais consumido em runtime após Fase 2 |
| `ingest_all()` lento (LLM por edital) | Batch paralelo com asyncio.gather + checkpoint a cada 5 |

## 8. Critérios de Sucesso

```sql
-- entities populada
SELECT count(*) FROM entities WHERE type = 'oportunidade';  -- > 0
SELECT count(*) FROM entities WHERE array_length(setores, 1) BETWEEN 1 AND 3;  -- = total

-- entity_relationships populada
SELECT count(*) FROM entity_relationships;  -- > 0

-- Consumidores migrados
-- grep -r "hypergraph_catalog" core/ --include="*.py" → vazio

-- load_all_hypergraphs removido de runtime
-- grep -r "load_all_hypergraphs" core/ --include="*.py" → vazio
```
