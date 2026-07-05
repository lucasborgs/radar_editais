-- 035: cache de vereditos LLM do match (KG v2 PR7 / Estágio 2 do funil).
-- Spec: docs/specs/kg-redesign.md (§PR7).
--
-- O veredito (racional de afinidade + red flags + fit de mecanismo + recomendação)
-- é computado async pela task `compute_match_verdicts` e cacheado por par
-- (workspace, oportunidade). `input_hash` = sha256(subgrafo serializado + perfil +
-- paths do match + versão do prompt): perfil ou oportunidade mudou ⇒ hash muda ⇒
-- o leitor trata a linha como miss, a task recomputa e o upsert substitui (PK no
-- par — nunca acumula histórico). Custo por refresh do radar ≤ K chamadas tier 3.

create table if not exists public.match_verdicts (
    workspace_id    uuid        not null references public.workspaces(id) on delete cascade,
    oportunidade_id text        not null,  -- file_key do hipergrado (ex. finep__602)
    input_hash      text        not null,
    verdict         jsonb       not null,
    model           text        not null default '',
    updated_at      timestamptz not null default now(),
    primary key (workspace_id, oportunidade_id)
);

comment on table public.match_verdicts is
    'Cache do veredito LLM top-K do match (Estágio 2, KG v2 PR7) por (workspace, oportunidade).';

alter table public.match_verdicts enable row level security;

-- RLS: o dono do workspace lê os próprios vereditos (a escrita é via service-role
-- na task do worker, que bypassa RLS — mesma postura de company_hypergraphs/033).
create policy "match_verdicts_select_own" on public.match_verdicts
    for select using (
        workspace_id in (select id from public.workspaces where user_id = auth.uid())
    );
