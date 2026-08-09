# SCV1-T02 — Brief revisável e projeto confirmado

## 1. Objetivo e resultado perceptível

Fazer o consultor compreender uma intenção incompleta antes de recomendar. O usuário vê um `BriefProjeto` editável, responde somente às lacunas que podem mudar os caminhos e confirma explicitamente a criação de um `ProjetoInovacao`. O projeto passa a ser o contexto compartilhado, em vez de campos de “projeto” improvisados dentro do perfil.

## 2. Estado atual encontrado

- O Explore monta contexto textual parcial do perfil e `ProfileExtractor` devolve diffs aceitos pelo usuário; não existe brief persistente.
- `CompanyProfile` contém `one_liner`, `solution_summary`, `portfolio_projetos` e `trl`, e `domain_paths.has_project` interpreta qualquer um deles como projeto.
- `planning_node.is_complex_proposal` decide por heurística quando oferecer planejamento; `/planning/generate` recebe `question`, `analysis`, `edital_id` e `company_nodes` e salva plano em `section_drafts` de uma sessão de escrita.
- Perfil autenticado está em `workspaces.profile`; o frontend também mantém espelho local e transcript em `sessionStorage`.

## 3. Escopo funcional

- Ampliar `BriefProjeto` com problema/usuários afetados, hipótese de solução, tecnologias/capacidades, objetivo, estágio, localização/restrições, dúvidas de impacto e estado de revisão.
- Permitir editar o brief como proposta do consultor, aceitar/corrigir campos e registrar origem (`user`, `assistant`, `profile`) sem aplicar automaticamente mudanças no perfil.
- Definir a confirmação que materializa `ProjetoInovacao`, com vínculo à empresa e ao snapshot/versão de perfil utilizado.
- Fazer o `ConsultantGraph` perguntar de modo adaptativo e determinístico quais lacunas são relevantes para a decisão; desconhecido fica explícito.
- Fazer `Knowledge`/`Pathways` receberem o projeto confirmado, não uma fusão ad hoc de perfil + texto de conversa.
- Mostrar uma página/estado de projeto retomável em `/projects` ou na superfície da conversa, incluindo histórico de decisões.

## 4. Contratos introduzidos ou alterados

- Completar `BriefProjeto` e `ProjetoInovacao` com campos da spec, `revision`, `source_refs`, `profile_version`, `decision_history` e `review_state`.
- `ConsultantGraph.update_brief`, `confirm_project` e `revise_project` como operações de estado; confirmação exige intenção explícita e versão esperada.
- Perfil passa a ser referência factual reutilizada; o projeto não altera `CompanyProfile` silenciosamente. Um dado novo de perfil continua seguindo o fluxo de diff/aceite existente.
- `Pathways.propose(company, project)` e ferramentas do agente devem aceitar objetos tipados/serializados, mantendo `workspace_id` fora do prompt como autoridade de isolamento.
- API deve expor `GET/PATCH brief`, `POST project/confirm` ou equivalente sem deixar cada tela inventar sua própria forma de confirmação.

## 5. Módulos provavelmente afetados

- Contratos/serviços do `ConsultantGraph` criados em T01; `src/radar/domain/user_profile.py`, `profile_schema.py`, `src/radar/api/common.py`.
- `src/radar/core/services/explore_agent.py`, `explore_routing.py`, `profile_extractor.py`, `planning_node.py` e ferramentas de perfil/explore.
- Router de consultoria e persistência de projeto/brief; `conversations.py` somente como adapter de leitura durante a migração.
- Frontend: home, `DiffCard`, componentes de revisão, `projects/page.tsx`, tipos e cliente API.
- Testes de contratos, revisão/confirmacão, ownership e cenários de campo desconhecido.

## 6. Dependências

- `SCV1-T01` aceito.
- Decisão do conjunto mínimo de confirmação (bloqueante real listado no README).
- Perfil e workspace autenticados; nenhum novo extractor é necessário.

## 7. Passos de implementação em ordem

1. Fixar o vocabulário e o mapeamento entre campos atuais do perfil e campos do brief, marcando o que é factual, hipótese ou desconhecido.
2. Implementar revisão/versionamento do brief e materialização transacional do projeto com confirmação explícita.
3. Substituir a heurística `is_complex_proposal` por uma decisão do grafo: revisar brief, perguntar lacuna de alto impacto ou pedir confirmação.
4. Adaptar as tools de perfil para propor atualizações sem mutar perfil/projeto; manter `AI drafts, humans decide`.
5. Passar `ProjetoInovacao` ao adapter `Pathways` e garantir que a resposta devolva a revisão do brief/projeto junto do turno.
6. Atualizar home e Projetos para mostrar brief, projeto, revisão e caminho associado; smoke intenção → correção → confirmação → reload.
7. Desligar o uso do `planning_node` como transição de produto, preservando apenas uma função interna se ainda necessária para formar outline de uma sessão antiga.

## 8. Comportamento legado a remover

- Remover `planning_context` em `sessionStorage` como fonte de verdade e os botões que saltam de Explore para `/workspace/planning` com `question/analysis` soltos.
- Remover a interpretação de `portfolio_projetos`/`solution_summary` como único “projeto” em `domain_paths.has_project`; usar projeto confirmado ou estado explícito de intenção.
- Remover mutação automática do perfil a partir da conversa; somente diffs aceitos continuam podendo alterar o perfil.
- A tela `/projects` deixa de ser lista exclusiva de WritingSessions; sessões antigas podem ser exibidas como legado, mas não recebem novos projetos por esse caminho.

## 9. Critérios de aceite verificáveis

- O brief é legível, editável e mostra pelo menos uma origem por premissa relevante.
- Alterar uma premissa atualiza a revisão sem apagar histórico nem criar projeto.
- Só uma ação explícita de confirmação cria o projeto; recarregar mantém o mesmo id e revisão.
- O projeto registra empresa/perfil usado e decisões relevantes; um perfil posterior não reescreve o snapshot histórico.
- Uma lacuna desconhecida é exibida como pergunta/próximo passo, nunca como “não elegível”.
- O caminho gerado depois da confirmação referencia `project_id` e não apenas texto do turno/`edital_id`.
- O smoke não usa `/planning/generate` nem `planning_context`.

## 10. Validação proporcional

- Testes focais dos contratos, transições draft→confirmed, revisão concorrente, snapshot de perfil e regra unknown.
- Smoke de UI da edição do brief, confirmação, reload e abertura de Projetos.
- `ruff check`, `npx tsc --noEmit`, `git diff --check` e inspeção de migrations/RLS.
- Rodar as avaliações existentes de `profile_extractor` apenas nos casos tocados; não exigir toda a suíte.

## 11. Fora de escopo

- Propostas de caminhos normativos/abertos completas, seleção, memória semântica e escrita.
- Extração adaptativa de documentos, OCR, Neo4j ou nova ontologia além do núcleo dos três contratos.
- Perfil de múltiplas empresas no mesmo workspace, submissão automática e aconselhamento jurídico.
