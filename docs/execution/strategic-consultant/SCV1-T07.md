# SCV1-T07 — Promoção do fluxo único e retirada do legado

## 1. Objetivo e resultado perceptível

Promover o `ConsultantGraph` como a jornada ativa do Radar e encerrar a coexistência de produtos concorrentes. O usuário vê uma entrada única para explorar, formar projeto, avaliar caminhos, escolher e executar; as duas verticais obrigatórias têm smokes ponta a ponta; links e rotas antigas deixam de criar estados paralelos.

## 2. Estado atual encontrado

- A home conversa via `/explore`/`/explore/stream`, `/radar` chama `/radar/matches`, `/projects` lista WritingSessions e `/workspace` executa escrita.
- `ConversationSidebar` lista frontdoor/writing pela tabela física `writing_sessions`; `planning` e `workspace_mode` ainda expõem etapas/ações herdadas.
- Explore possui rotas factual/reasoning/agent e tools de Match; Radar expõe estágios e filtros; Writing aceita edital direto.
- Investidores já são filtrados em partes do catálogo, mas tipos crédito/bolsa existem em `domain_paths` e precisam ser retirados de todas as superfícies ativas.
- A spec mandatória proíbe coexistência indefinida e exige remoção após cada substituto funcional.

## 3. Escopo funcional

- Fazer a home, sidebar, Projetos, Radar e Workspace consumirem o estado/contratos do `ConsultantGraph` e navegarem por `brief → project → path → execution`.
- Manter `/radar` como superfície de apoio ou removê-lo se a experiência nova absorver sua função; em ambos os casos, não pode haver ranking/cards concorrentes que criem caminhos diferentes.
- Migrar conversas/objetos legados apenas no limite necessário para não quebrar leitura; novos turnos e novos projetos entram somente no fluxo novo.
- Remover endpoints, tipos, componentes e adapters de produto legados listados abaixo, depois de provar equivalência por smoke e aceitar a pequena janela de migração.
- Aplicar guardrails de escopo: não recomendar crédito, investidores ou bolsas acadêmicas; Discovery permanece staging/gate humano; regra rígida permanece determinística.
- Registrar no harness existente os casos normativo e aberto como avaliação de jornada, sem criar uma suíte paralela.

## 4. Contratos introduzidos ou alterados

- `ConsultantState` é o payload canônico de jornada e a única origem de `brief`, `project`, `paths`, `selected_path`, `next_step` e `execution`.
- API de compatibilidade pode devolver redirect/erro orientativo para endpoints retirados, mas não deve criar objeto legado novo.
- Frontend elimina tipos de resposta específicos quando só duplicam os contratos centrais; cards de fato/inferência/lacuna usam `CaminhoInovacao`.
- Manifesto de conclusão da jornada deve registrar versão dos contratos, casos de smoke e fluxos legados removidos; não é plataforma de release de dados.

## 5. Módulos provavelmente afetados

- `src/radar/api/app.py` e routers `explore.py`, `radar.py`, `planning.py`, `writing.py`, `workspace.py`, `conversations.py`.
- `ConsultantGraph`, adapters Knowledge/Pathways/GroundedWriting, `domain_paths.py`, `match_v3.py`, `agent_tools` e `writing_session.py`.
- Home, `/radar`, `/projects`, `/workspace/planning`, `/workspace/[sessionId]`, sidebar, tipos e `lib/api.ts`.
- Harness/fixtures de `e2e_health`, `explore`, `matching`, `rag`, `writing`, `opportunity_type` e smoke frontend.

## 6. Dependências

- `SCV1-T06` aceito e escrita aberta a partir do caminho.
- Smokes aprovados de `SCV1-T03` (normativo) e `SCV1-T04` (aberto), além da retomada de `SCV1-T05`.
- Decisão de navegação final entre home/Radar/Projetos/Workspace; não é necessário mudar o modelo visual inteiro.

## 7. Passos de implementação em ordem

1. Inventariar consumidores reais das rotas/tipos legados e marcar os que já foram substituídos; não apagar um caminho ainda usado sem adapter de leitura.
2. Fazer a navegação e os componentes usarem os contratos centrais, com estados explícitos de revisão/unknown e um CTA de próximo passo.
3. Desativar a criação por Explore direto, Match direto, Planning direto e Writing por edital em sequência, mantendo respostas de compatibilidade apenas durante a migração curta.
4. Retirar `domain_paths` e Match do papel de superfície; preservar apenas adapters internos que ainda tenham valor comprovado.
5. Retirar as rotas de consulta factual/reasoning/agent e o transcript `frontdoor` como contrato de produto, após o novo grafo assumir continuidade e retomada.
6. Remover crédito/bolsa/investidor de tipos, filtros, prompts, fixtures ativas e cards; conferir Discovery sem conversão em agente.
7. Adicionar ao harness os dois casos completos e executar smoke final: conversa → brief → projeto → caminhos → seleção → próximo passo/escrita.
8. Revisar o diff final, documentação de contratos e dead code residual; deixar apenas adapters de compatibilidade de leitura com prazo/condição de remoção explícitos.

## 8. Comportamento legado a remover

- `POST /explore`, `/explore/stream` e `ExploreRequest` como jornada ativa independente.
- Consultas/rotas factual, reasoning e agent do Explore como autoridade conversacional.
- `/radar/matches`, `find_matching_editais`, `find_matching_entities`, Stage 0–3 e poll de `match_verdicts` como produto de recomendação separado.
- `/planning/*`, `planning_context`, `planning_node` e CTA “estruturar proposta” que bypassa projeto/caminho.
- `POST /writing/start` por `edital_id`, `/workspace/new?mode=writing` e modo derivado por namespace como entrada de execução.
- `writing_sessions.kind=frontdoor`/transcript antigo para novos turnos; apenas dados antigos legíveis podem permanecer em adapter temporário.
- Toda superfície ativa que mencione ou recomende linhas de crédito, investidores privados ou bolsas puramente acadêmicas.

## 9. Critérios de aceite verificáveis

- Usuário novo conclui cada vertical com a mesma navegação e os mesmos contratos centrais; nenhum branch depende de fase antiga do Match.
- Usuário existente pode abrir dados antigos em modo de leitura ou recebe migração orientada, mas não cria um projeto novo por rota removida.
- Não há duas respostas diferentes para o mesmo turno por sync versus stream; não há dupla persistência de conversa, caminho ou decisão.
- O caminho selecionado abre execução/escrita somente pelo `GroundedWriting`; caminho aberto sem artefato fica com próximo passo de mercado.
- Casos de unknown/conflito/sem prazo são visíveis e não são promovidos por UI a elegibilidade/validade.
- Busca manual por crédito/investidor/bolsa não retorna recomendação em superfície ativa; o dado histórico pode permanecer fora da recomendação.
- `docs/execution/strategic-consultant/` e specs não são alterados pela implementação, salvo documentação operacional explicitamente fora desta tarefa.

## 10. Validação proporcional

- Testes focais de contratos centrais, filtros de escopo, redirects/erros dos endpoints removidos e ausência de imports/consumidores do fluxo legado.
- Smoke de browser das duas verticais e de retomada de projeto/escrita; verificar cards, evidências, seleção e próximo passo.
- Rodar avaliações registradas `e2e_health`, `opportunity_type`, `provenance`, `rag`/`writing` focalizadas nos casos representativos; sem exigir uma suíte nacional.
- `ruff check .`, `cd frontend && npx tsc --noEmit`, `git diff --check`, busca de referências legadas e inspeção de integridade do diff.

## 11. Fora de escopo

- Deploy, migração operacional completa de todos os dados, backfill nacional, object storage e plataforma de dados.
- Neo4j/Graph Builder/GraphRAG, extração adaptativa, memória cross-workspace e cobertura total do ecossistema.
- Redesign visual amplo, submissão automática, aconselhamento jurídico e novas verticais fora do escopo ativo.
