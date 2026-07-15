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
