# Spike: Claude Agent SDK como superfície do Explore

**Status:** exploratório concluído — GO para spec de migração · **Data:** 2026-07-16/17 · **Decisão:** SDK venceu 10/10 no julgamento cego; custo de harness exige desenho antes da migração.

## Contexto

Auditoria do Grantable (2026-07-16, ver memória `reference_grantable_docs`) mostrou que o produto-referência embute o harness do ecossistema Claude (skills como `SKILL.md`, tiers Sonnet/Haiku, AI budget repassado ao usuário) em vez de manter orquestrador próprio. Premissa custo-zero foi relaxada para a superfície agêntica interativa. Este spike mede o que o Agent SDK entrega sobre o **mesmo core** antes de qualquer decisão de migração.

## Objetivo

Comparar, lado a lado, o ExploreAgent atual (LangGraph) vs um agente Claude Agent SDK consumindo as **mesmas tools do core**, nos eixos: qualidade das respostas, latência, custo por sessão, esforço de código e capacidades que vêm de graça (skills, subagentes).

## Não-escopo

- Writing agent, critic, frontend, persistência de workspace.
- Qualquer mudança em `core/` — o spike só *consome* o core existente.
- Decisão de migração (é o output do spike, não parte dele).

## Desenho

### Tools expostas (wrappers finos sobre o core existente)

Reusar as factories já existentes, sem reimplementar lógica:

| Tool | Fonte atual |
|---|---|
| `find_matching_editais` | `core/llm/agent_tools/match_tools.py:83` |
| `search_entities` | `core/llm/agent_tools/explore_tools.py:254` |
| `get_edital` | `core/llm/agent_tools/explore_tools.py:72` |
| `get_node_neighborhood` | `core/llm/agent_tools/explore_tools.py:305` |
| `search_edital_factual` | `core/llm/agent_tools/factual_tools.py:15` |

Expor via **servidor MCP in-process do SDK** (`create_sdk_mcp_server` + decorator `@tool` do pacote Python `claude-agent-sdk`) — confirmar a API vigente em `code.claude.com/docs/en/agent-sdk` antes de codar; não inferir da memória. Nota: as tools atuais retornam `str` formatada para o LangGraph — passar o retorno cru, sem re-formatar.

### Estrutura

```
spikes/agent_sdk_explore/
  agent.py        # ClaudeAgentOptions + loop query() ou client interativo
  tools.py        # wrappers MCP sobre core/llm/agent_tools/*
  run_compare.py  # roda o protocolo nos dois agentes e salva transcript + métricas
  results/        # transcripts, custos, relatório
```

- **Modelo runtime do spike:** `claude-sonnet-5` (paridade com o default do Grantable; barato o suficiente para iterar). Não usar Opus no runtime.
- System prompt: portar o prompt do explore atual com o mínimo de adaptação (o SDK tem preset próprio; testar com `system_prompt` custom equivalente ao do LangGraph para comparação justa).
- Env: `ANTHROPIC_API_KEY` novo (billing separado do resto), DB local existente.

### Protocolo de comparação

1. **Conjunto de prompts:** 8–10 perguntas reais de explore. Reusar casos do golden de matching/explore existente onde couber + 2–3 perguntas factuais sobre edital (exercitam `search_edital_factual`) + 1 pergunta multi-hop (exercita `get_node_neighborhood`).
2. Rodar cada prompt nos dois agentes (LangGraph via caminho atual; SDK via `run_compare.py`), mesma base de dados.
3. Registrar por prompt: resposta final, tools chamadas (quais/quantas), latência total, tokens/custo (SDK reporta usage; no LangGraph usar o tracking existente).
4. **Qualidade:** julgamento manual do Lucas (cego para qual agente gerou, se prático) + opcionalmente o juiz LLM da matching_eval para os casos de match.

### Extra (só se o básico passar): 1 hora de exploração de harness

- Uma skill `SKILL.md` de exemplo ("analisar aderência do meu perfil a um edital") para sentir o mecanismo que o Grantable expõe ao usuário.
- Um subagente read-only para fan-out (ex.: comparar 3 editais em paralelo).

## Critérios de decisão (gate do spike)

**GO para spec de migração se:**
- Qualidade ≥ LangGraph no conjunto de prompts (sem regressão perceptível);
- Custo por sessão de explore em Sonnet for compatível com repasse via plano (registrar o número, comparar com o baseline gpt-4o-mini atual);
- Esforço do wrapper for pequeno (ordem de ~200 LOC, sem tocar `core/`).

**NO-GO / adiar se:** integração exigir refazer contratos das tools, custo por sessão for ordem(ns) de magnitude acima do aceitável, ou qualidade cair.

## Entregável

`spikes/agent_sdk_explore/results/report.md` com: tabela comparativa por prompt, custo médio por sessão nos dois, achados de DX (o que o SDK deu de graça vs o que deu trabalho), e recomendação go/no-go com os números.
