# Spec - Paridade e isolamento de ambientes

**Status:** implementada com pré-produção local; staging Cloud adiado  
**Data:** 2026-07-15  
**Documentos relacionados:** [`durable-source-docs.md`](durable-source-docs.md),
[`evaluation-operations.md`](evaluation-operations.md),
[`tenant-isolation.md`](../reference/tenant-isolation.md) e
[`scripts/deploy.sh`](../../scripts/deploy.sh)

## 1. Problema

O projeto possui Supabase local, migrations versionadas e um Supabase Cloud de
deploy, mas ainda não tem um contrato único que identifique e isole `local`,
`test`, `staging` e `production`. Como consequência, uma `DATABASE_URL` remota
pode ser carregada durante testes locais, integrações dependentes de Postgres
falham de forma pouco diagnóstica e comandos de backfill não conseguem provar
qual ambiente irão alterar.

Paridade não significa compartilhar o banco de produção. Significa executar o
mesmo código, migrations, configuração estrutural e pipeline sobre bancos
isolados, com dados adequados a cada finalidade.

## 2. Decisões

### 2.1 Topologia

| Ambiente | Banco | Dados permitidos | Uso |
|---|---|---|---|
| `local` | Supabase CLI/Docker | corpus público, fixtures e dados sintéticos | desenvolvimento |
| `test` | Supabase efêmero no CI ou Supabase CLI descartável | seed determinístico, corpus público e dados sintéticos | CI, pré-produção local, E2E, RAG e agentes |
| `staging` | projeto Supabase Cloud próprio (adiado) | corpus público espelhado e workspaces sintéticos | paridade Cloud futura, quando custo/risco justificar |
| `production` | projeto Supabase Cloud exclusivo | dados reais | operação |

Produção não pode ser backend padrão de desenvolvimento, CI ou avaliação. App,
worker e frontend do mesmo ambiente compartilham seu banco; ambientes distintos
não compartilham projeto Supabase, Storage, Auth ou connection pooler.

Enquanto o plano Free estiver ocupado pelos projetos `radar-editais` e `gpteco`,
a pré-produção usa `ENVIRONMENT=test` + `INTEGRATION_TARGET=local`. O nome
`staging` continua reservado para um futuro ambiente Cloud realmente isolado;
não flexibilizamos o guard para aceitar credenciais locais como staging remoto.

### 2.2 Identidade obrigatória

`ENVIRONMENT=local|test|staging|production` será o identificador canônico. O
banco terá um sentinel durável `environment_metadata`, com uma única linha:

```text
environment, project_ref, schema_version, dataset_version, updated_at
```

No boot e antes de qualquer comando mutável, a aplicação compara
`ENVIRONMENT`, host/project-ref das credenciais e o sentinel. Divergência causa
falha fechada com mensagem explícita. `ENV` pode continuar alimentando Sentry,
mas não decide segurança de banco.

### 2.3 Credenciais

Cada ambiente possui seu próprio conjunto:

- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY` e
  `SUPABASE_JWT_SECRET`;
- `DATABASE_URL` do Postgres/pooler correspondente;
- chaves de LLM/embedding separadas por projeto ou quota quando possível;
- Langfuse e Sentry identificados por ambiente.

`service-role`, JWT secret e `DATABASE_URL` existem somente no backend, worker,
CI protegido e máquinas autorizadas. O frontend recebe apenas URL e anon key.
Segredos reais não são versionados, copiados para arquivos `*.example` nem
embutidos em fixtures.

Perfis locais:

```text
.env.local                 # gerado/ajustado pelo desenvolvedor; gitignored
.env.test                  # somente Supabase efêmero e chaves dummy
.env.staging-local         # pré-produção descartável; ENVIRONMENT=test
.env.staging.example       # nomes e defaults não secretos
.env.production.example    # nomes e defaults não secretos
```

O `.env` legado será aceito durante a transição, mas `scripts/env_doctor.py`
deverá informar que perfil foi carregado e recusar combinações ambíguas.

## 3. Contrato de paridade

Todos os ambientes ativos usam:

1. as mesmas migrations de `supabase/migrations/`, sem SQL manual não
   versionado;
2. a mesma versão de Postgres e extensões, especialmente `pgvector`;
3. as mesmas dimensões e coluna ativa de embedding;
4. os mesmos adapters, regras de autoridade e produtores Silver/gold/chunks;
5. o mesmo manifesto do harness de avaliação.

Paridade de dados será obtida por seed, não por acesso ao banco produtivo:

- editais e investidores públicos podem ser reproduzidos integralmente;
- os quatro casos de `eval_data/golden/explore.json` integram o seed canônico;
- Auth, workspaces, library e propostas usam usuários/documentos sintéticos;
- qualquer snapshot derivado de produção precisa ser anonimizado, revisado e
  armazenado fora do repositório quando contiver material privado;
- pré-produção/staging nunca envia e-mail real, executa cobrança ou promove descoberta sem
  um destinatário/sink explicitamente de teste.

## 4. Promoção de mudanças

```text
local → CI/test → pré-produção local → production
```

1. Local: `supabase db reset`, testes e avaliação hermética.
2. CI: sobe Supabase efêmero, aplica todas as migrations do zero e roda testes
   de integração sem rede externa desnecessária.
3. Pré-produção local: reseta o Supabase CLI, carrega seed/corpus público,
   executa app+worker em containers isolados, backfills, suites `rag`, `explore`,
   tenant isolation e smoke do frontend.
4. Produção: exige aprovação humana, backup/ponto de recuperação, migrations
   aprovadas no schema local construído do zero e runbook de rollback/kill switch.

Migrations são sempre forward-only. Backfill é uma etapa separada, idempotente
e observável; não fica oculto dentro da migration. O mesmo artefato/commit
aprovado na pré-produção local é promovido para produção. Aceita-se temporariamente
o risco residual de não ensaiar pooler, TLS, latência e configuração do Supabase
Cloud; ele deve ser reavaliado antes de ampliar tráfego, equipe ou criticidade.

## 5. Guards obrigatórios

- `pytest` e `supabase db reset` recusam host remoto por padrão.
- Integração remota exige `INTEGRATION_TARGET=staging`; `production` não é alvo
  válido de testes automatizados.
- Backfill, `gold --no-skip`, reindex e `supabase db push` chamam um helper
  comum `assert_database_target()` antes de escrever.
- Mutação em produção exige simultaneamente
  `ENVIRONMENT=production`, `ALLOW_PRODUCTION_MUTATION=1` e confirmação do
  `project_ref`; nenhuma dessas condições isoladamente é suficiente.
- Logs mostram ambiente, project-ref abreviado, schema/dataset version e nome
  do comando, nunca DSN ou chaves.
- CI falha se migrations pendentes não puderem construir o schema do zero.

## 6. Pacotes de implementação

### PR A - contrato e diagnóstico

- adicionar loader explícito de perfil e `scripts/env_doctor.py` read-only;
- criar migration de `environment_metadata` e `assert_database_target()`;
- aplicar o guard aos comandos mutáveis usados por ETL, gold e reindex;
- documentar os quatro bundles de variáveis sem segredos.

### PR B - teste efêmero

- subir Supabase no CI;
- aplicar migrations do zero e carregar seed mínimo;
- separar marcadores `unit`, `integration` e `remote_integration`;
- impedir que credenciais remotas herdadas ativem testes Postgres por acidente.

### PR C - pré-produção local

- criar perfil `.env.staging-local` sem credenciais Cloud;
- isolar containers, porta HTTP e diretório de dados do stack publicado;
- carregar corpus público e workspaces sintéticos no Supabase descartável;
- manter efeitos externos desativados.

### PR D - gates conectados e promoção

- executar backfill da spec de RAG factual na pré-produção local;
- rodar as quatro respostas reais contra o golden semântico;
- promover migrations/backfill para produção somente após 4/4 casos passarem;
- registrar dataset, commit, modelos e resultados da rodada.

### Estado da implementação (2026-07-15)

- PR A: implementada (`core/environment.py`, migration 040, `env_doctor`,
  perfis e guards dos CLIs mutantes).
- PR B: implementada (seed, isolamento em `tests/conftest.py`, marcadores e job
  Supabase efêmero no CI).
- PR C: implementada como pré-produção local descartável; staging Cloud foi
  deliberadamente adiado para não exceder o limite do plano Free.
- PR D: o golden de quatro casos e a suíte `explore` estão versionados; a rodada
  conectada usa Supabase local e credenciais LLM da máquina autorizada. A promoção
  Cloud continua protegida e nunca recebe segredos pelo chat ou repositório.

## 7. Critérios de aceitação

- Um comando read-only informa inequivocamente ambiente e banco-alvo sem expor
  segredo.
- Testes locais/CI não conseguem alcançar produção mesmo se uma credencial
  produtiva estiver presente no shell.
- O schema nasce do zero em local e CI usando somente migrations versionadas.
- A pré-produção local reproduz app + worker + Auth + Storage + pgvector e
  executa as suites conectadas sobre dados descartáveis.
- Os quatro casos do Explore passam na pré-produção local nas camadas de rota, autoridade,
  retrieval, grounding e resposta.
- Produção só recebe o mesmo commit aprovado na pré-produção e mediante guard
  explícito de mutação.

## 8. Fora de escopo

- copiar dados privados de produção para local ou staging;
- usar schemas diferentes dentro do mesmo Postgres como isolamento principal;
- criar forks permanentes de migrations ou código por ambiente;
- permitir que avaliações automatizadas escrevam em workspaces reais; e
- substituir backup, observabilidade ou RLS pelo isolamento de ambientes.
