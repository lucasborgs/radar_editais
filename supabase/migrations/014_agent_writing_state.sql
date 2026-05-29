-- 014: estado de persistência para WritingSession agente (Sprint 2 do Cenário B).
--
-- 1. session_turns.tool_use  jsonb
--    Lista de {id, name, input, output} de cada tool executada no turn.
--    NULL  → turn produzido pelo pipeline legado (1-shot, sem tools).
--    []    → turn do agente que decidiu não usar nenhuma tool.
--    [...] → turn do agente com 1+ chamadas de tool.
--    Usado para: eval/shadow log, debug, futura observabilidade no produto.
--
-- 2. writing_sessions.pending_user_input  jsonb
--    {field: str, prompt: str} quando o agente chamou request_user_info e
--    está aguardando o usuário responder. Limpado no próximo turn (a
--    resposta do usuário consome a pergunta). Persistir aqui (não só no
--    response) garante que fechar aba e voltar mantém a UX coerente.

alter table public.session_turns
  add column if not exists tool_use jsonb;

comment on column public.session_turns.tool_use is
  'Tool calls do agente neste turn: [{id, name, input, output}]. '
  'NULL quando o turn veio do pipeline legado (sem agent_runtime).';

alter table public.writing_sessions
  add column if not exists pending_user_input jsonb;

comment on column public.writing_sessions.pending_user_input is
  '{field, prompt} quando o agente pediu info ao usuário e aguarda resposta. '
  'Limpado quando o próximo turn do usuário chega.';
