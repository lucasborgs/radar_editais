-- SCV1-T06: snapshot autorizado que ancora uma WritingSession em um caminho.
-- Sessões antigas permanecem válidas com o contexto vazio.
alter table public.writing_sessions
  add column if not exists writing_context jsonb not null default '{}'::jsonb;

comment on column public.writing_sessions.writing_context is
  'Snapshot do WritingContext: projeto, caminho, requisitos, fontes, escopo RAG e materiais autorizados.';
