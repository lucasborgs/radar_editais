# KG-P1C — Exploração orientada pelo perfil

> Status: implementada localmente. Sem produção, rede, merge, push ou deploy.

## Identificação

| Campo | Valor |
|---|---|
| Branch | `codex/kg-phase1-production-c` |
| Base | `a068c8d0d` (`main` local) |
| Worktree | `/private/tmp/radar-editais-kg-phase1c` |
| Commit funcional | `5bb817c6a` — `fix(kg): correct profile-first graph strategy contract` |
| Commit documental | `a criar` — `docs(kg): update KG-P1C audit corrections` |

## O que mudou

Quando `KG_PHASE1_EXPLORE_ENABLED=false` (default), a montagem das ferramentas e
as instruções do Explore permanecem no caminho anterior. Quando a flag está
ligada, o agente recebe exclusivamente `graph_strategy`. A ferramenta captura
por closure o `CompanyProfilePayload` já autenticado pelo runtime; o modelo não
controla o perfil, não fornece `entity_ref` e não inicia a consulta a partir de
um edital ou node ID.

O perfil é o `CompanyProfilePayload` canônico validado pelo Pydantic. Ele só usa
os campos enviados pelo produto: identificação, textos descritivos, UF, TRL,
estágio e tipos de financiamento. O perfil é representado por `perfil:virtual`,
criado apenas em memória; nenhum campo `setores`, `tecnologias_tags` ou `temas`
é adicionado ao contrato.

A projeção textual é determinística e conservadora: `gold.normalize_setores`
e `gold.normalize_tag` reutilizam a taxonomia/aliases vigentes de
`schema.py`. Setores são reconhecidos apenas por aliases da taxonomia fechada;
tecnologias apenas quando o valor do nó ou alias gold aparece como frase inteira
no texto canônico. O payload separa `profile.declared` de `profile.projected`;
qualquer dimensão não sustentada fica em `profile.unresolved`. Não há keywords
específicas da iFlorestal, ontologia, LLM ou embedding.

Uma BFS limitada percorre oportunidades/editais, programas, agências, ICTs e
investidores. Para cada candidato, todos os vínculos diretos com âncoras de
qualidade são agregados e deduplicados; o ranking ordena por quantidade de sinais
compartilhados, depois distância e ID canônico. Um único caminho determinístico
é preservado para explicação.

## Fatos, atributos e derivações

Cada rota iniciada pelo nó virtual é recomendação/afinidade derivada, mesmo
quando contém fatos fortes. O payload usa três camadas:

- `route_relation`: sempre `derived_profile_route`, `confirmed=false`;
- `supporting_facts`: atributos `phase1_deterministic` e vínculos
  `phase1_structural` confirmados dentro do caminho;
- `derived_steps`: âncora do perfil, similaridade, ponte tecnológica e qualquer
  passo sem informação suficiente, sempre não confirmado.

O mapeamento das origens é:

| Origem | Classificação |
|---|---|
| `phase1_structural` | `catalog_structural_fact` |
| `phase1_deterministic` | `cataloged_attribute` |
| `phase1_similarity` ou `phase1_tech_bridge` | `derived_graph_step` |
| `profile_ephemeral` | `profile_affinity_anchor` |
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

## Mapa da rede de segurança da suíte anterior

| Contrato anterior | Tratamento P1C |
|---|---|
| snapshot consistente/mesma geração | preservado nas suítes de projeção; indisponibilidade coberta aqui |
| falha de banco e logs sanitizados | preservado e testado aqui |
| profundidade, caminhos, payload e UTF-8 | preservado onde aplicável; `graph_strategy` mantém teto UTF-8 |
| hubs multissetoriais | preservado na projeção e coberto no caminho estratégico |
| direção/origem das arestas | preservado no caminho/evidências |
| determinismo e travessia sem corte de atores | preservado; fronteiras BFS agora ordenadas e há teste de `PYTHONHASHSEED` |
| flag desligada/system prompt anterior | preservado byte a byte |
| três graph tools antigas | legitimamente substituídas por `graph_strategy` exclusiva |
| resolução por `entity_ref`/comunidade | legitimamente removida do fluxo profile-first; não inicia pelo catálogo |

## Suíte diagnóstica e limites

O caso amplo `iforestal-profile-strategy` foi adicionado à suíte `explore`; a
suíte continua diagnóstica, sem gate novo. Os testes herméticos cobrem resolução
de múltiplas âncoras, ausência sem inexistência de mercado, cobertura,
determinismo sob embaralhamento e `PYTHONHASHSEED`, classificação de origens,
atributos não resolvidos, projeção canônica, agregação de três sinais, teto
UTF-8, snapshot indisponível, injeção pelo runtime e exclusão de Match/catalog
tools no modo ativo. O teste hermético executa `strategy_payload`/`graph_strategy`
sobre snapshot controlado; o golden apenas valida o contrato do perfil. A
execução LLM conectada permanece opcional e não foi executada nesta correção.

O grafo atual só oferece as dimensões estruturadas já materializadas. Descrições
de solução, atividades e outros campos livres permanecem não resolvidos até
que uma ingestão autorizada os materialize; a P1C não cria ontologia, migration,
índice ou entidade da empresa.

## Validação

- `ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit/test_kg_phase1_explore_tools.py tests/unit/test_explore_agent.py tests/unit/test_phase1_lifecycle.py tests/unit/test_explore_golden_cases.py tests/unit/test_explore_routing.py` — **80 passed, 2 skipped**.
- `ENVIRONMENT=test PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/unit` — **2185 passed, 2 skipped**.
- `/Users/lucasborges/radar_editais/.venv/bin/ruff check $(git ls-files '*.py')` — **All checks passed**.
- `git diff --check a068c8d0d..HEAD` — **limpo**.
- `git diff --check` — limpo.
- Não foram acessados `.env`, produção, Supabase remoto, rede ou LLM real.

Não foram alterados Match, Writing, RAG, memória, frontend, schema persistido,
migrations ou Fase 2.

## Auditoria

Auditoria Codex: pendente.
