# SCV1-T05 — Escolha, continuidade e memória do consultor

## 1. Objetivo e resultado perceptível

Transformar uma lista de hipóteses em decisão acompanhada. O usuário compara caminhos, seleciona um, registra por que escolheu, retoma a conversa depois e pede reavaliação quando muda o projeto, o perfil ou a fonte. O consultor recupera contexto relevante sem confundir memória de trabalho/episódica com fatos do KG.

## 2. Estado atual encontrado

- O checkpointer do LangGraph é durável para Writing e loop-local para Explore streaming; o Explore síncrono continua stateless e as duas implementações duplicam o wiring.
- Transcript frontdoor é persistido em `writing_sessions/session_turns`; `GET /conversations` e `ConversationSidebar` permitem retomar mensagens, diffs e cards, mas não decisões de caminho.
- `reflection_service` guarda insights workspace-scoped e `agent_graph.memory_store` fornece busca semântica best-effort; auto-write está congelado por flag e memória é injetada principalmente na escrita/Explore.
- `application_log` registra status por edital e sessões, não seleção/reavaliação de um `CaminhoInovacao`; `domain_paths` não tem lifecycle.

## 3. Escopo funcional

- Implementar lifecycle de caminho: `proposed`, `investigating`, `selected`, `reassess_needed`, `discarded`, `completed` (nomes finais conforme contrato), decisão do usuário e última avaliação.
- Permitir comparar caminhos por fatos, requisitos, lacunas, riscos, confiança, temporalidade, próximo passo e custo/força da evidência, sem score universal.
- Fazer `ConsultantGraph` ser a única autoridade de seleção, atualização de estado e roteamento para nova pesquisa/Pathways/Writing.
- Persistir resumo da conversa, decisões e referências; usar checkpointer para contexto de trabalho e store episódico para projeto, não para fatos do ecossistema.
- Reutilizar memória autorizada por workspace para preferências/decisões anteriores, com escopo de projeto e consentimento; memória não altera perfil, caminho ou KG sem ação explícita.
- Unificar sync/stream e retomada pelo novo grafo; conversas antigas ficam read-only/adaptadas até sua substituição.

## 4. Contratos introduzidos ou alterados

- `CaminhoInovacao` recebe `decision {kind, reason, decided_at, actor}`, `state_history`, `reassessment_reason`, `context_revision` e `project_id` obrigatório.
- `ConsultantState` define uma única referência de trabalho para brief/projeto/caminhos/seleção/pending action; cada transição é versionada e idempotente.
- `MemoryContext` distingue `working`, `episodic`, `semantic` e `procedural`, com `scope=workspace|project`, origem, confiança e permissão de leitura.
- `Pathways.select(path_id, decision)` e `reassess(path_id, new_context)` retornam novo estado e explicação das mudanças; nenhuma operação grava fato de Knowledge.
- API de retomada deve devolver o mesmo estado lógico em qualquer transporte e expor eventos/decisões, não só transcript textual.

## 5. Módulos provavelmente afetados

- `ConsultantGraph`/store de estado, `agent_graph.py`, `agent_runtime.py`, `reflection_service.py`, `memory_store` e `writing_session.py`.
- `conversations.py`, `content_library.py`, `application_log` apenas onde uma referência nova for necessária; não usar application_log como store universal.
- `Pathways`, contratos de caminho, novo router de seleção/reavaliação e frontend de comparação, histórico e projetos.
- Testes de lifecycle, RLS/contaminação, checkpointer, memória e integração de retomada.

## 6. Dependências

- `SCV1-T03` e `SCV1-T04` aceitos com caminhos ricos e persistentes.
- Uma política de escopo de memória por workspace/projeto (decisão de segurança, não detalhe de implementação).
- Persistência e eventos do walking skeleton funcionando.

## 7. Passos de implementação em ordem

1. Fechar estados/transições e invariantes de seleção única, descarte, reavaliação e ownership.
2. Persistir decisão e histórico do caminho; adicionar endpoints/ações de comparar, selecionar e reavaliar no `ConsultantGraph`.
3. Consolidar um único produtor de turno sobre LangGraph para sync/stream, com checkpointer e estado terminal comuns.
4. Migrar o transcript novo para a sessão do consultor e criar adapter de leitura para `frontdoor`/writing antigos.
5. Integrar memória de trabalho/episódica e insights curados com escopo explícito; bloquear auto-write de fatos e evitar cross-project leakage.
6. Atualizar UI de Projetos/sidebar para mostrar projetos, caminhos selecionados, decisões pendentes e retomada.
7. Rodar smokes das duas verticais, incluindo alterar perfil/projeto e observar `reassess_needed` em vez de mutação silenciosa.

## 8. Comportamento legado a remover

- Remover as duas cópias de setup do Explore (`_explore_agent` e `explore_stream`) como fluxos de produto concorrentes; o novo grafo tem um caminho.
- Remover a dependência da lista de `session_turns`/`history` como memória de decisão e do `thread_id` somente do streaming.
- Remover `application_log` como proxy de “selecionado” quando o caminho poder ser ICT, canal aberto ou comparação sem candidatura.
- Remover memória automática de perfil/Knowledge; `reflection_service` continua para insights autorizados, mas não conduz seleção nem publica fatos.
- Remover filtros/botões de comparação que apenas operam cards efêmeros do Radar.

## 9. Critérios de aceite verificáveis

- O usuário compara ao menos um caminho normativo e um aberto usando o mesmo contrato e vê diferenças de evidência/lacuna/próximo passo.
- Selecionar um caminho registra usuário/data/motivo e altera o estado central; abrir novamente mantém a seleção.
- Pedir reavaliação após nova informação produz nova revisão e sinaliza o que mudou; não altera o histórico anterior.
- Sync e stream devolvem o mesmo estado lógico e não duplicam mensagens/decisões em retry.
- Memória de um projeto não aparece em outro workspace/projeto sem escopo autorizado; uma lembrança nunca é exibida como fato de fonte.
- O consultor retoma diretamente no ponto “escolha”, “investigação” ou “próximo passo”, sem repetir o onboarding inteiro.

## 10. Validação proporcional

- Testes focais das transições, optimistic revision/idempotência, isolamento por workspace/projeto e separação memória↔fato.
- Integração com checkpointer/store e smoke de fechar/reabrir a sessão em sync e streaming.
- Reusar `test_agent_graph_*`, `test_memory_store`, integrações de thread e smokes das duas verticais; não exigir backfill de todos os transcripts.
- `ruff check`, `npx tsc --noEmit`, `git diff --check` e inspeção do diff para evitar migração de estado fora da tarefa.

## 11. Fora de escopo

- Memória autônoma cross-workspace, aprendizado de pesos de Match, perfil multiempresa e recomendação universal.
- Fan-out de pesquisa, resumo/poda avançada além do necessário para continuidade, ou plataforma de observabilidade completa.
- Escrita fundamentada completa, extração adaptativa e mudança de backend KG.
