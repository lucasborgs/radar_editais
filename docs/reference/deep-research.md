# Deep Research — referência vigente

**Status:** referência vigente · **Verificado em:** 2026-07-14

Deep Research é uma capacidade transversal de pesquisa web com proveniência.
Não é um agente top-level nem uma fonte que publique diretamente no catálogo.

## Fluxo atual

```text
WritingAgent ou ExploreAgent
  → tool deep_research(question)
  → subagente em core.deep_research
  → web_search (Tavily) + fetch compartilhado
  → resposta com fontes no turno
  → research_findings (verified=false, quando há workspace + DB)
  → revisão humana
  → promoção para Content Library ou rejeição
```

## Contratos

- `core/llm/agent_tools/research_tools.py` expõe a tool e persiste findings sem
  bloquear a resposta quando staging falha;
- `core/deep_research.py` executa o subagente e exige síntese apoiada nas fontes;
- `core/web_search.py` usa Tavily e falha de forma controlada sem
  `TAVILY_API_KEY`;
- `core/web/fetch.py` é o fetch compartilhado por pesquisa, perfil e Descoberta;
- `research_findings` é isolada por workspace e recebe itens não verificados;
- `backend/routers/research.py` lista, promove e rejeita findings; e
- a promoção cria um item curado na Content Library, nunca conhecimento global.

O WritingAgent recebe a tool no conjunto padrão. O ExploreAgent só a recebe com
`EXPLORE_DEEP_RESEARCH_ENABLED=true`. Ausência de chave, falha de busca ou falha
de staging não quebra o núcleo de escrita ou exploração.

## Autoridade

Este documento explica o caminho implementado. Configuração suportada vive em
`.env.example`; contratos gerais do runtime vivem em
[`architecture.md`](../architecture.md). O desenho original está preservado em
[`deep-research-design.md`](../historical/deep-research-design.md).
