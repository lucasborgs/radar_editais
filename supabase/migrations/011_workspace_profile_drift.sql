-- 011: detector de drift do CompanyProfile (Gap 4)
--
-- O perfil da empresa é o prefixo estático de toda WritingSession. Quando
-- ele fica obsoleto (empresa pivotou, evoluiu TRL, expandiu áreas), todas
-- as sessões novas herdam contexto errado silenciosamente.
--
-- `workspaces.updated_at` existe mas reflete qualquer mudança (consent,
-- preferences, etc.), não só do profile JSONB. Adicionamos
-- `profile_updated_at` específico via trigger pra detectar drift com
-- precisão (heurística simples — comparar com volume de items na library
-- criados depois desse timestamp).

alter table public.workspaces
  add column if not exists profile_updated_at timestamptz;

-- Backfill: rows existentes recebem o created_at como ponto de partida.
-- (Ou updated_at — escolhemos created_at pra que workspaces "antigos" com
-- profile já preenchido não fiquem instantaneamente "stale" só pelo
-- backfill. Se já tinha 90+ dias de criação, é razoável reavaliar.)
update public.workspaces
   set profile_updated_at = coalesce(created_at, now())
 where profile_updated_at is null;

alter table public.workspaces
  alter column profile_updated_at set default now(),
  alter column profile_updated_at set not null;

-- Trigger: bump profile_updated_at apenas quando o JSONB `profile` muda.
-- Distinct from set_updated_at (that one tracks ALL changes including
-- consent toggles).
create or replace function public.bump_profile_updated_at() returns trigger as $$
begin
  if new.profile is distinct from old.profile then
    new.profile_updated_at = now();
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists workspaces_profile_updated_at on public.workspaces;
create trigger workspaces_profile_updated_at
  before update on public.workspaces
  for each row execute function public.bump_profile_updated_at();

comment on column public.workspaces.profile_updated_at is
  'Timestamp da última mudança no JSONB `profile`. Usado pelo Gap 4 para detectar drift (perfil + workspaces.profile_updated_at antigo + N items novos na library = sinalizar "perfil pode estar desatualizado").';
