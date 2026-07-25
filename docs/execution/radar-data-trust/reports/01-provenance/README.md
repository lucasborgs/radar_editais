# Relatório consolidado — Radar Data Trust 01 (Proveniência)

**Spec:** [`../../../../specs/radar-data-trust-01-provenance.md`](../../../../specs/radar-data-trust-01-provenance.md)
**Spec-mãe:** [`../../../../specs/radar-data-trust.md`](../../../../specs/radar-data-trust.md)
**Status:** implementação concluída (RT01-T01 a RT01-T13); **pronta para promoção
a vigente, pendente de confirmação da governança e do proprietário** — esta
task (T13) prepara e propõe a reconciliação, não a decreta.
**Fechamento:** 2026-07-24 · **Implementador RT01-T13:** claude-sonnet
(subagente), worktree isolado (`/private/tmp/radar-editais-rt01-t13`)

---

## 1. Tabela de tasks

| Task | Resultado | Veredito | Commit aprovado |
|---|---|---|---|
| [`RT01-T01`](RT01-T01-provenance-types.md) | Tipos `EvidenceRef`/`FactProvenance` (domínio puro) | aprovada 2026-07-23 | `ed718bf5a` |
| [`RT01-T02`](RT01-T02-equivalence-baseline.md) | Projeção de equivalência do gold + fixtures/baseline congelado | aprovada 2026-07-23 | `ae009ddf0` |
| [`RT01-T03`](RT01-T03-evidence-resolver.md) | Resolvedor `quote → Documento Canônico/silver` | aprovada 2026-07-23 | `e40833685` |
| [`RT01-T04`](RT01-T04-additive-storage.md) | Migration 042 aditiva + storage (`provenance jsonb`, coords `match_chunks`) | aprovada 2026-07-23 | `87cf84728` |
| [`RT01-T05`](RT01-T05-finep-vertical-slice.md) | Vertical slice FINEP em dual-write (entidade + arestas + chunks) | aprovada 2026-07-24 | `a1d16a4a2` |
| [`RT01-T06`](RT01-T06-fapesp-fapesc-web.md) | FAPESP/FAPESC/Web no mesmo contrato de dual-write | aprovada 2026-07-24 | `5017b052a` |
| [`RT01-T07`](RT01-T07-embrapii-icts.md) | ICTs EMBRAPII — proveniência por registro versionado (`document_only`) | aprovada 2026-07-24 | `da1206776` |
| [`RT01-T08`](RT01-T08-curated-actors.md) | Investidores/programas/agências — curado ≠ validado (`unknown` + âncora) | aprovada 2026-07-24 | `5f35a0459` |
| [`RT01-T09`](RT01-T09-writing-chunk-lineage.md) | Linhagem dos chunks de escrita (`edital_chunks.metadata`) | aprovada 2026-07-24 | `1c24994b2` |
| [`RT01-T10`](RT01-T10-api-explore.md) | API/entity_catalog/tools do Explorar com leitura pública opcional | aprovada 2026-07-24 | `64de00e5d` |
| [`RT01-T11`](RT01-T11-product-citations.md) | Citações e estados factuais nas fichas (`ProvenanceHint`) | aprovada 2026-07-24 (QA manual do proprietário pendente) | `3d8d039ad` |
| [`RT01-T12`](RT01-T12-sample-backfill.md) | Backfill amostral + shadow metrics (reprovada e refeita 1x) | aprovada 2026-07-24, com dívida registrada | `e6f1ba8fb` |
| `RT01-T13` | Suíte final, matriz de fontes e reconciliação documental (este relatório) | **pendente** — proposto por implementador, aguarda auditoria de governança | branch `codex/radar-data-trust-01-t13`, não commitado ao pousar |

Todas as auditorias de T01–T12 foram feitas pela governança (Fable), com pelo
menos uma rodada de reexecução independente de testes/gates por task; T06,
T11 e T12 tiveram achados materiais corrigidos antes da aprovação final (ver
os relatórios individuais).

---

## 2. Matriz de fontes — estado de proveniência

| Origem | Fatos cobertos | Estado | Evidência |
|---|---|---|---|
| FINEP | `status`, `mecanismo`, `setores`, `tecnologias_tags`, `constraints`, `requisitos_texto.<i>`, arestas `operado_por`/`subordinado_a`, coords de `match_chunks` | **validada** (dual-write + equivalência do gold) | [T05](RT01-T05-finep-vertical-slice.md), [T02](RT01-T02-equivalence-baseline.md) |
| FAPESP | idem FINEP | **validada** | [T06](RT01-T06-fapesp-fapesc-web.md), [T02](RT01-T02-equivalence-baseline.md) |
| FAPESC | idem FINEP | **validada** | [T06](RT01-T06-fapesp-fapesc-web.md), [T02](RT01-T02-equivalence-baseline.md) |
| Web (Descoberta promovida) | idem FINEP, exceto aresta `operado_por` (fonte não tem agência mapeada — comportamento pré-existente preservado, não regressão) | **validada** | [T06](RT01-T06-fapesp-fapesc-web.md), [T02](RT01-T02-equivalence-baseline.md) |
| EMBRAPII (ICT) | `name`, `metadata.url` (`stated`, âncora `document_only` no registro versionado), `uf`/`setores`/`tecnologias_tags` (`inferred`), aresta `credenciada_por` | **validada** | [T07](RT01-T07-embrapii-icts.md) |
| Curadoria — investidor/programa | campos copiados verbatim (`unknown` + âncora de catálogo, nunca `stated` — "curado ≠ validado"), campos derivados (`inferred/deterministic`), aresta `operado_por` de programa | **validada** | [T08](RT01-T08-curated-actors.md) |
| Curadoria — agência | `name` (`inferred/deterministic`, sem âncora — não tem registro próprio) | **validada** | [T08](RT01-T08-curated-actors.md) |
| Chunks de escrita (`edital_chunks`) | `canonical_content_hash`, `chunker_version`, `context_version` (quando contextualização ativa); idempotência de reindex preservada | **validada** | [T09](RT01-T09-writing-chunk-lineage.md) |
| Leitura pública (API/entity_catalog/Explorar/fichas) | subconjunto público (`state`+`citations`) aditivo, sem quebrar consumidores legados; citações e rótulo de curadoria na UI | **validada**, com QA manual do proprietário ainda pendente na superfície (T11) | [T10](RT01-T10-api-explore.md), [T11](RT01-T11-product-citations.md) |
| Backfill de registros legados — **investidores** | 17/17 investidores locais carimbados (`unknown` + âncora, idempotente) | **validada** (amostra completa da população local) | [T12](RT01-T12-sample-backfill.md) |
| Backfill de registros legados — **editais** (FINEP/FAPESC/FAPESP/Web) | medido em shadow (`--dry-run`), 0 escritas por decisão explícita (`--defer-editais`) | **PARCIAL/ADIADO** — `stated=0` nos requisitos (texto normalizado por LLM não bate verbatim no silver atual); encaminhado à spec 04 | [T12](RT01-T12-sample-backfill.md) |
| Campos `inferred/deterministic` de investidor/programa/ICT no backfill (`ticket_min/max`, `status`, `mecanismo`, `formato`, `uf`, tags) | não cobertos pelo backfill (escopo explicitamente restrito pela T12 a `name`/`url`/campos copiados) | **NÃO INICIADO** — dívida registrada, sem task própria ainda | [T12](RT01-T12-sample-backfill.md#pendências) |
| Estado `conflicting` | contrato de domínio existe (`FactState.conflicting`, T01); nenhum produtor emite este estado ainda; nenhuma UI/regra de precedência | **NÃO PRODUZIDO** — encaminhado à spec 04 (retificação/precedência) | spec §4.1, §14 |
| `exige_parceria_com` (relação §3.3) | pipeline de extração atual não localiza ICT específica na constraint de parceria (comportamento pré-existente, fora do escopo de T01) | **fora do escopo observável** — arquitetura tolera o campo, ninguém o produz | `docs/architecture.md` §1 |
| Evals formais §10 (locator, completude por campo, casos obrigatórios) | cobertos por testes unit/integration ad-hoc por task, não pela suíte `core/eval/*` (harness/`registry.py`) | **NÃO CONSTRUÍDAS como gate/suíte** | ver §4 item 8 abaixo; encaminhado à spec 02 |

**Nota sobre o estado do banco local:** consulta direta ao Postgres local
(`127.0.0.1:54322`, `ENVIRONMENT=test`) em 2026-07-24 mostra `entities` com
`provenance` não-vazia apenas nos 17 investidores (backfill T12); os 3 editais
FINEP, 1 FAPESC e as 2 agências locais ainda têm `provenance='{}'` na tabela
viva, porque nenhum `ingest_all()` completo rodou localmente desde que o
dual-write (T05–T08) foi mesclado — o guard anti-clobber de `_upsert_entity`
preserva registros antigos até o próximo re-ingest incremental (`source_hash`
mudar) ou `--no-skip`. Isso é esperado pelo desenho aditivo da spec (§9.2) e
**não é uma falha de código**: o dual-write foi validado por testes de
integração com transações revertidas (T04/T05) e pela auditoria adversarial
de cada task, não por uma reingestão completa em produção/local. A primeira
reingestão real (diária, `run_daily_etl`, ou manual) materializará
proveniência nos registros que tiverem `source_hash` alterado.

---

## 3. Auditoria dos critérios de conclusão (spec §16)

| # | Critério | Estado | Evidência |
|---|---|---|---|
| 1 | 100% dos fatos críticos **novos** têm estado factual e produtor | **PARCIAL** | Cumprido para a tabela de fatos aprovada por task (status/mecanismo/setores/tags/constraints/requisitos/identidade de ICT/campos curados/arestas). `deadline` e `name` de edital ficaram **deliberadamente fora** (âncora de coleta/versão do registro do portal pertence à spec 04) — dívida confirmada em todos os relatórios T05–T08. Não é 100% do escopo §3 da spec, é 100% do subconjunto aprovado tarefa a tarefa. |
| 2 | Todo fato novo `stated` possui `EvidenceRef` resolvível e verbatim | **CUMPRIDO** | Testado adversarialmente em T01 (invariante estrutural), T03 (resolver nunca promove `exact` sem coordenada real), T05/T06/T07/T08/T12 (`test_no_stated_without_evidence_ref` e equivalentes — nenhum caminho grava `stated` sem `locator_quality` `exact`/`document_only`). |
| 3 | Relações cobertas declaram evidência ou origem curada explícita | **CUMPRIDO** para as 3 arestas ativas do pipeline (`operado_por`, `subordinado_a`, `credenciada_por` — T05/T06/T07/T08). `exige_parceria_com` nunca é produzida pelo extrator atual (pré-existente, fora do escopo de T01) — não há aresta sem proveniência, porque não há aresta. |
| 4 | Match e RAG preservam coordenadas de origem após reindex | **CUMPRIDO** | `match_chunks` (T04 storage + T05/T06 escrita); `edital_chunks` (T09, com teste de idempotência de reindex — `test_reindex_same_content_is_idempotent_and_skips_reembed`). |
| 5 | APIs e Explorar distinguem estados sem quebrar consumidores legados | **CUMPRIDO** | T10 (`public_provenance` aditivo, nenhuma chave existente alterada) + T11 (fichas — legado idêntico, sem badge/selo). |
| 6 | Registros anteriores aparecem como `legacy/unknown` até backfill válido | **CUMPRIDO** | `provenance='{}'` é o estado real de todo registro não tocado por dual-write/backfill; `public_provenance` documenta path ausente = unknown/legado; frontend não renderiza hint para esses casos (T10/T11). Não existe um valor literal `FactState.legacy` no domínio — o contrato usa ausência/`{}`, que é a leitura operacional correta da spec §9.1. |
| 7 | Fixtures de todas as origens da §9.3 comprovam equivalência do gold fora dos campos aditivos | **CUMPRIDO** | Baseline T02 (`tests/unit/test_gold_equivalence.py`, 16 testes) permaneceu **byte-idêntico** — zero diff em `equivalence.py`/`baseline_projection.json` — em toda task de T05 a T12, e continua 16/16 verde na suíte completa rodada por esta task (1321 passed, ver §5). |
| 8 | Evals cobrem os casos da §10 e não há regressão nos gates existentes | **NÃO CUMPRIDO como gate formal** | `git diff` da base do programa (`e78989876`) contra `src/radar/core/eval/` é **vazio** — nenhuma suíte do harness (`registry.py`: matching/rag/writing/extraction/explore/opportunity_type/triage/profile_extractor/reranker/structurer/relevance_shadow) foi estendida para medir locator, completude de proveniência por campo, ou os casos obrigatórios da §10.2. A cobertura real desses casos existe como testes unit/integration por task (resolução de quote, HTML sem página, trecho repetido, campo ausente, etc. — ver T01/T03/T05), o que valida o **código**, mas não produz a **métrica agregada e versionada** que a spec pede como insumo para a spec 02. **Encaminhado à spec 02** (`radar-data-trust-02-quality-gates.md`), que é explicitamente a dona de thresholds/promoção a gate (spec §10.2, último parágrafo). Nenhuma regressão nos gates existentes: `extraction` (baseline 0.95/0.95/0.92) e os demais permanecem intocados. |
| 9 | Migrations, RLS, idempotência e rollback validados | **CUMPRIDO** | Migration 042 aplicada 3x (idempotente, T04 + auditoria); RLS testada com ataque adversarial (`authenticated` não escreve, T04); dual-write idempotente por transação revertida (T04/T05 integração); backfill idempotente por reexecução real no banco local (T12, `paths_already_covered`). |
| 10 | Nenhum runtime/import/fonte de verdade depende de `spikes/` | **CUMPRIDO** | Nenhum relatório T01–T12 menciona `spikes/`; nenhum módulo produtivo (`gold.py`, `provenance_writer.py`, `evidence_resolver.py`, `provenance_read.py`, `provenance_backfill.py`) importa de `spikes/` (confirmado por leitura de cada diff). |
| 11 | Documentação autoritativa reconciliada com o runtime observado | **EM EXECUÇÃO NESTA TASK** | `docs/architecture.md` e `docs/specs/radar-data-trust.md` §9 atualizados nesta task (ver diffs no commit RT01-T13); `docs/README.md` da main **não foi editado** (fora do escopo commitável desta branch — fica registrado aqui que precisa de uma atualização de status na próxima reconciliação feita pelo proprietário/governança no checkout principal). |

**Leitura honesta do conjunto:** 8 de 11 critérios cumpridos integralmente,
1 parcial com dívida nomeada (deadline/name), 1 não cumprido como gate formal
(evals §10 — trabalho real existe como testes, não como suíte do harness) e
1 em execução nesta própria task. Nenhum critério foi marcado cumprido sem
evidência; nenhuma pendência foi escondida.

---

## 4. Pendências e dívidas encaminhadas

| # | Item | Origem | Destino | Gatilho de retomada |
|---|---|---|---|---|
| a | Âncora de coleta/versão para `deadline` e `name` de edital (fonte/hash/data de coleta do registro do portal) | T05–T08 (deliberadamente fora do escopo) | **spec 04** (`source-bundles`) | spec 04 definir o pacote documental versionado de origem (bronze com hash/data de coleta estável) |
| b | Backfill de proveniência dos editais existentes (FINEP/FAPESP/FAPESC/Web) — hoje 100% `legacy` no banco local, medido mas não escrito (`--defer-editais`) | T12 | **spec 04** | ganho real de citação verbatim — hoje `requisitos_texto` armazenado é normalizado por LLM e não bate no silver atual; spec 04 (ou um novo produtor de requisitos com evidência) precisa fechar esse descasamento antes do backfill escrever `stated`/`inferred` em editais |
| c | Estado `conflicting` — contrato de domínio existe, nenhum produtor o emite; nenhuma regra de precedência entre fontes divergentes | spec §4.1/§14 (não-objetivo explícito de T01) | **spec 04** | spec 04 definir precedência de retificação/versão, pré-requisito para qualquer produtor poder detectar e emitir `conflicting` com segurança |
| d | QA manual do proprietário nas fichas com `ProvenanceHint` (1 edital com requisito citado, 1 investidor com rótulo de catálogo, 1 ficha legada sem ícone) | T11 (reauditoria da governança) | **proprietário** | próxima sessão com o app rodando localmente — roteiro completo em [T11 §"Roteiro de QA manual"](RT01-T11-product-citations.md#roteiro-de-qa-manual) |
| e | Evals formais da spec §10 (extensão de `extraction` para locator, completude de proveniência por campo, casos obrigatórios §10.2) não construídas como suíte do harness `core/eval/*` | critério §16 item 8 (ver §3 acima) | **spec 02** (`quality-gates`) | spec 02 é a dona explícita de thresholds e promoção a gate (spec 01 §10.2, último parágrafo) — esta reconciliação apenas entrega a métrica que falta produzir, não decide threshold |
| f | Campos `inferred/deterministic` de investidor/programa (`ticket_min/max`, `status`, `mecanismo`, `formato`) e de ICT (`uf`, `setores`, `tecnologias_tags`) não cobertos pelo backfill amostral | T12 (escopo explicitamente restrito) | **backlog técnico** (`docs/BACKLOG.md`, entrada registrada nesta task) | necessidade de comparar valor re-derivado vs. armazenado por origem, mesma classe de risco que `status`/`mecanismo` de edital já tratam |
| g | `docs/README.md` da main precisa de atualização de status (spec 01 deixa de ser "proposta criada"); não editável desta branch (fora do working tree commitável) | governança/proprietário | **próxima reconciliação no checkout principal** | esta task registra a necessidade; a edição em si é ato do proprietário/governança fora deste worktree isolado |

---

## 5. Validação final (RT01-T13)

Executada em `/private/tmp/radar-editais-rt01-t13` (worktree isolado, base
`e6f1ba8fb`), Python via `/Users/lucasborges/radar_editais/.venv/bin/python`,
`PYTHONPATH=src`.

| # | Comando | Resultado |
|---|---|---|
| 1 | `ruff check .` | `All checks passed!` |
| 2 | `PYTHONPATH=src pytest -q tests/unit` | `1321 passed, 2 skipped` (baseline exato esperado) |
| 3 | `ENVIRONMENT=test DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres PYTHONPATH=src pytest -q tests/integration/test_provenance_storage.py tests/integration/test_provenance_dualwrite.py` | `13 passed` |
| 4 | `cd frontend && npm run lint && npx tsc --noEmit` | lint: só os 4 warnings pré-existentes em `src/lib/auth.tsx` (`react-hooks/exhaustive-deps`, não relacionados); tsc: 0 erros |
| 5 | Docker/worker | **não executado** — nenhuma task T01–T12 mudou wiring de container ou runtime do worker; proveniência foi aditiva em schema, produtores de ingest e leitura. Confirmado por `git diff` das tasks: nenhum `Dockerfile`, `docker-compose*.yml`, `scripts/deploy.sh` ou `src/radar/core/tasks.py` (além da linhagem de chunk em T09, que não é wiring de worker) aparece nos diffs de T01–T12 além de T09/tasks.py (só o corpo de `chunk_edital_task`, não o registro do worker/cron). |
| 6 | Evals externas (Langfuse, `core/eval run/gate`) | **não executadas** — nenhum prompt, modelo ou comportamento de IA mudou em T01–T12 (proveniência descreve os produtores existentes — tagger, constraints_producer, contextual retrieval — sem alterar prompt, modelo ou lógica de decisão deles). Confirmado por `git diff e78989876 -- src/radar/core/eval/` vazio. Nenhum threshold novo foi inventado (item encaminhado à spec 02, §4 item e acima). |

Item 1 do enunciado (correção cosmética do relatório T01, "65 = 59 + 6" → "57
+ 8") aplicada — ver diff em `RT01-T01-provenance-types.md`.

---

## 6. O que esta task NÃO fez (por desenho)

- Não alterou `gold.py`, `provenance_writer.py`, `evidence_resolver.py`,
  `equivalence.py`, migrations, RLS, prompts ou fixtures do gate — nenhum
  desses apareceu no diff produzido por T13.
- Não promoveu a spec 01 a "vigente" no header nem na spec-mãe além do texto
  de estado factual da tabela §9 (ver nota de reconciliação na própria spec
  01). Essa promoção é ato de governança/proprietário.
- Não inventou threshold de eval nem gate oficial — isso é escopo da spec 02.
- Não editou `docs/README.md` (fora do working tree commitável desta
  branch).
