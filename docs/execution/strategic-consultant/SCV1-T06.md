# SCV1-T06 — Execução e escrita fundamentada pelo caminho

## 1. Objetivo e resultado perceptível

Permitir que um caminho selecionado leve a um artefato útil. Na vertical normativa, o usuário abre uma proposta técnica a partir do `CaminhoInovacao`, conversa com o redator, vê seções apoiadas por requisitos/evidências e recupera trechos documentais via RAG. O sistema mostra lacunas e crítica; não começa uma sessão sem contexto ou por um `edital_id` isolado.

## 2. Estado atual encontrado

- `WritingSession` é persistida em Postgres, usa `agent_graph`/tools, checkpointer, streaming, `retrieve_chunks`, biblioteca, playbooks, critic e `checklist_service`.
- `/writing/start` exige `edital_id` + perfil e opcionalmente plano; o construtor deriva modo/contexto pelo namespace do id e liga `application_log`.
- `planning_node` gera plano a partir de `question`/`analysis` e o salva como `__plan__` em `section_drafts`; `/planning/*` é a ponte atual.
- `retrieve_chunks` e `edital_chunks` atendem a edital, enquanto `source_card`, library e profile entram em blocos do redator; citações são expostas por chunks.
- Não existe `GroundedWriting` e não há entrada oficial para `CaminhoInovacao`, projeto, canal aberto ou artefato tipado.

## 3. Escopo funcional

- Criar `GroundedWriting.open(caminho, tipo_artefato)` como única entrada nova para escrita; carregar projeto, caminho, requisitos, fatos, evidências, perfil e materiais autorizados.
- Para edital normativo, usar RAG híbrido atual e contextual retrieval, preservando fonte/localização e indicando quando um trecho não sustenta uma afirmação.
- Gerar outline/artefato tipado por caminho, permitir turnos, edição, critic e checklist, e devolver `gaps` que podem disparar `reassess` no `ConsultantGraph`.
- Permitir próximo passo não textual para caminho aberto; se houver artefato compatível, abrir escrita com `formal_instrument=false` e fontes web claramente separadas.
- Compartilhar os três contratos com escrita; não permitir que WritingSession recrie projeto, elegibilidade ou caminho privado.
- Migrar a UI de workspace para receber contexto escolhido, exibir evidências/citações, decisões e lacunas junto do documento.

## 4. Contratos introduzidos ou alterados

- `GroundedWriting.open(path_ref, artifact_type)` retorna `writing_session_id`, snapshot de projeto/caminho, outline e requisitos.
- `GroundedWriting.turn(session_id, instruction)` retorna revisão do artefato, `evidence_refs`, `pending_questions`, trace e estado; `review` retorna crítica/coverage/gaps.
- `WritingContext` deve carregar `project_id`, `path_id`, `path_revision`, `profile_version`, `source_refs`, `retrieval_scope` e `allowed_materials`.
- `WritingSession` deixa `edital_id` opcional quando o contrato permitir, mas não pode transformar ausência de edital em fato normativo ou em deadline inventado.
- `CaminhoInovacao` registra artefatos possíveis, artefato em andamento e lacunas descobertas pela escrita; o caminho pode ser reavaliado.

## 5. Módulos provavelmente afetados

- `writing_session.py`, `agent_graph.py`, `writing_tools.py`, `retriever.py`, `chunker.py`, `contextual_retrieval.py`, `checklist_service.py`, playbooks e biblioteca.
- Novo adapter `GroundedWriting`, `ConsultantGraph`, contratos de caminho e router de execução.
- `routers/writing.py`, `planning.py`, `workspace.py`, `applications.py` e frontend `workspace/[sessionId]`, planning e Projects.
- Avaliações `writing`, `writing_v2`, `rag`, `provenance`, `e2e_health` e testes de WritingSession/RAG.

## 6. Dependências

- `SCV1-T05` com caminho selecionado e lifecycle persistente.
- RAG/documentos normativos gold com pelo menos um caso em que a citação possa ser verificada.
- Decisão de artefato inicial: proposta técnica normativa; pitch genérico e artefatos abertos ficam limitados ao necessário para o smoke.

## 7. Passos de implementação em ordem

1. Definir `WritingContext` e o mapeamento de requisitos/evidências do caminho para o outline; fazer uma sessão antiga continuar carregável.
2. Criar `GroundedWriting` sobre `WritingSession`, escondendo detalhes de DB, chunks, playbook e checkpointer atrás de `open/turn/review`.
3. Fazer o adapter resolver RAG pelo `path_ref`/documentos e biblioteca autorizada; manter KG como estrutura/fato e RAG como texto/evidência.
4. Ligar o `ConsultantGraph` ao open/turn/review e ao retorno de lacunas/reavaliação.
5. Trocar o workspace para abrir a partir de caminho selecionado e exibir contexto, citations, critic e próximo passo.
6. Validar a vertical normativa com proposta parcial, revisão e retomada; adicionar o ramo aberto somente como ação compatível e explicitamente não normativa.
7. Desligar o pipeline `planning_node`/`__plan__` como entrada principal e migrar o plano para `WritingContext`/artefato.

## 8. Comportamento legado a remover

- Remover `POST /writing/start` por `edital_id` como entrada de produto e os cliques de `/radar` que o chamam diretamente.
- Remover `/planning/generate`, `/planning/{session_id}` e `/planning/{session_id}/adjust` como jornada concorrente; seus elementos úteis entram no `GroundedWriting`.
- Remover a exigência artificial de `CompanyProfile._WRITING_MIN_FIELDS` como único gate de escrita; o gate passa a ser contexto suficiente do caminho, com lacunas perguntáveis.
- Remover a derivação de modo/contexto exclusivamente pelo namespace do id e qualquer fallback que trate um desafio/canal como edital.
- Não duplicar RAG, critic ou checklist numa segunda implementação; os existentes ficam atrás do adapter profundo.

## 9. Critérios de aceite verificáveis

- Um caminho normativo selecionado abre sessão sem receber somente `edital_id`; o documento identifica projeto, caminho, versão, requisitos e fontes.
- Um turno de escrita retorna conteúdo e/ou pergunta, evidências/citações e lacunas; o critic/checklist consegue apontar requisito não coberto.
- O RAG recupera trecho real do documento e a UI permite inspecionar a fonte; texto não sustentado é marcado como lacuna/inferência.
- Retomar a sessão preserva artefato, caminho e decisões; editar uma seção não perde o snapshot de contexto.
- Se a escrita revelar informação incompatível, o caminho fica `reassess_needed` e o consultor conduz a decisão.
- O caminho aberto nunca gera deadline/elegibilidade fictícios; seu próximo passo pode ser uma ação de mercado.

## 10. Validação proporcional

- Testes focais de `WritingContext`, open por caminho, escopo de RAG, citation refs, gap→reassess e compatibilidade de sessão.
- Smoke normativo conversa → projeto → caminho → escrita → citação → review → reload; smoke aberto apenas até artefato/ação suportada.
- Rodar casos focalizados de `writing`, `writing_v2`, `rag`, `provenance` e testes de `writing_session_agent`/retriever.
- `ruff check`, `npx tsc --noEmit`, `git diff --check`, e verificar que o diff não cria novo harness nem migração de storage fora do contrato.

## 11. Fora de escopo

- Submissão automática, geração perfeita, editor colaborativo, PDF final ou object storage novo.
- Extração adaptativa completa, GraphRAG, Neo4j, novos playbooks para todos os mecanismos.
- Reescrever toda a WritingSession internamente; a tarefa deve aprofundar a interface e reaproveitar a implementação confiável.
