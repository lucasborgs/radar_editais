# Radar Data Trust 02 — Quality Gates e Cobertura de Avaliacao

**Status da spec:** implementacao concluida; auditoria Codex pendente
**Branch de integracao:** codex/radar-data-trust-02-completion
**Base:** 37f34a74d112b441b91279d058209f127ce1e1d9

## Tasks e commits

Integracao da Onda A (cherry-pick preservado):

| Task | Commit | Descricao |
|---|---|---|
| RT02-T01 | d3cbed372 | Golden representativo de proveniencia (6 casos) |
| RT02-T03 | b248a2be0 | Revisao leve das suites existentes |
| RT02-T04 | 5f0a3d4ec | Sinal E2E e2e_health diagnostico |

Commits proprios:

| Commit | Descricao |
|---|---|
| e0a2c885d | fix(eval): refuse production and remote targets (hardening) |
| 8b9640a1d | feat(eval): provenance diagnostic suite (RT02-T02) |
| 3495c03d9 | docs: fix reports and add RT02-T02 report |
| 641b6e648 | fix(eval): make RT02 signals fail-closed and observed |
| (correção) | fix: add tests/__init__.py — resolve No module named 'tests' |
| fechamento desta task | docs: RT02-T05 quality-map reconciliation |

## Mapa camada -> suite -> classificacao

| Camada | Suite | Classificacao | Golden | Execucao observada | Limitacao de representatividade |
|---|---|---|---|---|---|
| Triagem/relevancia | triage | diagnostic | 122 | skip (requer LLM) | Contagem mede corpus, nao representatividade |
| Triagem/relevancia | relevance_shadow | diagnostic | - | skip | Diagnostica shadow |
| Aquisicao/estrutura | structurer | diagnostic | 12 | skip (requer LLM) | Golden pequeno |
| Extracao | extraction | gate | 10 | gate com 0.95 | Baseline aceito; golden curado |
| Proveniencia | provenance | diagnostic (NOVA) | 6 | rodou (deterministico) | Baseline comportamental; 6 casos nao provam representatividade |
| Recuperacao/consumo | rag | diagnostic | 28 | skip (requer pgvector) | So fonte FINEP por default |
| Recuperacao/consumo | matching | candidate | 11 | candidata | Criterios aceitos, contrato incompleto |
| Recuperacao/consumo | explore | diagnostic | 4 | rodou (hermetico) | 4 casos propositalmente pequenos |
| Recuperacao/consumo | reranker | diagnostic | 20 | rodou (cross-encoder) | Depende de backend ativo |
| Perfil | profile_extractor | diagnostic | 15 | skip (requer LLM) | Contagem nao equivale a cobertura |
| Escrita | writing | diagnostic (corrigido) | - | skip (requer workspace) | Sem threshold aceito |
| Escrita | writing_v2 | experimental | - | skip (requer workspace) | Limitacoes F0 conhecidas |
| E2E | e2e_health | diagnostic (NOVA) | 1 | sinal minimo | Um caminho, nao matriz |

## Incidente de producao (RT02-T03)

O incidente foi contido:
- Houve leitura de producao e custo de LLM durante execucao da revisao leve.
- Nenhuma escrita em producao.
- Nenhuma credencial exposta ou commitada.
- Nenhum resultado daquela rodada deve ser usado.
- Nao e necessaria rotacao de credenciais.

Hardening durave adicionado: _refuse_hostile_environment() em harness.py
recusa ENVIRONMENT=production/staging e ambientes local/test/unknown com
alvo remoto, antes de qualquer prereqs, load_data ou task. Mensagens
sanitizadas (sem DSN/credenciais/URLs completas). Nenhuma flag de bypass
para producao foi criada. Testado com 7 testes adversariais.

## Recomendacao de maturidade (recomendacao, nao decisao)

Nenhuma suite teve classificacao promovida. Nenhum threshold foi criado.

- extraction: gate ativo (baseline 0.95 aceito) - manter.
- matching: candidata com criterios aceitos - requer finalizacao dos
  julgamentos do top-8.
- provenance: baseline comportamental de locator+faithfulness; golden pequeno
  (6), sem state/producer observado por caso; nao sustenta gate.
- e2e_health: sinal minimo; util como smoke test, nao como gate sem
  expansao para multiplos caminhos.
- Demais suites: diagnosticas, sem baseline aceito para promocao.

## Reconciliacao documental

### writing classification

A fotografia da spec dizia writing/writing_v2 como experimental.
O runtime e: writing = diagnostic (default; agora explicito no codigo),
writing_v2 = experimental (ja explicito). Nenhuma mudanca efetiva.

### Golden de proveniencia

Golden e baseline comportamental diagnostico do resolvedor atual.
Seis casos nao provam representatividade. Nao autoriza threshold nem gate.

### conflicting/retificacao

Permanecem encaminhados a spec 04 (nenhum produtor os emite hoje).

### Suites novas

provenance e e2e_health adicionadas a registry.py, evaluation-operations.md
e AGENTS.md. Nenhuma suite nova e gate ou candidate.

## Validação final local (correção pós-auditoria)

- **Regressão corrigida:** `src/radar/core/eval/e2e_health.py` importava
  `tests.helpers.gold_projection` sem que `tests` fosse um pacote importável
  (`ModuleNotFoundError: No module named 'tests'`). A correção adicionou
  `tests/__init__.py` e `tests/helpers/__init__.py`, tornando `tests` um
  pacote sem alterar `PYTHONPATH`, `sys.path` ou duplicar o harness.
- Testes direcionados (`provenance`, `e2e_health`, harness e golden): **71 passed**
  (integralmente verde, mesmo comando canônico `ENVIRONMENT=test PYTHONPATH=src`).
- `ruff check` sobre todo Python versionado: **verde**.
- `provenance` foi executada duas vezes: agregados idênticos (exact,
  document_only e unresolved: 2/6 cada; faithfulness: 4/4 entre candidatos
  resolvidos). A suíte não mede state/producer por caso.
- `e2e_health` foi executada duas vezes: agregados idênticos; state presente e
  producer completo em 1.0; sinais do caminho em 1.0 e `operational_error=0.0`.
- `pytest -q` (excluindo 5 gold tests com import pré-existente `from helpers.gold_projection`
  que não faz parte do canônico `PYTHONPATH=src`): **1317 passed, 64 skipped, 1 failed**.
  A única falha é `test_card_has_exact_key_set` — `_row_to_card` expõe `'provenance'`
  mas `CARD_KEYS` não a lista. Pré-existente em relação à base `37f34a74d`
  (os quatro arquivos — `entity_catalog`, `chunker` e seus testes — não diferem
  entre base e branch).
- `git diff --check`: verde.

Todas as validações desta task usaram `ENVIRONMENT=test` e fixtures locais:
sem `.env`, credenciais, rede, LLM, banco remoto, publicação Langfuse ou
produção.

## Artefatos

- src/radar/core/eval/provenance.py - suite provenance
- src/radar/core/eval/e2e_health.py - suite e2e_health
- src/radar/core/eval/harness.py - hardening de ambiente
- data/evaluation/golden/provenance/ - golden
- tests/unit/test_eval_provenance.py - 13 testes
- tests/unit/test_eval_harness.py - 26 testes (+7 adversariais)
- tests/__init__.py, tests/helpers/__init__.py - correção: pacotes tests/ importáveis

## Relatorios individuais

- [RT02-T01-provenance-golden.md](RT02-T01-provenance-golden.md)
- [RT02-T02-provenance-suite.md](RT02-T02-provenance-suite.md)
- [RT02-T03-existing-suites-review.md](RT02-T03-existing-suites-review.md)
- [RT02-T04-e2e-health-suite.md](RT02-T04-e2e-health-suite.md)
- [RT02-T05-quality-map-reconciliation.md](RT02-T05-quality-map-reconciliation.md)
