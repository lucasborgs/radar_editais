# Spec — DeepResearch (busca web com fonte, learning humano-gated)

> **Objetivo:** permitir que o usuário peça um dado/informação da internet sem sair da interface; o agente busca, sintetiza **com fonte**, e o resultado só vira conhecimento persistente do projeto após o usuário confirmar.
> **Base:** branch `test-integration`. **Data:** 2026-06-03.
> **Enquadramento (CoALA):** grounding externo (busca) → working memory (turno) → **gate do usuário** → learning na memória semântica **do projeto** ([docs/historical/COALA.md](historical/COALA.md)). Converge com a filosofia Grantable ("AI finds, humans learn").

## Decisões travadas

| # | Tópico | Decisão |
|---|--------|---------|
| 1 | Backend de busca | **Tavily** (API p/ agentes: conteúdo limpo + URLs explícitas; fit para "fato com fonte") |
| 2 | Forma | **Subagente-como-tool**, não loop top-level paralelo. Tool `deep_research(question)` exposta ao Redator (e ao Explorador) |
| 3 | Duas intenções | **Um pipeline só.** Intenção 1 = grounding sem learning (vive no turno). Intenção 2 = + learning gated |
| 4 | Onde grava o learning | **ContentLibrary** (semântica do **workspace**), **NUNCA** o KG (semântica global compartilhada) |
| 5 | Proveniência | Obrigatória. Todo fato carrega `source_url` + trecho citável. Sem prosa solta |
| 6 | Frescor | Item entra como `type='web_research'` → meia-vida de decay mais agressiva que documento interno |

## Correção conceitual (CoALA, estrito)

A observação do grounding **entra na working memory no próprio turno** (senão o agente não conseguiria te mostrar). O que o gate do usuário controla é a **ação de learning** (escrita na memória de longo prazo), não a entrada na working memory. Sem confirmação, o fato é esquecido no fim da sessão — exatamente o comportamento desejado.

---

## Arquitetura

```
Redator/Explorador (run_agent loop)
   └─ tool deep_research(question)        ← subagente-como-tool
        └─ run_agent interno (max_steps ~5)
             ├─ tool web_search(query)    ← Tavily (port abstrato)
             └─ tool fetch_page(url)       ← reusa profile_tools (leitura profunda)
        ⇒ retorna: síntese + fontes [{url, title, quote}]
   ⇒ resultado na working memory do turno (SEMPRE)
        └─ UI mostra fontes "pendentes"
             └─ [usuário confirma] ⇒ learning:
                  create_item(type_='web_research', source_url=…, enrich=True)
                  ⇒ ContentLibrary do workspace ⇒ retrievable via search_library
```

### Port de busca (backend plugável)
`core/web_search.py` — abstração para não acoplar a Tavily:
```python
@dataclass
class SearchHit:
    title: str; url: str; snippet: str; content: str  # content: já limpo (Tavily)

def web_search(query: str, k: int = 5) -> list[SearchHit]: ...
```
Backend Tavily atrás de `WEB_SEARCH_BACKEND` (default `tavily`) + `TAVILY_API_KEY`. Falha graciosa: sem chave → tool retorna string de erro (loop nunca quebra, padrão [agent_runtime](../core/agent_runtime.py)).

### Subagente DeepResearch
`core/agent_tools/research_tools.py`:
- `build_research_subagent_tool()` → retorna um `Tool` cujo `func` roda um `run_agent` interno.
- Tools internas: `web_search` (Tavily) + `fetch_page` (reusa [core/agent_tools/profile_tools.py](../core/agent_tools/profile_tools.py)).
- `max_steps` baixo (≈5): Tavily já devolve `content`, então muitas perguntas não precisam de `fetch_page`.
- **System prompt anti-fabricação:** sintetize **apenas** o que está nas fontes; toda afirmação mapeia a uma fonte retornada; se não achou, responda "não encontrei evidência" — nunca preencher de memória.

### Contrato de retorno
A tool devolve ao agente chamador uma **string formatada** com a resposta + bloco de fontes (URL + trecho), e expõe no `TraceStep`/telemetria um payload estruturado `{answer, sources[]}` para o frontend montar o painel de "fontes pendentes".

### Exposição
- Tool no **Redator** ([core/agent_tools/writing_tools.py](../core/agent_tools/writing_tools.py)) — o caso "preciso de um dado pra escrever esta seção".
- Tool no **Explorador** ([core/agent_tools/explore_tools.py](../core/agent_tools/explore_tools.py)) — opcional, fase 2.
- **Não** é um agente top-level que o usuário invoca à parte (fragmentaria o contexto da sessão).

---

## Ação de learning (gate → ContentLibrary)

Reusa o que já existe — **delta de implementação pequeno**:
- `create_item(db, workspace_id, title, type_='web_research', content, tags, source_url=<fonte>, enrich=True)` ([core/content_library.py:166](../core/content_library.py#L166)).
- `enrich_content_task` (procrastinate) preenche summary/key_facts/themes/embedding async — **já existe** (ADR M8).
- Depois disso, o fato é recuperável via `search_library` em turnos futuros.

### Frescor / decay
O decay já roda: `effective_importance = importance_score * exp(-(now - last_referenced_at)/half_life)` ([content_library.py:290](../core/content_library.py#L290)). Extensão: **meia-vida por `type`** — `web_research` recebe `half_life` menor que documento interno da empresa (fato web envelhece mais rápido). Pequena mudança na fórmula de decay para parametrizar por tipo.

### Proveniência no uso
Quando o Redator usa um item `web_research`, a citação aponta para `source_url`, não para "a empresa afirma". O fato continua sujeito ao regime de grounding por-claim do [spec de robustez](spec_robustez_match_escrita.md) — é **claim externo com fonte**, não verdade interna.

---

## API + Frontend (esboço)

- A tool roda dentro do turno existente (`POST /writing/turn`); as fontes pendentes saem no payload da resposta do turno.
- Novo endpoint de confirmação: `POST /library/from-research` → `create_item(type_='web_research', …)`. Alternativamente reusar `POST /library` com `type='web_research'`.
- Frontend: painel lateral de "fontes encontradas (pendentes)" com botão importar → chama o endpoint acima. (Detalhar em tarefa de frontend.)

---

## Riscos

- **Fabricação na síntese.** Maior risco, contra a tese de robustez. Mitigação: prompt estrito + toda claim mapeia a uma `SearchHit` retornada; eval com casos onde a resposta correta é "não encontrei".
- **Custo Tavily.** Cap de chamadas por turno/sessão; `web_search` antes de `fetch_page` (evita crawl desnecessário).
- **Vazamento para o KG.** Proibido por design: o learning do DeepResearch grava **só** na ContentLibrary do workspace. Promover para o KG global é o problema 2.2 (dedup/schema/revisão), fora desta spec.
- **Loop do subagente.** `max_steps` baixo + Tavily devolvendo `content` direto.

## Faseamento

| Fase | Escopo | Gate |
|------|--------|------|
| **A** | `web_search` (Tavily) + subagente + tool no Redator + retorno com fontes | agente responde com fontes; nada persiste sem confirmação |
| **B** | Gate de learning: endpoint + `create_item(web_research)` + painel frontend | item importado vira retrievable via `search_library` |
| **C** | Decay por tipo + tool no Explorador + eval anti-fabricação | meia-vida de `web_research` menor; eval verde |

## Critérios de aceitação (Fase A)

- `web_search("...")` retorna `list[SearchHit]` com URLs reais via Tavily; sem `TAVILY_API_KEY` degrada com mensagem, não exceção.
- `deep_research(question)` devolve síntese **com bloco de fontes**; trace estruturado `{answer, sources[]}` disponível.
- Redator consegue chamar `deep_research` num turno; resultado aparece no turno sem persistir nada.
- Nenhum caminho escreve no KG; nenhuma persistência na library sem o gate da Fase B.
