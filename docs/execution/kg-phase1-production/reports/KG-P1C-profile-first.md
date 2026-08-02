# KG-P1C — Exploração orientada pelo perfil

> Status: implementada localmente. Sem produção, rede, merge, push ou deploy.

## Identificação

| Campo | Valor |
|---|---|
| Branch | `codex/kg-phase1-production-c` |
| Base | `a068c8d0d` (`main` local) |
| Worktree | `/private/tmp/radar-editais-kg-phase1c` |
| Commit funcional | `feat(kg): add profile-first strategic graph exploration` |
| Commit documental | `docs(kg): report KG-P1C profile-first exploration` |

## O que mudou

Quando `KG_PHASE1_EXPLORE_ENABLED=false` (default), a montagem das ferramentas e
as instruções do Explore permanecem no caminho anterior. Quando a flag está
ligada, o agente recebe exclusivamente `graph_strategy`. A ferramenta captura
por closure o `CompanyProfilePayload` já autenticado pelo runtime; o modelo não
controla o perfil, não fornece `entity_ref` e não inicia a consulta a partir de
um edital ou node ID.

O perfil é representado por `perfil:virtual`, criado apenas em memória. UF,
estágio, TRL, financiamento e, quando presentes em payloads já enriquecidos,
setores/tecnologias/temas são resolvidos por igualdade exata com os nós de
qualidade do snapshot. Texto livre não é tokenizado nem aproximado; dimensões
sem correspondência entram em `profile.unresolved`.

Uma BFS limitada percorre oportunidades/editais, programas, agências, ICTs e
investidores. A saída agrupa resultados por tipo e inclui caminho, características
compartilhadas, cobertura consultada, truncamento, limitações e classificação da
relação. O desempate é determinístico por distância, quantidade de características
compartilhadas e ID canônico.

## Fatos, atributos e derivações

O campo `relation` é derivado somente da origem das arestas do snapshot:

| Origem | Classificação |
|---|---|
| `phase1_structural` | `catalog_structural_fact` |
| `phase1_deterministic` | `cataloged_attribute` |
| `phase1_similarity` ou `phase1_tech_bridge` | `derived_relation` |
| origem ausente/desconhecida | `insufficient_information` |

As relações derivadas nunca recebem `confirmed=true`. A cobertura marca cada
tipo como `queried` ou `not_queried`, informa totais/retornos/truncamento e usa
"ausência no recorte atualmente representado pelo grafo"; snapshot indisponível
ou falha não acionam catálogo, Match ou outro fallback silencioso.

## Diferença concreta em relação à spike

`scripts/ab_spike_explore.py` começava com uma entidade fixa
(`edital:finep:783`) e passava um perfil fixo de laboratório, além de exercitar
perguntas em torno dessa âncora. A KG-P1C remove essa dependência: começa pelo
perfil autenticado, cria apenas uma âncora virtual efêmera, consulta todos os
tipos solicitados numa chamada e não contém IDs, nomes ou regras específicas da
iFlorestal.

## Suíte diagnóstica e limites

O caso amplo `iforestal-profile-strategy` foi adicionado à suíte `explore`; a
suíte continua diagnóstica, sem gate novo. Os testes herméticos cobrem resolução
de múltiplas âncoras, ausência sem inexistência de mercado, cobertura,
determinismo sob embaralhamento, classificação de origens, atributos não
resolvidos, teto UTF-8, snapshot indisponível, injeção pelo runtime e exclusão
de Match/catalog tools no modo ativo.

O grafo atual só oferece as dimensões estruturadas já materializadas. Descrições
de solução, atividades e outros campos livres permanecem não resolvidos até
que uma ingestão autorizada os materialize; a P1C não cria ontologia, migration,
índice ou entidade da empresa.

## Validação

- `ENVIRONMENT=test PYTHONPATH=src .venv/bin/pytest -q tests/unit/test_kg_phase1_explore_tools.py tests/unit/test_explore_agent.py tests/unit/test_phase1_lifecycle.py tests/unit/test_explore_golden_cases.py` — **67 passed, 2 skipped**.
- `.venv/bin/ruff check src/radar/core/kg/phase1/tools.py src/radar/core/services/explore_agent.py` — **All checks passed**.
- `git diff --check` — limpo.
- Não foram acessados `.env`, produção, Supabase remoto, rede ou LLM real.

Não foram alterados Match, Writing, RAG, memória, frontend, schema persistido,
migrations ou Fase 2.

## Auditoria

Auditoria Codex: pendente.
