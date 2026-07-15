# Spec — Match Evolution (KG Split + EntityMatcher Unification)

Status: **supersedida** pela linhagem v3, consolidada em
[`v3-unified.md`](../specs/v3-unified.md) · 2026-06-24 · registro de proposta.

---

## Contexto

O matching do sistema cresceu organicamente em 4 direções paralelas, cada uma com seu próprio padrão arquitetural, prompt e `_make_client()`:

| Serviço | Bebe de | Arquitetura | LLM? | LOC |
|---------|---------|-------------|------|-----|
| **KGMatchService** | `index.json` | Karpathy (catálogo inteiro no prompt) | Sim | 936 |
| **HybridMatchService** | `index.json` + wiki pages | 2 estágios: determinístico → LLM | Sim | 956 |
| **InvestorMatch** | `investidores.json` | Karpathy | Sim | 151 |
| **ProgramaMatch** | `programas.json` | Karpathy | Sim | 145 |
| **ICT match** | `icts.json` | Determinístico (interseção de temas) | **Não** | 103 |

Os problemas estruturais:

1. **KGMatchService** amalgama 3 responsabilidades não-coesas — leitura de grafo (sem LLM), chat exploratório (LLM pesado) e readers de catálogo (sobreposição com HybridMatch):
   - `get_graph()`, `resolve_scope()`, `_edital_ids_for_node()`, `_find_analogue_ids()` — parse de markdown + wikilinks, **sem LLM**
   - `explore()`, `explore_turn()`, `_explore_agent()`, `_explore_legacy()` — **LLM-heavy**, ~$0.02–0.10/call
   - `list_editais()`, `get_edital_by_id()`, `get_stats()`, `match()` — readers não-wired em routers

2. **InvestorMatch e ProgramaMatch são cópias um do outro** (~150 linhas cada, 70% sobreposição). Mesmo padrão: catálogo no prompt, 1 LLM call, `_make_client()` duplicado, `_parse()` duplicado, diferindo só na fonte de dados (`load_investidores` vs `load_programas`), no prompt e nos campos de enriquecimento pós-parse.

3. **ICT Match é o outlier correto** — determinístico, sem LLM, sem endpoint próprio. É chamado como enriquecimento dentro do HybridMatch e da tool `find_ict_partners` do agente. Não precisa de mudança.

---

## Decisões pinadas (não revisitar)

| # | Decisão |
|---|---------|
| D1 | GraphService nunca chama LLM — zero custo de inferência. Pode ser instanciado sem API keys |
| D2 | ExploreAgent é o único endpoint público de chat. Substitui `/kg-explore` + `/frontdoor/turn` |
| D3 | ExploreAgent mantém rate limit diferenciado: anônimo (3/min) vs. autenticado (10/min). Implementado via middleware que inspeciona JWT e seleciona o bucket — o `@limiter.limit` do FastAPI não suporta condicionais |
| D4 | WritingSession migra de `KGMatchService.resolve_scope()` para `GraphService.resolve_scope()` — lazy import preservado |
| D5 | ExploreAgent mantém `_make_client()` próprio (independência de provider/model do HybridMatchService) |
| D6 | Readers de catálogo (`list_editais`, `get_edital_by_id`, `get_stats`) permanecem no ExploreAgent como thin wrappers sobre `kg_store` — não puxam `HybridMatchService` como dependência |
| D7 | Backwards compatibility: `/kg-explore` vira redirect 308 para `/explore` durante 1 ciclo de deploy |
| D8 | GraphService faz cache em memória do grafo construído (`lru_cache`). Invalida por mtime do vault — não lê disco a cada request |
| D9 | `/explore` é stateless por design: o servidor não mantém sessão. Cliente envia `profile` parcial + `history` a cada turno; servidor só extrai `profile_updates` e devolve `answer`. Não há LangGraph checkpointer nem `thread_id` — o cliente consolida o estado |
| D10 | InvestorMatch + ProgramaMatch viram `EntityMatcher` — classe genérica que recebe `EntityCatalog`. Os catálogos (`catalog_investidores`, `catalog_programas`) são instâncias diretas. Endpoints `/match/investidores` e `/match/programas` mantidos (backwards compat), router chama `EntityMatcher` direto. Módulos `investor_match.py` e `programa_match.py` removidos — conteúdo migra para `entity_matcher.py` |

---

## Design

### GraphService (sem LLM)

```python
# core/services/graph_service.py

class GraphService:
    """Leitura do grafo Obsidian (vault .md + wikilinks). Sem LLM, sem API keys."""

    def __init__(self, vault: Path = OBSIDIAN_VAULT_DIR): ...

    def get_graph(self) -> dict: ...
    def resolve_scope(self, edital_id=None, node_id=None,
                      node_type=None, max_analogues=3) -> list[str]: ...
    def edital_ids_for_node(self, node_id: str) -> list[str]: ...
    def find_analogue_ids(self, edital_id: str) -> list[str]: ...
```

`get_graph()` usa `functools.lru_cache(maxsize=1)` com invalidação por mtime do vault para não reler centenas de `.md` a cada chamada. `resolve_scope()` lê 1–3 arquivos (I/O aceitável sem cache).

Move de `kg_match_service.py` para `graph_service.py`:

| Símbolo | Destino |
|---------|---------|
| `_WIKILINK_RE`, `_FRONTMATTER_RE` | constantes do módulo |
| `_parse_frontmatter()` | método privado |
| `_folder_type_map()` | método privado |
| `_node_type_for_parts()` | método privado |
| `get_graph()` | público |
| `resolve_scope()` | público |
| `_edital_ids_for_node()` → `edital_ids_for_node()` | público (renomeado, ferramentas do agente usam) |
| `_find_analogue_ids()` → `find_analogue_ids()` | público (renomeado, ferramentas do agente usam) |

Instância única em `backend/common.py`:

```python
from core.services.graph_service import GraphService
graph_service = GraphService()
```

### ExploreAgent (LLM-heavy)

```python
# core/services/explore_agent.py

class ExploreAgent:
    """Chat exploratório sobre o catálogo. LLM-heavy (legacy ou agente)."""

    def __init__(self): ...  # lazy: _client, _model, _index só sob demanda

    def explore(self, message, history=None, edital_ids=None,
                node_id=None, node_type=None, agent_enabled=False) -> str: ...
    def explore_turn(self, message, history=None, edital_ids=None,
                     node_id=None, node_type=None) -> tuple[str, dict]: ...
    def match(self, profile, top_k=10) -> list[dict]: ...
    def get_stats(self) -> dict: ...
    def list_editais(self, status=None, tema=None, limit=100) -> list[dict]: ...
    def get_edital_by_id(self, edital_id) -> dict | None: ...
```

Move de `kg_match_service.py` para `explore_agent.py`:

- Tudo que não foi para GraphService
- Mantém `_make_client()` (gpt-4o-mini default, configurável por env)
- `_explore_tools()` recebe `GraphService` como parâmetro em vez de `self`

`backend/common.py`:

```python
from core.services.explore_agent import ExploreAgent
explore_agent = ExploreAgent()
```

### explore_tools.py

`build_explore_tools(service: KGMatchService)` → `build_explore_tools(explore_agent: ExploreAgent, graph_service: GraphService)`:

| Tool atual | Service call | Novo owner |
|------------|-------------|------------|
| `list_editais` | `service.list_editais()` | ExploreAgent |
| `get_edital` | `service.get_edital_by_id()` | ExploreAgent |
| `find_analogues` | `service._find_analogue_ids()` | GraphService |
| `get_graph_neighbors` | `service._edital_ids_for_node()` | GraphService |
| `find_ict_partners` | `ict_match` direto | sem mudança |
| `list_icts` | `kg_store` direto | sem mudança |
| `list_investidores` | `kg_store` direto | sem mudança |
| `oportunidades_por_tema` | `service.list_editais()` | ExploreAgent |
| `search_edital_trechos` | `retrieve_chunks` direto | sem mudança |

---

## Endpoints

### Atual → Novo

| Atual | Novo | Rota | Serviço |
|-------|------|------|---------|
| `GET /graph` | `GET /graph` | graph.py | GraphService |
| `POST /kg-explore` | `POST /explore` | explore.py | ExploreAgent |
| `POST /frontdoor/turn` | `POST /explore` | explore.py | ExploreAgent |
| `POST /match/investidores` | `POST /match/investidores` | matching.py | EntityMatcher (wrapper) |
| `POST /match/programas` | `POST /match/programas` | matching.py | EntityMatcher (wrapper) |

### explore.py (novo router)

```python
@router.post("/explore", summary="Chat exploratório + extração de perfil (auth opcional)")
@limiter.limit("3/minute")  # anônimo
def explore(request, req: ExploreRequest, user_id: OptionalUserId):
    if user_id is not None:
        # autenticado: rate 10/min, explora com perfil
        ...
    else:
        # anônimo: rate 3/min, sem perfil
        answer = explore_agent.explore(...)
        return {"answer": answer}
```

Payload:

```json
{
  "message": "...",
  "history": [],
  "profile": null,
  "edital_ids": [],
  "node_id": null,
  "node_type": null,
  "agent_enabled": false
}
```

Rate limit:
- Anônimo (sem JWT): 3/min (mesmo do kg-explore atual)
- Autenticado (com JWT): 10/min (mesmo do frontdoor atual)
- Implementação: middleware condicional (ver seção "explore rate limit — middleware condicional" acima). `@limiter.limit` simples não suporta buckets diferentes no mesmo endpoint

### frontdoor.py → removido

`POST /frontdoor/turn` deixa de existir. O frontend migra para `POST /explore` com o campo `profile` preenchido. A extração de `profile_updates` via `explore_turn()` é ativada quando `profile` está presente e `agent_enabled=false`.

Fonte única de dados: ambos GraphService (via métodos de grafo) e ExploreAgent (via `list_editais`/`get_edital_by_id`) consomem `kg_store.load_index()` — o mesmo `index.json` no disco. Nunca há cache separado entre eles. Ver D6b.

### explore rate limit — middleware condicional

O `@limiter.limit` padrão do FastAPI não suporta rate limit que varia por usuário no mesmo decorator. Implementação:

```python
# backend/rate_limit.py — novo middleware
async def explore_rate_limit(request: Request, call_next):
    user_id = getattr(request.state, "user_id", None)
    bucket = "explore_auth:10/m" if user_id else "explore_anon:3/m"
    # consulta token bucket e retorna 429 se excedido
    ...
```

Ou, alternativa mais simples: dois decorators + branch no router (ver D3).

### graph.py → simplificado

```python
@router.get("/graph")
def get_graph():
    return graph_service.get_graph()
```

A rota `POST /kg-explore` é removida (redirect 308 para `/explore` no nginx/API网关).

---

## EntityMatcher — unificação de InvestorMatch + ProgramaMatch

### Problema

`investor_match.py` (151 LOC) e `programa_match.py` (145 LOC) são estruturalmente idênticos: catálogo inteiro no prompt, 1 chamada LLM, JSON parse, enriquecimento pós-parse. A duplicação cobre:

- `_make_client()` — 3 providers, 25 linhas, copiado ipsis litteris
- `_parse()` — regex + JSONDecodeError fallback, 15 linhas, copiado
- Estrutura do `match_*()` — carrega fonte, monta prompt, chama LLM, enriquece, retorna

A única diferença real: qual `kg_store.load_*()` chamam, o prompt e os campos de enriquecimento.

### Design

`EntityMatcher` é uma classe parametrizável que substitui ambos os módulos:

```python
# core/services/entity_matcher.py

from collections.abc import Callable
from dataclasses import dataclass

@dataclass
class EntityCatalog:
    loader: Callable[[], list[dict]]   # ex.: kg_store.load_investidores
    system_prompt: str
    user_prompt_template: str           # {profile} e {catalog} no formato
    format_item: Callable[[dict], str]  # uma entrada → linha do catálogo no prompt
    enrich: Callable[[dict, dict], dict] # (match, raw_entity) → match enriquecido

class EntityMatcher:
    """Match genérico para entidades (investidores, programas, …).
    Karpathy-style: catálogo inteiro no prompt, 1 LLM call.
    """
    def __init__(self, catalog: EntityCatalog): ...

    def match(self, profile: CompanyProfile, top_k: int = 5) -> list[dict]: ...
```

`_make_client()` e `_parse()` viram funções compartilhadas no módulo (não duplicadas).

### Catálogos como instâncias de `EntityCatalog`

Os dois catálogos são instâncias **diretas** de `EntityCatalog`, definidas no módulo `entity_matcher.py`. Sem thin wrappers — o router chama `EntityMatcher` diretamente.

```python
# core/services/entity_matcher.py

catalog_investidores = EntityCatalog(
    loader=kg_store.load_investidores,
    system_prompt=MATCH_SYSTEM_INVESTIDOR,
    user_prompt_template=MATCH_USER_TEMPLATE,
    format_item=_format_investidor_props,  # → "ID:{id} | nome | modo:generalista/..."
    enrich=_enrich_investidor,
)

catalog_programas = EntityCatalog(
    loader=kg_store.load_programas,
    system_prompt=MATCH_SYSTEM_PROGRAMA,
    user_prompt_template=MATCH_USER_TEMPLATE,
    format_item=_format_programa_props,    # → "ID:{id} | nome | tipo | operador | ..."
    enrich=_enrich_programa,
)
```

```python
# backend/routers/matching.py
from core.services.entity_matcher import EntityMatcher, catalog_investidores, catalog_programas

@router.post("/match/investidores")
def match_investidores_endpoint(...):
    return EntityMatcher(catalog_investidores).match(profile, top_k=5)

@router.post("/match/programas")
def match_programas_endpoint(...):
    return EntityMatcher(catalog_programas).match(profile, top_k=5)
```

Os módulos `investor_match.py` e `programa_match.py` são **removidos** — todo o código (prompts, formatadores, enriquecedores) migra para `entity_matcher.py`. Os endpoints `/match/investidores` e `/match/programas` continuam existindo e funcionando, só que agora são implementados pelo router diretamente.

### Formato do catálogo no prompt

Cada entidade é serializada para 1 linha no prompt, com os campos que o match usa:

```
ID:{id} | nome | modo:generalista/tese | estagio:a,b | setores:x,y | tese:... | kw:...
```

Os formatadores `_format_investidor` e `_format_programa` extraem os campos específicos de cada schema (já existem, só que como funções `_format_catalog` dentro de cada módulo).

### Compartilhamento de `_make_client`

`_make_client()` (3 providers, gpt-4o-mini default) sai de dentro de cada módulo e vira função no módulo `core/llm/llm_client.py` ou num `core/services/_match_utils.py`. Todos os match services que usam LLM (`EntityMatcher`, `ExploreAgent`, `HybridMatch` Stage 2) consomem daqui — fim da duplicação.

---

## Dependências

### WritingSession._resolve_edital_scope

```python
# Atual (writing_session.py:450)
from core.services.kg_match_service import KGMatchService
return KGMatchService().resolve_scope(edital_id=self.edital_id, max_analogues=3)

# Novo
from core.services.graph_service import GraphService
return GraphService().resolve_scope(edital_id=self.edital_id, max_analogues=3)
```

Lazy import mantido. `GraphService` é leve (sem LLM), não impacta boot.

### explore_tools.py

`build_explore_tools` recebe `graph_service` como segundo parâmetro:

```python
def build_explore_tools(
    explore_agent: ExploreAgent,
    graph_service: GraphService,
) -> list[BaseTool]:
```

### backend/common.py

```python
from core.services.hybrid_match_service import HybridMatchService
from core.services.graph_service import GraphService
from core.services.explore_agent import ExploreAgent

wiki_matcher = HybridMatchService()
graph_service = GraphService()
explore_agent = ExploreAgent()
```

`KGMatchService` removido das imports.

---

## Arquivos afetados

### Parte A — Split KGMatchService

#### Criar
- `core/services/graph_service.py` — GraphService (extraído de kg_match_service)
- `core/services/explore_agent.py` — ExploreAgent (extraído de kg_match_service)
- `backend/routers/explore.py` — novo router unificado

#### Modificar
- `core/services/kg_match_service.py` — stub com deprecation warning por 1 ciclo, depois remover
- `core/llm/agent_tools/explore_tools.py` — `build_explore_tools` aceita 2 parâmetros
- `core/services/writing_session.py` — lazy import troca para `GraphService`
- `backend/common.py` — troca `kg_service = KGMatchService()` por `graph_service` + `explore_agent`
- `backend/routers/graph.py` — usa `graph_service` em vez de `kg_service`
- `core/profile_extractor.py` — docstring update (referencia `explore_turn` do ExploreAgent)

#### Remover
- `backend/routers/frontdoor.py` — rota `/frontdoor/turn` vira `/explore`

#### Testes
- `tests/test_explore_agent.py` — `KGMatchService` → `ExploreAgent`
- `tests/test_explore_grounded.py` — `KGMatchService` → `ExploreAgent`
- `tests/test_resolve_scope.py` — `KGMatchService` → `GraphService`
- Criar `tests/test_graph_service.py`

### Parte B — EntityMatcher

#### Criar
- `core/services/entity_matcher.py` — `EntityMatcher` + `EntityCatalog` + `catalog_investidores` + `catalog_programas` + `_make_client` compartilhado + `_parse` compartilhado + prompts e formatadores (migrados de `investor_match.py` e `programa_match.py`)

#### Modificar
- `backend/routers/matching.py` — endpoints `/match/investidores` e `/match/programas` chamam `EntityMatcher(catalog).match()` direto

#### Remover
- `core/services/investor_match.py` — removido (conteúdo migrado para entity_matcher.py)
- `core/services/programa_match.py` — removido (conteúdo migrado para entity_matcher.py)

#### Testes
- `tests/test_entity_matcher.py` — cobertura do `EntityMatcher` com fixtures de investidores e programas
- `tests/test_investor_match.py` — atualizar para testar `EntityMatcher(catalog_investidores)`
- `tests/test_programa_match.py` — atualizar para testar `EntityMatcher(catalog_programas)`

---

## Sequência de implementação

```
Fase 0  Extrair GraphService + wiring backend/common → routes (sem quebrar nada)
        ↓
Fase 1  Extrair ExploreAgent + explorar_tools refactor
        ↓
Fase 2  Criar /explore router + remover frontdoor.py + redirect kg-explore
        ↓
Fase 3  Criar EntityMatcher + compartilhar _make_client + _parse
        ↓
Fase 4  Migrar investor_match e programa_match para thin wrappers sobre EntityMatcher
        ↓
Fase 5  Limpeza: remover KGMatchService.py, remover código duplicado, atualizar testes, docs
```

Fases 0 e 1 rodam em paralelo (GraphService não depende de ExploreAgent, e nenhum depende de EntityMatcher).

Fase 2 depende de Fase 1 (precisa do ExploreAgent para o router `/explore`).

Fase 3 é independente do split — pode ser implementada em paralelo com Fases 0-2.

Fase 4 depende de Fase 3, e Fase 5 depende de tudo.

---

## Riscos

| # | Risco | Mitigação |
|---|-------|-----------|
| R1 | ExploreAgent mantém dependência circular com explore_tools (tools chamam ExploreAgent que cria tools) | `build_explore_tools` recebe ExploreAgent por parâmetro (injeção), não é método. Sem circularidade |
| R2 | GraphService e ExploreAgent duplicam `list_editais` / `get_edital_by_id` | Decide-se por D6: duplicação é barata (thin wrappers sobre kg_store) e evita acoplamento entre os serviços. Se futuramente incomodar, extrair mixin |
| R3 | Remover KGMatchService quebra imports de terceiros (worktrees, notebooks) | Stub com deprecation warning por 1 ciclo antes de remover |
| R4 | ExploreAgent instancia cliente LLM (pesado) mesmo em calls que só usam cache do índice | Mantido lazy-init (`_ensure_client()`). Index load é barato (< 100ms) |
| R5 | Perda de funcionalidade: `explore_turn` só funciona quando `profile` está no payload | No novo `/explore`, `profile` é campo opcional. Quando ausente → `explore()` (sem extração). Quando presente → `explore_turn()` (com extração) |
| R6 | Gargalo de I/O: `get_graph()` varre centenas de `.md` no disco a cada chamada (síncrono) | Cache em memória com `lru_cache(maxsize=1)` + invalidação por mtime do vault. `resolve_scope()` lê 1–3 arquivos — aceitável sem cache (D8) |
| R7 | Rate limit condicional não suportado pelo `@limiter.limit` padrão | Middleware customizado que inspeciona `request.state.user_id` e escolhe o bucket (3/min anônimo vs 10/min autenticado). Fallback: dois decorators com branch no router handler |
| R8 | Divergência de dados entre ExploreAgent e HybridMatch se um introduzir cache separado | D6b: ambos consomem `kg_store.load_index()` como source única. Nunca criar cache privado nos métodos de catálogo |
| R9 | `/explore` sem sessão perde continuidade da extração de perfil entre turns anônimos | D9: o cliente (frontend) mantém `profile` + `history` em `sessionStorage` / `localStorage` e reenvia a cada request. Servidor não armazena estado anônimo. Funciona como o `frontdoor` atual — não há amnésia |
| R10 | EntityMatcher muda comportamento de match de investidores/programas ao unificar `_make_client` e `_parse` | Manter os prompts, formatadores e enriquecedores EXATOS dos módulos originais — a unificação é estrutural, não funcional. Validar com `python -m core.eval investor_match` e `python -m core.eval programa_match` |
| R11 | `investor_match.py` e `programa_match.py` removidos — risco de quebrar import de notebooks/scripts que importam `match_investidores` de lá | Stub de deprecation: manter `from core.services.entity_matcher import match_investidores as _` nos arquivos originais por 1 ciclo com deprecation warning. Depois remover |
| R12 | EntityMatcher acopla investidores e programas num único módulo, mas catálogos futuros podem ter schema diferente | O design é aberto por composição: `EntityCatalog` é um dataclass, não uma hierarquia. Qualquer nova entidade só precisa implementar as 4 callbacks — sem herança, sem acoplamento |

---

## Fora de escopo

- Unificação de `list_editais`/`get_edital_by_id`/`get_stats` com `HybridMatchService` — duplicação aceita por D6
- ExploreAgent com memória (exploration_log) — spec separada (ver `docs/historical/agentic-evolution.md` Fase 3A)
- Deep research no ExploreAgent — gated por env `EXPLORE_DEEP_RESEARCH_ENABLED`, mantido
- Métricas de uso do /explore — adicionar ao dashboard de telemetria após merge
- Unificação de `HybridMatchService` com `EntityMatcher` — paradigmas diferentes (2 estágios vs Karpathy), manter separados
- Refactor do `RadarService` — pura orquestração, não toca I/O. Só precisaria de mudança se os schemas de saída mudarem, e não mudam
- Migração do `_make_client()` do `HybridMatchService` Stage 2 — tem seu próprio `_make_client` com suporte a embeddings (`MATCH_STAGE2A_BACKEND`), escopo diferente do Karpathy simples. Pode compartilhar base futuramente, não agora
