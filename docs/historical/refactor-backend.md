# Nota de arquitetura — reorg do backend (#3-backend)

> Esboço de alvos para a **Fase 2 do ROADMAP**: aplicar boas práticas
> de arquitetura na base **antes** do rewrite do frontend. NÃO é um plano fechado —
> é a marcha pra a conversa de execução não inventar. **Princípio guia:** o código
> hoje FUNCIONA e tem seams bons (não é um resgate); a reorg é sobre **legibilidade,
> fronteiras e apresentação profissional**, mudança de comportamento ZERO.

## Diagnóstico (2026-06-10)

O que está **bom** e deve ser preservado como referência de padrão:
- **Schema autoritativo em doc** (`docs/domain/schema.md` + `core/wiki_schema.py`, validado por teste) — regra vive no doc, código lê. Manter e expandir esse padrão.
- **Seam de dados único** (`core/kg_store.py`) — file vs Postgres atrás de uma fronteira só.
- **Harness de eval unificado** (`core/eval/`, registry) — uma suíte = uma linha.
- **Routers já começaram a sair** (`backend/auth_routes.py`, `library_routes.py`).

Onde **dói** (alvos da reorg):

| Sintoma | Evidência | Alvo |
|---|---|---|
| `backend/api.py` monolítico | 1206 linhas, 32 rotas num arquivo | quebrar em routers por domínio (matching, writing, library, profile, graph, brief) — seguindo o padrão de `auth_routes`/`library_routes` que já existe |
| `core/` é um saco flat | 48 módulos, 12.5k linhas, sem subpastas | agrupar por responsabilidade (ver abaixo) sem virar over-engineering |
| Serviços grandes | `writing_session.py` 1344, `hybrid_match_service.py` 891, `kg_match_service.py` 768 | avaliar extração de submódulos coesos (prompts, scoring, persistência) — só onde reduzir carga cognitiva, não por dogma |
| Mistura de camadas em `core/` | services + adapters LLM + helpers de domínio + agent runtime convivem | fronteiras explícitas (ver proposta) |

## Proposta de agrupamento (a validar, não cravada)

Migração **só de organização** (imports são absolutos `from radar.core...`; mover = ajustar imports + um passe de teste). Candidato:

```
core/
  services/      writing_session, hybrid_match_service, kg_match_service,
                 investor_match, radar_service, checklist_service, content_library
  retrieval/     retriever, embedder, chunker
  llm/           llm_client, agent_runtime, agent_tools/
  kg/            kg_store, wiki_schema, edital_id, temporal
  eval/          (já existe)
backend/
  routers/       matching, writing, library, profile, graph, brief  (de api.py)
  api.py         só app + middleware + wiring dos routers
```

**Cuidados:**
- **Aditivo e testado a cada passo** (mesma disciplina do multi-quadrante): mover um grupo → ajustar imports → suíte verde → commit. Nunca um big-bang.
- **NÃO mexer no frontend** aqui — ele vai ser reescrito na Fase 3; reorganizar agora = trabalho jogado fora.
- **Não inventar camadas que não pagam** (ex.: repositórios/DTOs genéricos) — o projeto é um produto, não um framework. Mover só onde a legibilidade melhora de verdade.
- Atualizar a seção "Package layout" do `CLAUDE.md` ao final.

## Apresentação (parte 3-apresentação, ortogonal à reorg de código)

- **README** com pitch do produto + arquitetura em 1 diagrama + como rodar (hoje o "como rodar" está no CLAUDE.md, voltado a agente).
- **Diagrama de arquitetura** (medallion ETL → KG → match/escrita → API → frontend).
- **CONTRIBUTING / convenções** (imports absolutos, schema-em-doc, eval-gated, etc.).
- Limpar cruft do repo (ex.: `*.bak` em `knowledge_graph/`, `fly.toml`/`deploy.sh` obsoletos — deploy é Vercel+Railway).
- Higiene de git: branches mortas, mensagens consistentes (já estão boas).
