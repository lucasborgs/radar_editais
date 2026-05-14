-- ============================================================
-- Migration 006 — Fase 3 #22: Supabase Storage para propostas geradas
--
-- Cria bucket privado `proposals` e RLS policies em storage.objects.
-- Convenção de path: <workspace_id>/<session_id>/<filename>.md
--
-- Tenant isolation: RLS confere que o primeiro segmento do path bate com
-- algum workspace.id do user. Mesma lógica das tabelas (ADR B5/M7).
-- ============================================================

insert into storage.buckets (id, name, public)
values ('proposals', 'proposals', false)
on conflict (id) do nothing;

-- SELECT (read): bucket privado, mas dono do workspace pode ler
drop policy if exists "proposals_select_own" on storage.objects;
create policy "proposals_select_own" on storage.objects
  for select to authenticated using (
    bucket_id = 'proposals'
    and (storage.foldername(name))[1] in (
      select id::text from public.workspaces where user_id = auth.uid()
    )
  );

-- INSERT (upload): só na pasta do próprio workspace
drop policy if exists "proposals_insert_own" on storage.objects;
create policy "proposals_insert_own" on storage.objects
  for insert to authenticated with check (
    bucket_id = 'proposals'
    and (storage.foldername(name))[1] in (
      select id::text from public.workspaces where user_id = auth.uid()
    )
  );

-- UPDATE (overwrite): mesma regra
drop policy if exists "proposals_update_own" on storage.objects;
create policy "proposals_update_own" on storage.objects
  for update to authenticated using (
    bucket_id = 'proposals'
    and (storage.foldername(name))[1] in (
      select id::text from public.workspaces where user_id = auth.uid()
    )
  );

-- DELETE: simétrico
drop policy if exists "proposals_delete_own" on storage.objects;
create policy "proposals_delete_own" on storage.objects
  for delete to authenticated using (
    bucket_id = 'proposals'
    and (storage.foldername(name))[1] in (
      select id::text from public.workspaces where user_id = auth.uid()
    )
  );
