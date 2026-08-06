# Plano de correção — Workspace, Ecossistema e contratos de sessão

## Objetivo

Corrigir rapidamente as inconsistências observadas no uso local/produção:

- Ecossistema exibindo métricas e tabela em estados diferentes durante o primeiro carregamento;
- títulos de projetos alternando entre nome legível e ID cru (`finep:777`);
- Workspace exibindo “plano carregado” quando a página de Plano não encontra o plano persistido;
- fluxo de escrita retornando apenas “Erro interno no servidor”;
- modo `/explorer` da Workspace prometendo uma capacidade que não é o `ExploreAgent` global.

Não implementar nesta rodada o consultor estratégico cross-opportunity do KG. Essa é uma evolução futura do Explorar global.

## Diretriz de produto

### Workspace

A Workspace deve ser um ambiente de execução de uma proposta/pitch:

- redação e refinamento;
- dúvidas factuais sobre o edital ou alvo selecionado;
- RAG e tools de escrita contextualizadas à sessão.

Ela não deve se apresentar como o ExplorerAgent do ecossistema.

### Explorar global

O Explorar global continua sendo o lugar para:

- descobrir oportunidades, ICTs, agências, programas e investidores;
- consultar o KG;
- futuramente construir arranjos estratégicos, por exemplo: edital exige ICT → identificar ICTs candidatas → explicar o caminho.

Não criar agora uma implementação parcial dessa consultoria.

## Ordem de implementação

Implementar na ordem abaixo. Cada bloco deve ser pequeno e independente.

---

## Bloco 1 — Remover a ambiguidade `/explorer` da Workspace

### Arquivos prováveis

- `frontend/src/app/workspace/[sessionId]/page.tsx`
- `frontend/src/components/workspace/WorkspaceHeader.tsx`
- `frontend/src/components/workspace/ModeBadge.tsx`
- `src/radar/api/routers/workspace.py`
- `src/radar/core/services/workspace_service.py`

### Alteração

1. Remover o modo `/explorer` da Workspace.
2. Remover o badge/troca de modo correspondente no header e no chat.
3. Remover o comando `/explorer` do parser da Workspace.
4. Remover o dispatch de `MODE_EXPLORER` dentro da Workspace.
5. Manter a Workspace no fluxo de escrita/contexto do alvo selecionado.
6. Preservar o Explorar global e suas rotas existentes sem alteração.
7. Não criar novo modo. Se for necessário manter a mensagem de boas-vindas, usar texto como “Contexto do edital carregado” — sem chamar isso de Explorer.

### Contrato esperado

- A Workspace não chama `ExploreAgent`.
- A navegação estratégica do ecossistema continua somente no Explorar global.
- Dúvidas sobre o edital selecionado continuam sendo tratadas pelo fluxo de escrita/RAG existente.

### Validação mínima

- confirmar que a Workspace abre;
- confirmar que uma mensagem normal chega ao fluxo de escrita;
- confirmar que o Explorar global continua registrado e acessível.

---

## Bloco 2 — Unificar o contrato do plano

### Problema

O Workspace exibe “Plano de proposta carregado” quando existem seções (`next.length > 0`). A página Plano consulta especificamente `section_drafts["__plan__"]`. São condições diferentes.

### Arquivos prováveis

- `frontend/src/app/workspace/[sessionId]/page.tsx`
- `frontend/src/app/workspace/planning/page.tsx`
- `frontend/src/components/workspace/WorkspaceHeader.tsx`
- `src/radar/api/routers/planning.py`
- `src/radar/core/services/writing_session.py`

### Alteração

1. No Workspace, exibir “Plano carregado” somente quando `doc.plan` for realmente um objeto válido.
2. Se houver outline/seções, mas não houver `doc.plan`, usar mensagem neutra: “Proposta carregada. Converse para continuar.”
3. Fazer `GET /planning/{session_id}` usar a mesma fonte de verdade do endpoint do documento, preferencialmente `get_session_document()` ou um helper compartilhado.
4. Garantir que o endpoint valide o `workspace_id` do usuário autenticado.
5. Preservar `section_drafts["__plan__"]` como armazenamento atual; não criar tabela nem novo formato.
6. Se `_save_plan()` falhar, registrar erro com `session_id`; não esconder silenciosamente a falha operacional.

### Contrato esperado

| Estado | Workspace | Página Plano |
|---|---|---|
| `plan` persistido | “Plano carregado” | carrega plano |
| somente outline/seções | “Proposta carregada” | informa plano inexistente |
| nenhum outline | mensagem inicial | informa ausência de plano |

### Validação mínima

- abrir sessão com plano;
- abrir sessão sem plano;
- clicar em Plano nos dois casos;
- confirmar que o texto exibido corresponde ao estado real.

---

## Bloco 3 — Corrigir carregamento do Ecossistema

### Arquivos prováveis

- `frontend/src/app/oportunidades/page.tsx`
- `frontend/src/lib/hooks.ts`
- `frontend/src/components/ui/DataTable.tsx`

### Problema

`/stats` e `/opportunities` são chamadas independentes. A página pode mostrar métricas prontas enquanto a tabela ainda não terminou de carregar, ou a tabela pronta enquanto as métricas ainda exibem `—`.

### Alteração

1. Manter chamadas independentes, mas representar corretamente o estado de cada uma.
2. Enquanto a lista não terminou, nunca exibir “nenhuma oportunidade”; exibir skeleton/loading.
3. Enquanto as métricas não terminaram, manter `—` somente nos cards.
4. Exibir erro da lista explicitamente, em vez de tratá-lo como lista vazia.
5. Não alterar o catálogo, filtros ou paginação.
6. Opcionalmente disparar `/stats` e `/opportunities` com `Promise.all`, desde que um erro em uma chamada não apague o resultado válido da outra.

### Observação

Não afirmar que os números são um snapshot transacional. A página apenas apresenta dois read models obtidos separadamente.

### Validação mínima

- simular atraso em `/stats`;
- simular atraso em `/opportunities`;
- simular erro em cada chamada;
- confirmar que não aparece falso vazio.

---

## Bloco 4 — Unificar resolução de títulos e diagnosticar `finep:777`

### Arquivos prováveis

- `src/radar/api/routers/conversations.py`
- `src/radar/api/routers/writing.py`
- `src/radar/core/kg/entity_catalog.py`
- `frontend/src/components/layout/ConversationSidebar.tsx`
- `frontend/src/app/projects/page.tsx`
- `frontend/src/app/workspace/[sessionId]/page.tsx`

### Alteração

1. Reutilizar no endpoint `/conversations` a mesma resolução de títulos usada por `/writing/sessions`.
2. Evitar o padrão sidebar → N chamadas individuais a `getEditalById()`.
3. Enquanto o título estiver pendente, mostrar “Carregando…” em vez de alternar imediatamente para o ID cru.
4. Preservar o ID em tooltip ou detalhe técnico, mas não como título principal quando houver nome disponível.
5. Não alterar IDs nem criar alias silencioso.

### Diagnóstico obrigatório de `finep:777`

Antes de qualquer correção de dados, verificar nos logs/banco:

- `writing_sessions.edital_id`;
- retorno de `entity_catalog.get_edital("finep:777")`;
- retorno de `entity_catalog.get_opportunity("finep:777")`;
- ambiente/banco consultado pelo Workspace e pelo Explorar;
- versão/IMAGE_TAG do app que respondeu cada chamada.

Se o ID não existir no catálogo atual, tratar como inconsistência de dado/ingestão. Não fazer fallback para outro edital e não inventar título.

### Validação mínima

- sidebar, Projetos e Workspace devem exibir o mesmo título para a mesma sessão;
- um ID inexistente deve produzir erro explícito e rastreável.

---

## Bloco 5 — Tornar o erro de escrita diagnosticável e proteger o fallback

### Arquivos prováveis

- `src/radar/api/routers/writing.py`
- `frontend/src/lib/api.ts`
- `frontend/src/app/workspace/[sessionId]/page.tsx`
- `src/radar/core/services/writing_session.py`

### Problema

O frontend tenta primeiro `/writing/turn/stream` e depois pode tentar `/writing/turn`. O usuário recebe apenas “Erro interno no servidor”, sem saber se falhou o stream, o agente, o RAG, a persistência ou o fallback.

### Alteração

1. No endpoint de stream, verificar a idempotency key antes de iniciar o produtor/thread.
2. Se já houver resposta armazenada, retornar somente o evento `done`.
3. Garantir que o fallback batch use a mesma idempotency key e não execute efeitos duplicados.
4. Adicionar `session_id`, `idempotency_key` truncada/anonimizada, etapa e exception type aos logs estruturados.
5. Não enviar stack trace ao usuário.
6. No frontend, distinguir pelo menos:
   - erro de conexão/stream;
   - erro do servidor;
   - erro de geração/persistência.
7. Preservar o retry manual existente.

### Não fazer

- não adicionar novo mecanismo de retry;
- não duplicar chamadas de LLM;
- não alterar o contrato de conteúdo do agente;
- não esconder erro retornando texto genérico como se fosse resposta válida.

### Validação mínima

- turno normal via stream;
- stream indisponível com fallback batch;
- mesma idempotency key repetida;
- erro controlado exibindo mensagem rastreável no log e mensagem segura na UI.

---

## Bloco 6 — Verificação final de integração

Executar somente validação de wiring, sem suíte exaustiva:

1. `ruff` nos arquivos alterados.
2. Imports dos módulos alterados.
3. Testes unitários diretamente afetados, se já existirem.
4. Smoke manual/API:
   - `/stats`;
   - `/opportunities`;
   - criação/abertura de sessão;
   - documento com plano;
   - documento sem plano;
   - troca para a página Plano;
   - um turno de escrita;
   - um caso de edital inexistente.
5. Conferir logs do `session_id` afetado.

Não executar reingestão, migração de schema, reindexação ou alteração do KG nesta rodada.

## Deploy

1. Commitar apenas os arquivos dos blocos implementados.
2. Usar o `IMAGE_TAG` do commit.
3. Executar o runbook de produção:

```text
scripts/compose.sh production build app worker
scripts/compose.sh production up -d
scripts/compose.sh production ps
scripts/compose.sh production logs app
```

4. Confirmar `/health`.
5. Repetir o smoke mínimo no ambiente de produção.
6. Registrar especificamente:
   - estado do Ecossistema na primeira abertura;
   - título do projeto/sidebar;
   - presença/ausência de `__plan__`;
   - resolução de `finep:777`;
   - etapa exata de eventual erro no turno de escrita.

## Critério de conclusão

O ciclo está concluído quando:

- a Workspace não se apresenta como ExplorerAgent;
- o status visual do plano corresponde ao plano persistido;
- o Ecossistema não mostra falso vazio durante carregamento;
- o mesmo projeto apresenta o mesmo título em todas as telas;
- `finep:777` é resolvido ou diagnosticado como ID ausente, sem fallback silencioso;
- erros de escrita são rastreáveis;
- nenhum comportamento estratégico novo do KG foi implementado prematuramente.

