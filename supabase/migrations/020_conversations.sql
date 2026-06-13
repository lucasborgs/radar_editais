-- 020: generaliza writing_sessions/session_turns em "conversations" (spec
-- frontend chat-first, fase 2). O nome físico das tabelas NÃO muda — RLS,
-- código e dados existentes ficam intactos; `kind` distingue o sabor da conversa.
--
-- 1. writing_sessions.kind  text  ('writing' | 'frontdoor')
--    'writing'  → sessão de escrita de proposta (comportamento de hoje, default).
--    'frontdoor'→ conversa da porta de entrada (persistida só para usuário logado).
-- 2. writing_sessions.title text
--    Exibição no sidebar. frontdoor: 1ª mensagem do usuário truncada (60 chars).
--    writing: NULL (o front deriva do edital, como hoje).
-- 3. edital_id deixa de ser NOT NULL — frontdoor não tem edital. Uma constraint
--    condicional garante que escrita CONTINUA exigindo edital_id.
-- 4. session_turns.entry_kind/payload — persistem as entradas heterogêneas do
--    transcript do front door:
--      'msg'   → role+content como hoje.
--      'diff'  → proposta de diff de perfil; payload = {items, status, origin}.
--      'radar' → cards de radar inline; payload = JSON da entrada.
--    'gate' NÃO persiste (só existe para anônimo, que não tem persistência).

alter table public.writing_sessions
  add column if not exists kind  text not null default 'writing'
    check (kind in ('writing', 'frontdoor')),
  add column if not exists title text;

-- frontdoor não tem edital; relaxa o NOT NULL herdado da migration 004.
alter table public.writing_sessions alter column edital_id drop not null;

-- `add constraint` não aceita `if not exists` em Postgres — guardamos com um
-- bloco do $$ (estilo das migrations 004/017) para a migração ser idempotente.
-- A constraint preserva o invariante antigo: escrita (kind='writing') exige
-- edital_id; frontdoor pode tê-lo nulo.
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'writing_sessions_edital_required'
  ) then
    alter table public.writing_sessions
      add constraint writing_sessions_edital_required
      check (kind <> 'writing' or edital_id is not null);
  end if;
end$$;

alter table public.session_turns
  add column if not exists entry_kind text not null default 'msg'
    check (entry_kind in ('msg', 'diff', 'radar')),
  add column if not exists payload jsonb;

comment on column public.writing_sessions.kind is
  'Sabor da conversa: writing (escrita de proposta) | frontdoor (porta de entrada).';
comment on column public.writing_sessions.title is
  'Título de exibição no sidebar. frontdoor: 1ª mensagem truncada; writing: NULL (deriva do edital).';
comment on column public.session_turns.entry_kind is
  'Tipo da entrada do transcript: msg (role+content) | diff | radar (usam payload).';
comment on column public.session_turns.payload is
  'JSON da entrada para entry_kind diff|radar (inclui status do diff). NULL para msg.';
