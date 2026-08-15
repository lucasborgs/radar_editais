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
scripts/compose.sh staging-local up -d app worker
```

O frontend local usa `http://127.0.0.1:8001` para a API e
`http://127.0.0.1:54321` para Supabase. Em seguida, execute backfills
idempotentes e os gates:

```bash
ENV_FILE=.env.staging-local python -m radar.core.eval run explore
```

Para o sinal hermético `e2e_health`, use um ambiente virtual com o extra de
desenvolvimento e um perfil local explícito:

```bash
.venv/bin/python -m pip install -e ".[dev]"
ENV_FILE=.env.staging-local ENVIRONMENT=test PYTHONPATH=src \
  .venv/bin/python -m radar.core.eval run e2e_health
```

O comando não acessa banco, rede ou LLM reais. Ele exige `pytest` porque
reutiliza o harness de captura gold existente. `ENV_FILE` inexistente e
`pytest` ausente são reportados pelo preflight; não há fallback para um perfil
`.env` potencialmente remoto.

Somente corpus público e usuários/workspaces sintéticos são permitidos. E-mail,
cobrança, tunnel e promoção automática de descoberta permanecem desligados.

Para encerrar apenas a pré-produção:

```bash
scripts/compose.sh staging-local down
```

## Identidade dos projetos Compose

Use sempre [`scripts/compose.sh`](../../scripts/compose.sh) para os serviços do
Radar. O wrapper fixa o arquivo de ambiente e o nome do projeto, impedindo que
um teardown de validação encontre os containers públicos:

| Alvo | Projeto Compose | Perfil |
| --- | --- | --- |
| Produção | `radar-production` | `.env` (`ENVIRONMENT=production`) |
| Pré-produção local | `radar-staging-local` | `.env.staging-local` (`ENVIRONMENT=test`) |

Um `down` de produção exige confirmação explícita e deliberada:

```bash
ALLOW_PRODUCTION_DOWN=radar-production scripts/compose.sh production down
```

Comandos `docker compose` sem o wrapper não compartilham a identidade
`radar-production` e, portanto, não removem os containers públicos. O wrapper
também rejeita tentativas de sobrescrever `--project-name` ou `--env-file`.

## Staging Cloud futuro

`ENVIRONMENT=staging` permanece reservado para um projeto Cloud separado. Ele
será criado quando custo, tráfego ou criticidade justificarem ensaiar pooler,
TLS, latência e configuração gerenciada antes da produção.

## Promover para produção

Pré-condições: mesmo commit aprovado na pré-produção local, backup/ponto de
recuperação e resultado 4/4 dos casos motivadores. Em pré-beta, o deploy é
automático via CD após CI verde — não há aprovação humana registrada.

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
