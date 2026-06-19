# 03 — Camada web única + cache (Finding E)

**Fase:** 1 (plumbing) · **Validação:** teste + eval extração · **Esforço:** médio

## Problema

Existem ~3 implementações de fetch/web espalhadas, com acoplamento de direção
errada e **sem cache** — a mesma URL/busca é paga toda vez.

## Estado atual

- `profile_tools._fetch_and_parse` + `fetch_page` (cap 12k, `profile_tools.py:36`).
- `deep_research.fetch_url` (cap 3k, `deep_research.py:25,101`) — **reusa
  `_fetch_and_parse` de `profile_tools`**: uma tool de pesquisa dependendo de
  internals de uma tool de perfil (acoplamento invertido).
- `deep_research.web_search` → `core.web_search.web_search`.
- Nenhum cache em nenhum caminho.

## Mudança proposta

1. **Novo módulo canônico** `core/web/fetch.py` (ou `core/llm/agent_tools/web_tools.py`):
   - `fetch_url(url, *, char_limit) -> str` — HTTP GET + limpeza + truncamento
     parametrizado (absorve `_fetch_and_parse`).
   - `web_search(query, k) -> list[...]` — wrapper canônico.
2. **Cache TTL** (ex.: `functools` + dict com expiração, ou disco em
   `.web_cache/`): chave = URL/query normalizada. TTL configurável por env.
3. **Migrar consumidores:** `profile_tools` e `deep_research` passam a importar do
   módulo canônico. Remove a dependência `deep_research → profile_tools`.
4. Manter os caps específicos por chamador (12k perfil, 3k research) como
   parâmetro, não hard-code.

## Validação

- **Teste:** `fetch_url` respeita `char_limit`; segunda chamada à mesma URL é cache
  hit (não faz I/O — mockar transporte e assert 1 chamada).
- **Eval:** `python -m core.eval extraction` (usa fetch no Extrator) — sem
  regressão. Comportamento é preservado; eval é rede de segurança.

## Risco

Baixo-médio: cache pode servir conteúdo velho (mitigar com TTL curto). Garantir
que limpeza de HTML seja idêntica à atual para não mudar o texto que o LLM vê.

## Dependência

Nenhuma. Recomendado fazer cedo — habilita o cache que reduz custo nos demais
caminhos web (Descoberta, deep_research).
