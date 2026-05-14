# Radar de Editais

Radar de Editais matches Brazilian companies with public funding opportunities
(editais) using a medallion ETL pipeline, semantic search and LLM agents. See
[CLAUDE.md](CLAUDE.md) for the architecture overview and
[ADR-001-decisoes-iniciais.md](ADR-001-decisoes-iniciais.md) for the foundational
decisions, including M4 (local Supabase development).

## Local development

Production uses Supabase Cloud, but local development runs the same Postgres +
Auth + Storage stack through the Supabase CLI (Docker). Migrations in
`supabase/migrations/` are applied in both environments so schema stays in sync.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) running
- [Supabase CLI](https://supabase.com/docs/guides/cli) installed
  (e.g. `brew install supabase/tap/supabase` on macOS)

### Start the local stack

```bash
./scripts/dev.sh        # wraps `supabase start`
# or, equivalently:
supabase start
```

The first run downloads the Postgres, GoTrue, PostgREST, Studio, etc. images
and may take a few minutes.

### Wire up `.env`

After the stack is up, print the local credentials:

```bash
supabase status
```

Copy `.env.example` to `.env` and replace the Supabase values with the local
ones reported by `supabase status` (API URL, `anon` key, `service_role` key,
JWT secret).

### Apply migrations

`supabase start` automatically applies every file in `supabase/migrations/` on
first boot. To re-apply them on demand (e.g. after editing a migration or
pulling new ones):

```bash
supabase db reset
```

This drops the local database and replays the migration chain from scratch,
including `002_enable_extensions.sql` (pgvector + pgcrypto).

### Run the background worker

Background jobs (e.g. content enrichment after `POST /library`) are handled by
[procrastinate](https://procrastinate.readthedocs.io/) — see ADR-001 decision
M8. Run the worker in a separate terminal alongside the FastAPI server:

```bash
python -m procrastinate --app=core.tasks.app worker
```

The worker needs `DATABASE_URL` pointing at the same Postgres the API uses
(local Supabase exposes it on port 54322 — see `.env.example`). All other
service env vars (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENAI_API_KEY`,
etc.) must also be set, because tasks reuse the same core modules.

### Stop the stack

```bash
supabase stop
```
