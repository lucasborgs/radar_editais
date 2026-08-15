-- SCV1-T06: snapshot autorizado que ancora uma WritingSession em um caminho.
-- Sessões antigas permanecem válidas com o contexto vazio.
alter table public.writing_sessions
  add column if not exists writing_context jsonb not null default '{}'::jsonb,
  add column if not exists grounded_open_key text;

-- A mesma revisão de caminho abre exatamente uma sessão para cada artefato e
-- conjunto de materiais autorizados. A chave é determinística no serviço; o
-- índice é a garantia para retry/concurrency entre instâncias.
create unique index if not exists writing_sessions_grounded_open_key_idx
  on public.writing_sessions (workspace_id, grounded_open_key)
  where grounded_open_key is not null;

comment on column public.writing_sessions.writing_context is
  'Snapshot do WritingContext: projeto, caminho, requisitos, fontes, escopo RAG e materiais autorizados.';

comment on column public.writing_sessions.grounded_open_key is
  'Identidade determinística de abertura: conversa, caminho/revisão, artefato e materiais autorizados.';
