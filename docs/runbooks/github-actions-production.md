# CD no Docker local de produção

O CI continua executando em runners hospedados pelo GitHub. O CD usa um runner
self-hosted instalado no PC que hospeda o Docker do Radar.

## Fluxo

Cada push em `main` executa:

1. deploy do `radar-staging-local`;
2. smoke autenticado real contra Supabase local, backend e frontend;
3. registro das imagens de produção atualmente em execução, para rollback;
4. snapshot lógico validado do Supabase Cloud, persistido no host antes de qualquer migration;
5. migration protegida do Supabase Cloud;
6. build versionado pelo SHA do commit;
7. atualização do `radar-production`;
8. health check da API.

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
- Docker apto a executar a imagem ARM64 fixada do cliente PostgreSQL, usada pelo CD;
- Supabase CLI 2.98.2;
- Python 3.12;
- Node.js 20;
- acesso de rede ao Supabase Cloud.

### Arquivos locais protegidos

Fora do workspace do runner, crie a pasta persistente
`$HOME/.config/radar-editais/` e coloque nela:

- `.env.staging-local`, com base em `envs/.env.staging-local.example`;
- `.env`, com base em `envs/.env.production.example`;
- `cloudflared/config.yml` e as credenciais do tunnel.

O workflow copia esses arquivos para o checkout somente durante o job e os
remove ao final. Não coloque os arquivos diretamente em `_work/...`: o
`actions/checkout` limpa esse diretório no início de cada execução.

O `.env` deve apontar para o mesmo projeto Supabase de produção e declarar
`ENVIRONMENT=production`. Não copie esses arquivos para o repositório.

O `PROD_DATABASE_URL` do Environment `Production` é usado pela migration
protegida via URL explícita e pelo snapshot lógico pré-migration; o CD não
depende de `supabase link` persistido no runner. A etapa de staging baixa e
verifica antecipadamente a imagem ARM64 fixada do cliente PostgreSQL. Em
produção, o dump e sua validação são executados com o ID dessa imagem, sem expor
a URL de conexão nos argumentos de processo. O snapshot fica em
`$HOME/.local/share/radar-editais/recovery-snapshots/`, com permissões privadas,
e o workflow falha antes da migration se não puder criá-lo e validá-lo com
`pg_restore --list`. A `OPENAI_API_KEY` deve existir também no Environment `Production`;
ela é injetada somente durante a atualização dos containers.

## Segurança da produção

Como o projeto roda em pré-beta com dados descartáveis, o CD não exige
aprovação humana: um push verde em `main` chega a produção automaticamente.
Os gates restantes são de máquina: snapshot lógico validado com `pg_restore --list`
antes de cada migration, migration protegida e health check pós-deploy.

O Environment `Production` mantém apenas escopo de secret e a política de branch
(`main`): `PROD_DATABASE_URL` e `OPENAI_API_KEY` são resolvidos de lá pelo job;
nenhuma regra de revisão (`required_reviewers`) está configurada.

## Rollback

As imagens são marcadas com o SHA do commit. O resumo do job de produção registra
a imagem e o digest dos containers anteriores antes de cada migration. Para retornar
à versão anterior, use esse SHA validado e execute manualmente:

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
