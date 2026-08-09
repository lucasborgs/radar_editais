# SDD de execução — Consultor estratégico v1

Este diretório decompõe [`docs/specs/strategic-consultant-v1.md`](../../specs/strategic-consultant-v1.md) em fatias de implementação para a próxima versão do Radar. A spec mandatória é a autoridade; [`knowledge-ecosystem-evolution.md`](../../specs/knowledge-ecosystem-evolution.md) só é usado para registrar decisões adiadas e critérios de promoção.

Não há código, migração, plano de deploy ou alteração das specs neste SDD.

## Resultado pretendido

O usuário entra com uma intenção ainda incompleta e percorre uma única jornada:

```text
conversa → brief revisável → projeto confirmado → caminhos explicados
→ escolha persistente → próximo passo ou artefato de escrita fundamentado
```

O `ConsultantGraph` é a autoridade da progressão conversacional. Ele mantém referências ao contexto e chama módulos profundos por interfaces pequenas. Não absorve regra de elegibilidade, ingestão, busca documental ou persistência especializada.

Os contratos centrais são `BriefProjeto`, `ProjetoInovacao` e `CaminhoInovacao`. Eles são os mesmos objetos vistos pela API, pelo orquestrador, pela interface web, pela avaliação de caminhos e pela escrita. Perfil, memória, `Knowledge`, `Pathways`, `DocumentIntelligence`, RAG e Writing não mantêm cópias privadas incompatíveis.

## Baseline encontrado

### Reutilizável

- `CompanyProfile`/`CompanyProfilePayload`, `profile_from_workspace`, `PUT /me/profile` e os cards de diff implementam perfil estruturado, origem humana e aceitação explícita.
- `ExploreAgent`, `run_agent`/`run_agent_async`, `run_agent_streaming_async` e `agent_graph.py` já fornecem ReAct em LangGraph, trace, streaming, limite de passos, checkpointer e degradação graciosa.
- `entity_catalog.py` é a leitura gold atual: entidades, relações, temporalidade, proveniência, vizinhança, tags e busca semântica. `match_v3.py` e `eligibility.py` fornecem recuperação/ranking e regras duras reutilizáveis como adapters internos.
- `domain_paths.py` já separa classificação determinística, requisitos, evidências básicas, lacunas e próximo passo, mas ainda produz uma anotação efêmera dentro do payload do Match.
- `source_docs.py`, `source_bundles`, ingestão gold, `research_findings` e o gate de promoção da Discovery já dão uma base para documentos canônicos, pesquisa web e revisão humana.
- `WritingSession`, `retrieve_chunks`, `contextual_retrieval`, `checklist_service`, critic, biblioteca, streaming e `writing_sessions/session_turns` já cobrem boa parte da execução textual.
- Memória de trabalho do LangGraph, transcript persistido de conversas e `reflection_service` são mecanismos de continuidade reutilizáveis, com a regra de que memória não vira fato canônico automaticamente.
- O harness em `src/radar/core/eval/` e os testes focais existentes são a base de validação; não será criado um harness paralelo.

### Precisa ser conectado

- A home `/explore` hoje invoca `ExploreAgent` e salva transcript, mas o perfil, o possível planejamento e a escrita não compartilham um objeto de projeto.
- O Match v3 retorna `caminho`/`explicacao` dentro de cards, porém não os persiste, não recebe `ProjetoInovacao` e não mantém seleção/reavaliação.
- O streaming do Explore tem uma implementação síncrona e outra espelhada; somente o streaming usa thread durável por sessão. O `ConsultantGraph` deve absorver essa divergência.
- `find_matching_opportunities` e `build_match_tools` ainda são expostos como “match”; deverão virar adapters internos de `Knowledge`/`Pathways` e desaparecer das superfícies de produto quando o substituto estiver funcional.
- A escrita recebe `edital_id`, perfil e plano ad hoc. Ela precisa abrir com `CaminhoInovacao` e aceitar que um caminho aberto não tenha edital formal.
- `writing_sessions` já generaliza conversas, mas ainda não é o store de brief/projeto/caminho e a tela `/projects` lista apenas sessões de escrita.

### Contratos que precisam ser criados

- Estado persistente do `ConsultantGraph` com workspace, conversa, brief, projeto, caminhos, decisão, lacunas e próximo passo.
- `BriefProjeto`, `ProjetoInovacao` e `CaminhoInovacao`, com versão, estado, origem, confiança, evidências e referências estáveis.
- Interface `Knowledge`: `search`, `get` e `paths`, sem expor SQL, tabelas gold, embeddings ou eventual backend futuro.
- Interface `DocumentIntelligence`: ingestão que devolve documento canônico, conteúdo estruturado e evidências; na v1 pode ser um adapter fino do pipeline atual.
- Interface `Pathways`: `propose`, `select` e `reassess`, com separação entre fatos, inferências, lacunas e recomendação.
- Interface `GroundedWriting`: `open(caminho, tipo_artefato)`, `turn`, `review`, com contexto de projeto, requisitos e RAG.
- Estados de revisão humana e temporalidade: `unknown`/`needs_review` nunca vira inelegibilidade ou validade ativa por inferência.

### Fluxos legados a retirar durante a migração

- `POST /explore`/`/explore/stream` como contrato de produto final, com `ExploreRequest` contendo histórico e perfil transitórios.
- Consultas e rotas `factual → reasoning → agent` de Explore como autoridade de jornada; elas podem sobreviver apenas dentro do adapter `Knowledge` enquanto necessário.
- Match v3 em estágios exposto como Radar principal e `domain_paths` como resultado efêmero de card.
- `planning_node`/`/planning/*` como ponte separada entre Explore e Writing.
- Entrada `POST /writing/start` por `edital_id` escolhida diretamente em card, além do salto `/workspace/new?mode=writing` sem caminho.
- `writing_sessions.kind=frontdoor` e `session_turns` como transcript concorrente, quando o novo estado de conversa do `ConsultantGraph` assumir a continuidade.
- Classificações/recomendações de crédito, investidores e bolsas acadêmicas nas superfícies ativas. Discovery e regras determinísticas continuam determinísticos.

## Ordem das tarefas

| ID | Fatia vertical | Valor ao terminar |
|---|---|---|
| `SCV1-T01` | Walking skeleton do consultor | Uma conversa autenticada percorre brief mínimo, confirmação, um caminho real e próximo passo, com o estado novo observável. |
| `SCV1-T02` | Brief e projeto revisáveis | A intenção deixa de ser perfil improvisado: o usuário revisa o brief e confirma um `ProjetoInovacao` persistente. |
| `SCV1-T03` | Caminho normativo | Financiamento público não reembolsável é explicado por regras, prazo, evidências, lacunas e possível ICT, sem falsa certeza. |
| `SCV1-T04` | Caminho aberto | Desafio corporativo/inovação aberta pode ser descoberto em fontes web mesmo sem edital formal e produzir ação de mercado. |
| `SCV1-T05` | Escolha, continuidade e memória | O usuário compara, escolhe, retoma e reavalia um caminho sem perder decisões nem misturar memória com fatos. |
| `SCV1-T06` | Execução e escrita fundamentada | O caminho escolhido abre um artefato de escrita com requisitos, evidências, materiais autorizados e RAG. |
| `SCV1-T07` | Promoção e retirada do legado | As duas verticais estão ponta a ponta na superfície única e os fluxos concorrentes são removidos. |

Cada arquivo define uma conversa de implementação focada. Uma tarefa só começa quando suas dependências têm aceite; durante a transição pode existir um adapter reversível, mas não uma segunda jornada de produto permanente.

### Por que cada corte é independente

- `SCV1-T01` termina com um contrato mínimo, uma sessão e um caminho real; pode ser demonstrado sem esperar a ontologia completa.
- `SCV1-T02` só amplia a entrada humana e a persistência de projeto; seus critérios são verificáveis sem depender de uma vertical específica.
- `SCV1-T03` é um adapter normativo fechado por regras, temporalidade e evidência; pode ser validado com fixtures gold mesmo enquanto a vertical aberta ainda não existe.
- `SCV1-T04` usa o mesmo projeto e contratos, mas troca apenas a fonte/objetivo do adapter; pode ser demonstrado com um caso web e gate humano próprio.
- `SCV1-T05` entrega uma decisão persistente sobre caminhos já disponíveis; seu smoke não precisa de um novo tipo de oportunidade.
- `SCV1-T06` consome um caminho selecionado e pode ser validado com um único artefato normativo; não bloqueia a existência do caminho aberto.
- `SCV1-T07` só promove o que já foi aceito e remove consumidores comprovadamente substituídos; não introduz comportamento de negócio novo.

## Capacidade reaproveitada e papel na jornada

| Capacidade atual | Reuso planejado |
|---|---|
| Perfil e diffs humanos | Fonte factual do contexto; propostas continuam sujeitas a aceite. |
| LangGraph/runtime/checkpointer/streaming | Implementação do `ConsultantGraph`, com um estado e um produtor de eventos. |
| Gold relacional, temporalidade, proveniência e relações | Adapter `Knowledge`; nenhum contrato novo depende do SQL. |
| Match v3, elegibilidade e `company_chunks` | Implementação interna de recuperação e regras em `Pathways`, não superfície do produto. |
| `domain_paths` | Sementes determinísticas de tipo/requisitos/próximo passo, enriquecidas e persistidas pelo novo contrato. |
| Discovery, pesquisa web, source docs e staging | `DocumentIntelligence` para a vertical aberta, mantendo revisão humana. |
| Chunker, contextual retrieval, embeddings, biblioteca, critic e checklist | Implementação de `GroundedWriting` e validação de evidência. |
| Transcript, reflection e memory store | Continuidade/decisões/insights com escopo; nunca como autoridade de fato. |
| Harness de evals e testes existentes | Casos focais e smokes das duas verticais; sem harness paralelo. |

## Fluxos legados que desaparecem

A retirada ocorre somente após o substituto correspondente passar o smoke da tarefa. Ao final, desaparecem como entradas ativas: Explore com `history` transitório; rotas factual/reasoning/agent; Radar/Match por estágios e poll de veredito; `planning_node` e `/planning/*`; escrita iniciada por `edital_id`; transcript frontdoor concorrente; e recomendações de crédito, investidores e bolsas acadêmicas. Dados antigos podem permanecer read-only durante uma migração curta, mas não podem gerar novos objetos ou competir com o `ConsultantGraph`.

## Riscos de execução

- **Alto e bloqueante:** não haver uma decisão clara de confirmação do projeto ou de artefato inicial; sem isso a jornada fica ambígua e não deve ser implementada por inferência.
- **Alto, mas contornável por `needs_review`:** fontes normativas sem localizador ou com temporalidade conflitante; o caso não pode ser “consertado” por prompt e deve produzir lacuna/revisão.
- **Médio:** o caso aberto não ter fonte primária estável; usar fixture versionada ou escolher outro caso sem mudar o contrato aberto.
- **Médio:** divergência entre estado novo e sessões antigas; resolver com adapter read-only e remoção após os consumidores migrarem, sem dual-write permanente.
- **Baixo/não bloqueante:** performance ou backend futuro do KG; os módulos profundos preservam a seam `Knowledge` e podem usar o gold atual.

## Definição global de conclusão

A versão está concluída quando:

1. uma conversa forma e atualiza um `BriefProjeto` visível;
2. confirmação explícita cria um `ProjetoInovacao` persistente, versionado e ligado à empresa/perfil usado;
3. o `ConsultantGraph` propõe `CaminhoInovacao` rastreável, separando fato, inferência, lacuna, risco e recomendação;
4. caminho normativo e caminho aberto percorrem conversa, brief, projeto, propostas, escolha e execução;
5. a escolha é persistente e pode ser retomada/reavaliada;
6. desconhecido, conflito, ausência de prazo e ausência de edital formal são comunicados proporcionalmente;
7. a escrita abre a partir do caminho selecionado, usa RAG fundamentado e devolve crítica/lacunas;
8. crédito, investidores e bolsas puramente acadêmicas não aparecem nas superfícies ativas;
9. cada fluxo legado equivalente foi removido após o substituto funcional;
10. a solução é compreensível e operável por um desenvolvedor solo, sem Neo4j, Graph Builder, GraphRAG unificado, object storage ou plataforma de dados na rota crítica.

## Decisões ainda bloqueantes

Somente decisões de produto/contrato podem bloquear a execução:

- o conjunto mínimo de campos que o usuário precisa confirmar para materializar `ProjetoInovacao`;
- quais tipos de artefato a v1 realmente abre, começando por proposta técnica normativa;
- qual caso representativo de desafio aberto será usado para o smoke, preservando a generalidade do adapter web;
- política explícita para um caminho sem edital formal: próximo passo de mercado obrigatório, escrita opcional e nenhuma “elegibilidade” inventada.

Não são bloqueantes nesta rota: escolha de banco do KG, Neo4j, Graph Builder, topologia de chunks/embeddings, object storage, extração adaptativa completa, memória autônoma entre projetos e cobertura nacional.
