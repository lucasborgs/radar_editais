# Spec — ExploreAgent com Rota Factual (Routing)

> **Registro histórico:** o dispatcher de três rotas descrito abaixo foi
> substituído pelo caminho único `_explore_agent`. Consulte
> [`docs/architecture.md`](../architecture.md).

Status original: **implementado** · 2026-06-24 · Fase 0 (classificador + factual route) e Fase 1 (WritingSession sem resolve_scope) concluídas. Fase 2 (eval) pendente de API keys.

---

## Contexto

O ExploreAgent hoje é LLM-heavy em **todos** os caminhos — mesmo perguntas puramente factuais ("quais editais estão abertos?", "mostra o edital 589") passam por gpt-4o-mini com o catálogo inteiro no contexto. O custo é baixo por chamada (~$0.001 para factual), mas o problema é arquitetural: o GraphService já tem toda a informação necessária para responder factualmente sem inferência, e não é usado.

### Rotas internas do ExploreAgent (após implementação)

| Rota | LLM? | Custo | Trigger |
|------|------|-------|---------|
| `_factual_route()` | **Não** | $0 | stats, lista, card por ID |
| `_explore_legacy()` | Sim — 1 call | ~$0.002 | conceitual, fallback |
| `_explore_agent()` | Sim — multi-step | ~$0.05–0.20 | precisa de dados + raciocínio |
| `match()` | Sim — 1 call | ~$0.002 | Karpathy, chamado direto |

### Serviços sem LLM (alimentam a rota factual)

| Serviço | Métodos | Consumido por |
|---------|---------|---------------|
| `GraphService` | `get_graph()`, `resolve_scope()`, `edital_ids_for_node()`, `find_analogue_ids()` | ExploreAgent factual route + `GET /graph` |
| `ExploreAgent.readers` | `get_stats()`, `list_editais()`, `get_edital_by_id()` | factual route + agent tools |

---

## Design

### Três rotas no dispatcher

```
POST /explore
  ↓
ExploreAgent.explore(message, profile, history)
  ↓
[classificador de intenção determinístico]
  │
  ├── ROTA FACTUAL (sem LLM)
  │     "quais editais estão abertos?"
  │     "mostra o edital finep:589"
  │     "quantos editais tem em saúde?"
  │     → GraphService + ExploreAgent readers
  │     → resposta formatada (template string)
  │     → custo: ZERO
  │     → SE não achou dados compatíveis → sobe para raciocínio
  │
  ├── ROTA RACIOCÍNIO (1 LLM call, ~$0.002)
  │     "o que é subvenção?"
  │     "qual a diferença entre subvenção e reembolsável?"
  │     → GraphService fornece contexto factual
  │     → 1 chamada LLM (legacy, template existente)
  │     → resposta com raciocínio sobre o catálogo
  │
  └── ROTA AGENTE (multi-step, ~$0.05–0.20)
        "qual edital combina com minha startup de bio?"
        "compare os prazos de saúde vs energia e me recomende"
        "quais ICTs podem fazer parceria nos editais de agro?"
        → agente com tools (list_editais, get_edital, find_analogues, etc.)
        → resolve dados + raciocínio integrados
        → histórico completo disponível para o agente
```

**Regra do classificador (implementada em `_classify_intent`):**

Ordem de avaliação (a primeira que match vence):

1. **`has_profile=True`** → rota agente (precisa cruzar perfil com catálogo)
2. **`has_edital_ids=True` + pergunta simples** (`mostra|exibe|abre|detalhes`) → factual; senão → agente
3. **Menciona entidade não-edital** (`ict|fundo|investidor|embrapii|sebrae`) → agente (precisa de tools)
4. **Padrão de raciocínio** (`compare|melhor|recomende|qual combina|pra mim|sugira|ajuda`) → agente
5. **Padrão factual** (`^(quais|quantos|lista|mostra|tem|existe|abertos|filtra|busca)`) → factual
6. **Padrão conceitual** (`^(o que é|como funciona|explique|qual a diferença|defina)`) → reasoning
7. **Fallback** → reasoning (1 call, barato)

O classificador é uma função pura (regex + regras, ~µs). Não usa LangGraph porque não há estado a gerenciar: a mensagem atual é suficiente para decidir, e o custo de errar é baixo (~$0.002 se cair em raciocínio em vez de factual, zero se factual cair para raciocínio via fallback).

#### Por que o classificador não precisa de estado entre turnos

O ExploreAgent é stateless: cada request carrega o histórico completo do cliente (`history[]`). O classificador decide com base **apenas na mensagem atual** porque o fallback cobre os erros:

| Erro | Consequência | Custo |
|------|-------------|-------|
| Factual → Raciocínio (falso positivo do factual) | LLM responde com histórico + contexto | ~$0.002 |
| Raciocínio → Factual (classificador subestimou) | Factual não acha dados → fallback para raciocínio | zero + ~$0.002 |
| Agente → Raciocínio (perda de ferramentas) | Resposta sem dados do grafo (pobre) — mas o LLM indica que precisa de mais dados | ~$0.002 |

O pior caso (pergunta que precisava de agente cai em raciocínio) custa ~$0.002 e o usuário reformula. Não justifica a complexidade de um classificador stateful (LangGraph, memória entre turnos, etc.).

### Factual response format

A rota factual retorna markdown simples (string). Três templates:
- **stats:** `"**42 editais** no catálogo · 42 abertos · 7 temas · 3 fontes"`
- **tabela:** tabela markdown com ID, Título, Status, Prazo (até 20 linhas)
- **card:** nome do edital + campos (ID, Status, Prazo, Mecanismo, Elegíveis, Valor, Objetivo, Requisitos)

### Onde cada método fica

| Operação | Rota | Implementação |
|----------|------|---------------|
| `get_stats()` | factual | `ExploreAgent.get_stats()` → markdown |
| `list_editais(status="ABERTA")` | factual | `ExploreAgent.list_editais()` + `_format_edital_table()` |
| `get_edital_by_id(id)` | factual | `kg_store.load_wiki_page()` + `_format_edital_card()` |
| `list_por_tema(tema)` | factual | `ExploreAgent.list_editais(tema=X)` + `_format_edital_table()` |
| Pergunta conceitual | reasoning | `_explore_legacy()` (1 LLM call) |
| Match com perfil | agent | `_explore_agent()` (multi-step com tools) |
| Comparação | agent | `_explore_agent()` (multi-step com tools) |

---

## Mudanças implementadas

### 1. `explore()` — nova interface

```python
def explore(
    self,
    message: str,
    history: list[dict] | None = None,
    edital_ids: list[str] | None = None,
    node_id: str | None = None,
    node_type: str | None = None,
    has_profile: bool = False,          # novo: router passa se veio profile
    workspace_id: str | None = None,
    db=None,
) -> str:
```

`agent_enabled` removido. O classificador decide a rota internamente. `has_profile` informa o classificador para rotear para agent (precisa cruzar perfil com catálogo).

### 2. Router `/explore` simplificado

Antes (3 caminhos):
```
agent_enabled=True  → explore_agent.explore(agent_enabled=True) + diff separado
profile != None     → explore_agent.explore_turn() (resposta + profile_updates)
default             → explore_agent.explore(agent_enabled=False)
```

Depois (1 caminho):
```
explore_agent.explore(has_profile=...) → answer
if profile: ProfileExtractor.extract_diff_from_message() → profile_diff
```

`explore_turn()` não é mais chamado — extração de perfil é responsabilidade do router via `ProfileExtractor`, desacoplada da rota de resposta.

### 3. WritingSession sem `resolve_scope`

`_scope_edital_ids = [self.edital_id]` — análogos pertencem à descoberta (ExploreAgent), não à escrita.

### 4. GraphService exclusivo do ExploreAgent

GraphService instanciado dentro de ExploreAgent. Nenhum outro serviço importa GraphService. `GET /graph` router preservado para frontend.

### 5. ExploreAgent como porta de entrada

```
Visitante → POST /explore
              ├── factual → resposta imediata, zero LLM
              ├── reasoning → 1 LLM call
              └── agent → multi-step, ferramentas
          → POST /match (EntityMatcher)
          → POST /writing/start (WritingSession)
```

---

## Arquivos (implementado)

| Arquivo | Mudança |
|---------|---------|
| `core/services/explore_agent.py` | +`_classify_intent()`, +`_factual_route()`, +`_format_edital_card()`, +`_format_edital_table()`. `explore()` vira dispatcher das 3 rotas. `agent_enabled` removido. |
| `core/services/writing_session.py` | `_resolve_edital_scope()` → `return [self.edital_id]`. |
| `backend/routers/explore.py` | `agent_enabled` removido do modelo. Handler simplificado: sempre chama `explore()` + extração de perfil via `ProfileExtractor`. |
| `tests/unit/test_explore_agent.py` | 9 novos testes: `_classify_intent` (factual/reasoning/agent/fallback/edital_ids) + dispatcher (3 rotas + fallback factual→reasoning). |

---

## Decisões

| # | Decisão | Status |
|---|---------|--------|
| D1 | Classificador 100% determinístico (regex + regras). Zero LLM na decisão de rota. | ✅ |
| D2 | Rota factual nunca chama LLM. Fallback factual→reasoning quando não acha dados. | ✅ |
| D3 | Rota reasoning (1 call) usa `_explore_legacy`. Sem tools, sem agente. | ✅ |
| D4 | Rota agent (multi-step) usa `_explore_agent`. Mantida para perguntas complexas. | ✅ |
| D5 | WritingSession perde análogos. Pendente de validação via eval. | ✅ código, ⏳ eval |
| D6 | GraphService continua servindo `GET /graph`. | ✅ |

---

## Status da implementação

```
Fase 0  Classificador + rota factual dentro do ExploreAgent  ✅
          ├── classify_intent() com regex
          ├── _factual_route() usando GraphService + ExploreAgent readers
          └── explore() vira dispatcher das 3 rotas
          ↓
Fase 1  Remover resolve_scope do WritingSession              ✅
          ├── _scope_edital_ids = [self.edital_id]
          └── router simplificado (sem agent_enabled, sem explore_turn)
          ↓
Fase 2  Validar com eval                                     ⏳
          ├── python -m radar.core.eval writing  (precisa API keys)
          ├── python -m radar.core.eval matching (precisa API keys)
          └── se regressão em writing → reavaliar D5
```

---

## Gate de eval

- `python -m pytest tests/ -x --tb=short` — **712 pass, 2 skip, 0 regressão** ✅
- `python -m radar.core.eval matching` — sem mudança nos scores (ExploreAgent.match não muda) ⏳
- `python -m radar.core.eval writing` — verificar se qualidade textual cai sem análogos ⏳

---

## Riscos

1. **Classificador falso-positivo**: pergunta de raciocínio cai na rota factual → resposta pobre. Mitigação: quando factual não encontra match nos dados, sobe para raciocínio como fallback. ✅ implementado
2. **Análogos na escrita**: se remover análogos piorar a qualidade, reverter D5 e fazer análogos como enriquecimento via `retrieve_chunks` (não via `resolve_scope`), com flag `max_analogues` default 0. ⏳ pendente de eval
3. **ExploreAgent readers duplicam GraphService**: `list_editais()` e `get_edital_by_id()` existem em ambos. A rota factual usa readers do ExploreAgent (índice JSON) como fonte; não é um problema real porque o GraphService opera sobre o vault .md, não o índice. A duplicação é aceitável.
4. ~~**Dead code**: `explore_turn()` e `EXPLORE_PROFILE_EXTRACTION_INSTRUCTION` não são mais chamados.~~ ✅ removidos
