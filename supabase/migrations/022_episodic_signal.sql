-- 022: reflection_insights.origin — proveniência do insight (Item 4, Sprint 2)
--
-- Até aqui, todo reflection_insight vinha de `reflect_workspace` (síntese de
-- outcomes de application_log). Item 4 adiciona uma SEGUNDA fonte: a compressão
-- episódica da sessão de escrita. Ao comprimir o histórico (>10 turnos) ou ao
-- fechar a sessão, um segundo prompt extrai sinal estruturado — seções que o
-- Critic rejeitou, afirmações que o usuário corrigiu, seções que fluíram sem
-- atrito — e materializa cada um como insight level 1.
--
-- Esta coluna distingue as duas origens:
--   origin = 'episodic_compression'  → extraído ao comprimir/fechar uma
--                                       WritingSession (heurístico, confidence low)
--   origin = NULL                    → veio da reflexão de outcomes (reflect_workspace)
--
-- Idempotente: `add column if not exists` — pode rodar mais de uma vez sem
-- efeito colateral.

alter table public.reflection_insights
  add column if not exists origin text;

comment on column public.reflection_insights.origin is
  'Proveniência do insight: ''episodic_compression'' = extraído ao comprimir/fechar uma sessão de escrita (Item 4); NULL = veio da reflexão de outcomes (reflect_workspace).';
