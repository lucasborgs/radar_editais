# RT01-T13 — Validação final, evals proporcionais e reconciliação documental

**Status:** proposto (validação + reconciliação; promoção da spec é ato de governança)
**Plano:** fechamento da spec [`radar-data-trust-01-provenance.md`](../../../specs/radar-data-trust-01-provenance.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t13` / `e6f1ba8fb`
**Commits:** commit único desta branch (ver `git log codex/radar-data-trust-01-t13`)
**Implementador/modelo:** claude-sonnet (subagente), worktree isolado
(`/private/tmp/radar-editais-rt01-t13`)

## Realizado

Esta task é de **validação e reconciliação documental**, não de feature nova.
Nenhum módulo produtivo de proveniência (`gold.py`, `provenance_writer.py`,
`evidence_resolver.py`, `equivalence.py`, `provenance_backfill.py`,
migrations, RLS, prompts, fixtures do gate) foi tocado.

1. Lidas integralmente: `docs/specs/radar-data-trust-01-provenance.md`,
   `docs/specs/radar-data-trust.md` (§9, §15), os 12 relatórios
   `RT01-T01`–`RT01-T12` (incluindo as auditorias da governança dentro de
   cada um), `AGENTS.md` e `docs/architecture.md`.
2. Rodadas as 4 validações executáveis do enunciado (outputs completos em
   "Validação" abaixo); Docker/worker e evals externas **não executados**,
   com justificativa registrada (nenhuma task RT01 alterou wiring de
   container/worker nem prompt/modelo de IA).
3. Corrigida a dívida cosmética do relatório T01 (quebra `65 = 59 + 6` →
   `57 + 8`, total preservado).
4. Escrito o consolidado
   [`reports/01-provenance/README.md`](README.md): tabela T01–T13 com
   veredito e commit aprovado por task, matriz de fontes (validada/parcial/
   bloqueada/não produzida, com link do relatório), auditoria linha-a-linha
   dos critérios §16 (8 cumpridos, 1 parcial, 1 não cumprido como gate
   formal, 1 em execução nesta própria task) e a lista de pendências
   encaminhadas com destino (spec 02, spec 04, proprietário, backlog
   técnico).
5. Reconciliação documental, só status/reconciliação, contratos normativos
   intocados:
   - `docs/specs/radar-data-trust-01-provenance.md` — nota de reconciliação
     inserida logo após o cabeçalho (cabeçalho/status **não alterados**,
     conforme instrução explícita), apontando para o consolidado e
     declarando "pronta para promoção a vigente, pendente de confirmação da
     governança";
   - `docs/specs/radar-data-trust.md` §9 — linha da spec 01 mudou de
     "proposta criada" para o estado real (implementação concluída,
     pendências encaminhadas);
   - `docs/architecture.md` — parágrafo novo em "1. Plano de dados"
     descrevendo fielmente o runtime observado: colunas `provenance` em
     `entities`/`entity_relationships`, coordenadas em `match_chunks`,
     linhagem em `edital_chunks.metadata`, leitura pública restrita a
     `{state, citations}`, e o estado real de cobertura por origem (dual-write
     pleno em FINEP/FAPESP/FAPESC/Web/EMBRAPII; curado≠validado em
     investidor/programa/agência; legado em `{}` até reingest/backfill).
6. Dívida registrada em `docs/BACKLOG.md`: campos `inferred/deterministic`
   de investidor/programa/ICT não cobertos pelo backfill amostral (T12),
   com evidência, motivo, gatilho e ponto de entrada — segue o formato
   existente do arquivo.
7. `docs/README.md` da main **não foi editado** — está fora do working tree
   commitável desta branch (working tree do proprietário no checkout
   principal). Registrado no consolidado (§4, item g) que ele precisa de
   atualização de status na próxima reconciliação feita pelo
   proprietário/governança.

## Divergências e decisões

- **Nenhuma migration, código produtivo ou fixture do gate foi tocada** —
  confirmado por `git diff e6f1ba8fb --stat` (ver "Validação"): só arquivos
  de `docs/` foram modificados/criados.
- **A spec 01 não foi marcada como vigente.** O cabeçalho
  (`**Status:** proposta para aprovação`) permanece byte-a-byte idêntico;
  a nota de reconciliação inserida logo abaixo dele é aditiva e declara
  explicitamente que a promoção pertence à governança/proprietário.
- **O critério §16 item 8 (evals §10) foi marcado NÃO CUMPRIDO como gate
  formal, não varrido para "cumprido por analogia".** `git diff e78989876 --
  src/radar/core/eval/` (a base completa do programa Radar Data Trust) é
  vazio — nenhuma suíte do harness (`registry.py`) foi estendida para medir
  locator, completude de proveniência por campo ou os casos obrigatórios
  §10.2. O trabalho real de T01/T03/T05 (testes unit/integration cobrindo
  trecho único, repetido, HTML sem página, campo ausente etc.) valida o
  **código**, não produz a **métrica agregada e versionada** que a spec pede
  como insumo para thresholds da spec 02. Decisão: reportar o gap
  honestamente e encaminhar à spec 02 (dona explícita de thresholds/gates,
  spec 01 §10.2 último parágrafo), em vez de reinterpretar "evals" como
  "qualquer teste automatizado".
- **Item §16 #1 (100% dos fatos críticos novos) marcado PARCIAL, não
  cumprido.** `deadline` e `name` de edital são fatos críticos do escopo §3.1
  da spec (grupos "temporal"/"identidade") e ficaram deliberadamente sem
  proveniência em T05–T08 (âncora de coleta pertence à spec 04). Isso é uma
  lacuna real do escopo original §3, não apenas uma dívida de detalhe —
  reportado como tal, não maquiado de "cumprido para o que foi implementado".
- **Estado do banco local registrado explicitamente no consolidado (§2,
  nota).** Uma consulta direta ao Postgres local mostrou que só os 17
  investidores (backfill T12) têm `provenance` não-vazia nas tabelas vivas —
  editais/agências locais ainda mostram `{}` porque nenhum `ingest_all()`
  completo rodou localmente após o dual-write ser mesclado. Decisão: não
  tratar isso como falha de código (o dual-write foi validado por testes de
  integração com transação revertida, não por reingestão real), mas
  registrar honestamente que a materialização em produção depende do
  próximo ciclo de ingestão incremental ou de um `--no-skip`.
- **`node_modules` do frontend** estava ausente no worktree isolado
  (gitignored, como `data/silver/structured_docs` foi para a T12). Resolvido
  com um symlink temporário para `/Users/lucasborges/radar_editais/frontend/
  node_modules` (leitura apenas — `npm run lint`/`npx tsc --noEmit`), removido
  logo em seguida. `git status` confirma worktree limpo (nenhum rastro do
  symlink).

## Dados e migrations

Não aplicável — nenhuma migration, tabela ou dado de `data/`/banco foi
alterado por esta task. As consultas ao Postgres local (contagens de
`entities`/`entity_relationships`/`match_chunks` por origem, usadas na matriz
de fontes) foram somente leitura (`SELECT`), confirmadas por não aparecerem
em nenhum `INSERT`/`UPDATE`/`DELETE` executado por esta task.

## Validação

### 1. `ruff check .`

```
All checks passed!
```

### 2. `PYTHONPATH=src pytest -q tests/unit`

```
1321 passed, 2 skipped, 4 warnings in 13.11s
```

Baseline exato esperado pelo enunciado (1321 passed / 2 skipped). Nenhuma
falha, nenhuma regressão.

### 3. `ENVIRONMENT=test DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres PYTHONPATH=src pytest -q tests/integration/test_provenance_storage.py tests/integration/test_provenance_dualwrite.py`

```
.............                                                            [100%]
13 passed in 0.53s
```

### 4. Frontend — `cd frontend && npm run lint && npx tsc --noEmit`

```
$ npm run lint
./src/lib/auth.tsx
44:6  Warning: React Hook useEffect has a missing dependency...
56:5  Warning: React Hook useCallback has a missing dependency...
61:6  Warning: React Hook useCallback has a missing dependency...
66:6  Warning: React Hook useCallback has a missing dependency...
```

Só os 4 warnings pré-existentes em `auth.tsx` (mesmos já registrados no
relatório T11), nenhum erro. `npx tsc --noEmit` não produziu output (0
erros). `npm run build` não foi executado (proibido pelo enunciado).

### 5. Docker/worker — NÃO executado

Justificativa: nenhuma task `RT01-T01`–`RT01-T12` alterou `Dockerfile`,
`docker-compose*.yml`, `scripts/deploy.sh`, o wiring do worker
(`procrastinate --app=radar.core.tasks.app worker`) ou os crons registrados.
A única mudança em `src/radar/core/tasks.py` (T09) foi dentro do corpo de
`chunk_edital_task`/`_build_chunks_for_edital` (linhagem aditiva de
metadata), sem alterar sua assinatura, agendamento ou registro no app
Procrastinate. Proveniência foi aditiva em schema, produtores de ingest
existentes e leitura — nenhum runtime de container ou processo de worker
mudou.

### 6. Evals externas — NÃO executadas

Justificativa: nenhuma task `RT01-T01`–`RT01-T12` alterou prompt, modelo ou
comportamento de decisão de nenhuma LLM existente (tagger, constraints_producer,
contextual retrieval, ExploreAgent, WritingAgent). Confirmado por
`git diff e78989876 -- src/radar/core/eval/` **vazio** — nenhuma suíte do
harness foi tocada por T01–T12. Proveniência descreve os produtores
existentes (adiciona metadados sobre a decisão já tomada), não os altera.
Nenhum threshold novo foi inventado — essa é competência da spec 02
(registrado no consolidado, §4 item e).

### Diff desta task

```
$ git diff e6f1ba8fb --stat
 docs/BACKLOG.md                                                          |  25 ++
 docs/architecture.md                                                     |  17 ++
 docs/execution/radar-data-trust/reports/01-provenance/README.md          | ~140 (reescrito)
 docs/execution/radar-data-trust/reports/01-provenance/RT01-T01-provenance-types.md | 2 +-
 docs/specs/radar-data-trust-01-provenance.md                             |   9 +
 docs/specs/radar-data-trust.md                                           |   2 +-
```

(mais este próprio relatório, novo). Nenhum arquivo fora de `docs/` aparece
no diff — confirmado por `git status --short` antes do commit.

## Pendências

Ver a seção "4. Pendências e dívidas encaminhadas" do consolidado
[`README.md`](README.md) — não duplicado aqui para evitar que as duas listas
divirjam. Resumo dos destinos: (a) âncora deadline/name → spec 04; (b)
backfill de editais → spec 04; (c) estado `conflicting` → spec 04; (d) QA
manual das fichas (T11) → proprietário; (e) evals §10 → spec 02; (f) campos
derivados de investidor/programa/ICT no backfill → `docs/BACKLOG.md`
(registrado nesta task); (g) `docs/README.md` da main → próxima reconciliação
no checkout principal.

**Implementador/modelo: claude-sonnet (subagente), worktree isolado**

**Veredito:** pendente
