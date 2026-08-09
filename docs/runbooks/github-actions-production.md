# CD no Docker local de produção

O CI continua executando em runners hospedados pelo GitHub. O CD usa um runner
self-hosted instalado no PC que hospeda o Docker do Radar.

## Fluxo

Cada push em `main` executa:

1. deploy do `radar-staging-local`;
2. smoke autenticado real contra Supabase local, backend e frontend;
3. espera por aprovação no Environment `Production`;
4. migration protegida do Supabase Cloud;
5. build versionado pelo SHA do commit;
6. atualização do `radar-production`;
7. health check da API.

Pull Requests nunca executam deploy.

## Preparação única do PC

### Runner

No GitHub, abra `Settings → Actions → Runners → New self-hosted runner` e
instale o runner neste PC. Use uma conta do sistema dedicada, conceda a ela
acesso ao Docker e adicione a label:

```text
radar-production-host
```

Deixe o runner configurado como serviço para iniciar com o sistema.

O runner precisa ter:

- Docker e Docker Compose;
- Supabase CLI 2.98.2;
- Python 3.12;
- Node.js 20;
- acesso de rede ao Supabase Cloud.

### Arquivos locais protegidos

No workspace usado pelo runner, crie os arquivos gitignored:

- `.env.staging-local`, com base em `envs/.env.staging-local.example`;
- `.env`, com base em `envs/.env.production.example`;
- `cloudflared/config.yml` e as credenciais do tunnel.

O `.env` deve apontar para o mesmo projeto Supabase de produção e declarar
`ENVIRONMENT=production`. Não copie esses arquivos para o repositório.

Faça uma vez o vínculo da CLI Supabase com o projeto de produção no usuário do
runner:

```bash
supabase link --project-ref <production-project-ref>
```

O `PROD_DATABASE_URL` do Environment `Production` é usado pela migration
protegida. A `OPENAI_API_KEY` deve existir também no Environment `Production`;
ela é injetada somente durante a atualização dos containers.

## Aprovação de produção

No GitHub, configure o Environment `Production` com pelo menos um revisor
obrigatório. O job de produção não inicia antes dessa aprovação.

## Rollback

As imagens são marcadas com o SHA do commit. Para retornar à versão anterior,
defina `IMAGE_TAG` com o SHA validado anterior e execute manualmente:

```bash
IMAGE_TAG=<sha-anterior> scripts/compose.sh production up -d --no-build app worker tunnel
curl --fail http://127.0.0.1:8000/health
```

Não remova imagens antigas antes de confirmar a nova versão.

## Limite atual

O CD não executa smoke autenticado contra produção. O smoke existente cria e
remove dados temporários e é deliberadamente restrito a Supabase local. Um
smoke autenticado de produção exige uma conta e workspace técnicos dedicados,
com política explícita de dados descartáveis.
