# SCV1-T01 — Walking skeleton do ConsultantGraph

## 1. Objetivo e resultado perceptível

Colocar no ar a primeira jornada real e curta do consultor: o usuário autenticado descreve uma intenção, vê um brief mínimo, confirma que quer trabalhar nela, recebe pelo menos um `CaminhoInovacao` proveniente do catálogo gold atual e vê um próximo passo. O resultado é observável na home e pode ser retomado pela lista de conversas/projetos.

Esta é uma fundação vertical deliberadamente pequena: um único fluxo de sucesso, um estado persistente e adapters finos para capacidades já existentes. Não é a infraestrutura definitiva de todos os caminhos.

## 2. Estado atual encontrado

- `/explore` e `/explore/stream` recebem mensagem, histórico, perfil e ids transitórios; `ExploreAgent` chama tools gold/Match e `_post_process` persiste `frontdoor` em `writing_sessions/session_turns`.
- `agent_graph.py` já executa ReAct em LangGraph; o runtime tem trace, streaming, limites e checkpointer, mas não possui estado de consultoria (`brief`, `projeto`, `caminhos`, `seleção`).
- `CompanyProfile` é o único contexto estruturado durável; não há `BriefProjeto`, `ProjetoInovacao`, `CaminhoInovacao` nem `ConsultantGraph` no domínio.
- `domain_paths.build_path` produz um dict mínimo a partir de uma entidade do Match, mas o resultado é efêmero e não é uma hipótese persistente.
- `GET /conversations` unifica transcript de frontdoor e writing, enquanto `/projects` filtra sessões de escrita; nenhuma tela mostra o estado novo.

## 3. Escopo funcional

- Definir o estado mínimo do `ConsultantGraph`: workspace/perfil usado, mensagens, brief, projeto opcional, caminhos propostos, caminho selecionado opcional, lacunas, próximo passo e versão do estado.
- Criar os três contratos centrais em forma mínima, já compatível com evolução: identidade, estado, timestamps, origem, referências de evidência e `confidence`/`needs_review`.
- Introduzir um adapter `Knowledge` que use `entity_catalog` para buscar uma oportunidade/entidade real e um adapter `Pathways` que use `domain_paths` + Match v3 apenas internamente.
- Introduzir o primeiro grafo LangGraph do consultor, com nós de interpretação, chamada de capacidades, atualização determinística do estado e pausa/retorno para confirmação humana.
- Expor um endpoint de turno do consultor e um endpoint de leitura do estado da jornada; a home deve renderizar mensagens, brief mínimo, ação de confirmar e card de caminho/próximo passo.
- Persistir um estado por workspace/sessão com RLS e idempotência suficiente para retry do turno; manter a conversa e os objetos referenciados no store adequado.

## 4. Contratos introduzidos ou alterados

- `BriefProjeto` mínimo: `id`, `status=draft|confirmed`, intenção original, hipótese de problema/solução, dúvidas, origem, versão e `updated_at`.
- `ProjetoInovacao` mínimo: `id`, `status=confirmed`, `workspace_id`, `empresa_id`/perfil snapshot ou versão, `brief_id`, decisões, caminhos associados e versão.
- `CaminhoInovacao` mínimo: `id`, `status=proposed|selected|discarded`, tipo, projeto, referência da entidade/canal, fatos, inferências, lacunas, recomendação e próximo passo.
- `ConsultantState` com `conversation_id`, `brief_id`, `project_id`, `path_ids`, `selected_path_id`, `pending_confirmation` e `revision`.
- `Knowledge.search/get` e `Pathways.propose/select/reassess` como seams internos; nenhum router conhece tabela gold ou `match_v3`.
- Novo payload de turno deve devolver estado resumido e eventos observáveis (`brief_updated`, `confirmation_required`, `paths_proposed`, `next_step`), além do texto.

## 5. Módulos provavelmente afetados

- Backend: novo módulo de domínio/serviço do `ConsultantGraph`, `src/radar/core/llm/agent_graph.py`, `agent_runtime.py`, `entity_catalog.py`, `domain_paths.py`, `match_v3.py`, `src/radar/api/app.py` e novo router de consultoria.
- Persistência: migration nova para sessão/estado/objetos ou extensão controlada de `writing_sessions`; RLS, índices e idempotência.
- Frontend: `frontend/src/app/page.tsx`, `frontend/src/lib/api.ts`, `frontend/src/types/api.ts`, `frontend/src/types/frontdoor.ts`, componentes de chat/card e sidebar.
- Testes: contratos do estado, adapter fake de Knowledge/Pathways, grafo com confirmação e smoke autenticado da home.

## 6. Dependências

- Nenhuma tarefa SCV1 anterior.
- `AGENTS.md`, schema de domínio e migrations gold existentes.
- Um caso real vigente do catálogo gold com evidência suficiente para o smoke.
- O runtime LangGraph e o workspace autenticado já existentes.

## 7. Passos de implementação em ordem

1. Mapear os invariantes mínimos e criar os tipos/serialização dos três contratos e do estado, sem duplicar `CompanyProfile`.
2. Criar a seam de `Knowledge` e um adapter relacional sobre `entity_catalog`; criar a seam de `Pathways` e um adapter que converta uma entidade real no novo caminho.
3. Persistir sessão, revisão do estado e objetos mínimos com isolamento por workspace; definir reexecução idempotente do turno.
4. Montar o `ConsultantGraph` em LangGraph: entender intenção → atualizar brief → pedir confirmação → materializar projeto → propor caminho → preparar próximo passo.
5. Ligar o endpoint e o streaming a esse grafo, usando um único produtor de eventos e um único estado terminal.
6. Trocar a home para renderizar o novo estado e fazer o primeiro smoke conversa → confirmação → caminho → próximo passo; manter a cópia anterior somente como fallback técnico curto.

## 8. Comportamento legado a remover

- O novo caminho não deve aceitar `history` e `profile` como autoridade: eles viram entrada de compatibilidade que é normalizada no estado do consultor.
- Remover a responsabilidade de `_post_process` de decidir `next_action` com `is_complex_proposal`; a progressão passa ao `ConsultantGraph`.
- Remover a criação de um `frontdoor` concorrente para cada turno novo; a sessão do consultor torna-se a autoridade, com adaptação temporária para leitura de conversas antigas.
- Não levar `matched_editais`/`matched_entities` como estado paralelo da home; o caminho persistente passa a ser a representação de produto.

## 9. Critérios de aceite verificáveis

- Uma pessoa autenticada envia uma intenção e recebe resposta com `brief_id` e brief visível.
- A interface apresenta uma confirmação explícita; sem confirmação não há `ProjetoInovacao` definitivo.
- Após confirmar, o estado contém exatamente um projeto persistente ligado ao workspace e pelo menos um caminho real com `id`, tipo, evidência, lacuna e próximo passo.
- Recarregar a página e abrir a conversa preserva brief, projeto, caminho e etapa atual.
- Um retry com a mesma chave não cria duas sessões nem duplica a proposta de caminho.
- O smoke usa `Knowledge`/`Pathways` pelos contratos; nenhum teste de router precisa conhecer SQL gold.
- Um erro de LLM ou de catálogo aparece como estado de revisão/erro recuperável, sem criar projeto falso.

## 10. Validação proporcional

- Testes focais dos schemas, transições de estado, confirmação humana, idempotência e adapters com doubles.
- Smoke manual ou de integração da home autenticada até um caminho real persistido.
- `ruff check` nos módulos alterados e `npx tsc --noEmit` no frontend alterado.
- Verificar `git diff --check`, RLS/ownership e que o diff contém apenas a fatia e seus contratos; não exigir a suíte completa.

## 11. Fora de escopo

- Ranking completo, reavaliação sofisticada, duas verticais completas, escrita, memória semântica ou extração adaptativa.
- Neo4j, Graph Builder, GraphRAG, object storage, ingestão nova e plataforma de dados.
- Migração de todas as conversas antigas; apenas leitura/adaptação mínima necessária para o walking skeleton.
- Regras novas de elegibilidade ou agentes para Discovery.
