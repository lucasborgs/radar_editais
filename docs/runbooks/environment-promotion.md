# Runbook — ambientes e promoção

Este runbook materializa a promoção
`local → CI/test → pré-produção local → production` da
[spec de paridade e isolamento](../specs/environment-parity-isolation.md).

## Bootstrap local

```bash
cp envs/.env.local.example .env.local
supabase start
ENVIRONMENT=local DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
  SUPABASE_URL=http://127.0.0.1:54321 python scripts/supabase_safe.py reset
python scripts/env_doctor.py
pytest -q
```

`reset` apaga apenas o Supabase local e deve ser usado conscientemente. Para
preservar dados locais, use `supabase migration up` e inicialize a sentinela com
`scripts/set_environment_metadata.py`.

## Pré-produção local

Não é necessário criar outro projeto Supabase Cloud. Este perfil é descartável,
usa `ENVIRONMENT=test` e nunca aceita credenciais remotas.

```bash
supabase start
python scripts/bootstrap_staging_local.py
```

O bootstrap lê somente as chaves **locais** mostradas por `supabase status` e
gera um arquivo gitignored com permissão `0600`; nenhuma credencial Supabase
Cloud é copiada. O reset abaixo apaga dados locais, nunca os dados remotos:

```bash
ENV_FILE=.env.staging-local ENVIRONMENT=local \
  python scripts/supabase_safe.py reset
ENV_FILE=.env.staging-local \
  python scripts/set_environment_metadata.py --environment test --project-ref local
ENV_FILE=.env.staging-local python scripts/env_doctor.py
```

Prepare a memória e rode primeiro as integrações de banco, antes de iniciar o
worker (a suíte injeta jobs adversariais deliberadamente):

```bash
ENV_FILE=.env.staging-local python scripts/setup_checkpointer.py
set -a
source .env.staging-local
set +a
pytest -m integration -v
```

Suba então app e worker paralelos ao stack publicado. Eles usam porta, nomes e
volume separados; não suba o serviço `tunnel`:

```bash
docker compose --env-file .env.staging-local -p radar-staging-local up -d app worker
```

O frontend local usa `http://127.0.0.1:8001` para a API e
`http://127.0.0.1:54321` para Supabase. Em seguida, execute backfills
idempotentes e os gates:

```bash
ENV_FILE=.env.staging-local python -m radar.core.eval run explore
```

Somente corpus público e usuários/workspaces sintéticos são permitidos. E-mail,
cobrança, tunnel e promoção automática de descoberta permanecem desligados.

Para encerrar apenas a pré-produção:

```bash
docker compose --env-file .env.staging-local -p radar-staging-local down
```

## Staging Cloud futuro

`ENVIRONMENT=staging` permanece reservado para um projeto Cloud separado. Ele
será criado quando custo, tráfego ou criticidade justificarem ensaiar pooler,
TLS, latência e configuração gerenciada antes da produção.

## Promover para produção

Pré-condições: mesmo commit aprovado na pré-produção local, backup/ponto de recuperação,
resultado 4/4 dos casos motivadores e aprovação humana registrada.

```bash
ENVIRONMENT=production ALLOW_ENVIRONMENT_INITIALIZATION=1 \
  ALLOW_PRODUCTION_MUTATION=1 CONFIRM_PROJECT_REF=<ref-exato> \
  python scripts/supabase_safe.py push
```

Os opt-ins não permanecem no serviço. Depois da operação, o backend/worker usam
somente `ENVIRONMENT=production`; o boot confere a sentinela, mas não requer nem
aceita implicitamente autorização de mutação.

## Diagnóstico

`python scripts/env_doctor.py --json` informa perfil carregado, hosts,
project-ref e versões da sentinela sem imprimir DSN ou chaves. Qualquer
divergência deve ser corrigida na configuração; não contorne o guard.
