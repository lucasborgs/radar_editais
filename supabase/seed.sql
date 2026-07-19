insert into public.environment_metadata
    (id, environment, project_ref, schema_version, dataset_version)
values
    (true, 'local', 'local', '040', 'local-seed-v1')
on conflict (id) do update set
    environment = excluded.environment,
    project_ref = excluded.project_ref,
    schema_version = excluded.schema_version,
    dataset_version = excluded.dataset_version,
    updated_at = now();

-- ---------------------------------------------------------------------------
-- Identidade de EVAL local (fixture) — Item 3 / gate da escrita.
-- O harness de eval usa EVAL_WORKSPACE_ID=dca65d63... cujo workspace referencia
-- (FK) um user em auth.users. Recriamos AMBOS com os MESMOS UUIDs do cloud, mas
-- com dados DUMMY (zero PII: email fake, senha fake) — assim EVAL_WORKSPACE_ID
-- funciona local sem override e a fixture sobrevive a `supabase db reset`.
-- O corpus dos editais do golden vem à parte: scripts/seed_eval_corpus.py.
-- ---------------------------------------------------------------------------
insert into auth.users
    (instance_id, id, aud, role, email, encrypted_password,
     email_confirmed_at, created_at, updated_at,
     raw_app_meta_data, raw_user_meta_data)
values
    ('00000000-0000-0000-0000-000000000000',
     'aee5a3b3-b9b4-44a2-b793-7f41721fbaca',
     'authenticated', 'authenticated', 'eval@local.test',
     crypt('eval-local-fake-password', gen_salt('bf')),
     now(), now(), now(),
     '{"provider":"email","providers":["email"]}'::jsonb, '{}'::jsonb)
on conflict (id) do nothing;

insert into public.workspaces (id, user_id)
values
    ('dca65d63-a340-498f-92df-f2634316df32',
     'aee5a3b3-b9b4-44a2-b793-7f41721fbaca')
on conflict (id) do nothing;
