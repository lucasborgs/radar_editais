# RT00-T02 — Goldens representativos

**Status:** `awaiting_owner_review`
**Plano:** [`plans/00-relevance/RT00-T02-representative-goldens.md`](../../plans/00-relevance/RT00-T02-representative-goldens.md)
**Branch/commit-base:** `codex/radar-data-trust-00-t02` / `656c32362`
**Commits de implementação:** `cc61b31db` (feat), `729d279c0` (docs)
**Correction commits:** (2 commits a criar — `fix`, `docs`)

## Realizado

### 1. Taxonomia de atores (relevance.py)

Quatro enums novos, separados por kind, sem reuso de R/X codes:

| Enum | Códigos | Kind |
|---|---|---|
| `InvestorReasonCode` | `INV_IDENTITY_VERIFIED`, `INV_TECH_STARTUP_ACTIVITY`, `INV_BRAZIL_RELEVANCE` | investor |
| `IctReasonCode` | `ICT_IDENTITY_VERIFIED`, `ICT_INSTITUTIONAL_LINK_VERIFIED`, `ICT_ENTERPRISE_TECH_COOP`, `ICT_CURRENT_STATUS_VERIFIED` | ict |
| `ProgramReasonCode` | `PRG_IDENTITY_OPERATOR_VERIFIED`, `PRG_RELEVANT_INNOVATION_MECHANISM`, `PRG_ENTERPRISE_RELEVANCE` | program |
| `AgencyReasonCode` | `AGY_IDENTITY_VERIFIED`, `AGY_RELEVANT_INNOVATION_MANDATE`, `AGY_BRAZIL_RELEVANCE` | agency |

Cada verdict subtipo tem:
- `reason_codes` tipado com o enum específico do kind
- `evidence` tipado com a classe de evidência específica (`InvestorEvidence`, etc.)
- `model_validator` que exige **todos os códigos** para `in_scope`; `needs_review` permite subconjunto com `missing_information`

Cross-kind rejection testada: `IctReasonCode` em `InvestorVerdict` falha em validação.

### 2. Golden datasets

7 arquivos em `data/evaluation/golden/relevance/`:

| Arquivo | Casos | Decisões |
|---|---|---|
| `manifest.json` | 14 IDs referenciados | revisão pendente |
| `opportunities.json` | 7 | 1 in_scope, 3 out_of_scope, 3 needs_review |
| `investors.json` | 2 | 1 in_scope, 1 needs_review |
| `icts.json` | 1 | 1 in_scope |
| `programs.json` | 2 | 2 in_scope |
| `agencies.json` | 2 | 2 in_scope |
| `actor_sources.json` | 7 snapshots | hashes SHA-256 verificados, cross-referenciados |

**Total:** 14 casos (1 removido: ISA-CT), 3 estados no corpus total (7 in_scope, 3 out_of_scope, 4 needs_review).

`triage.json` permanece byte a byte idêntico (122 casos booleanos, lido pela suíte `triage`).

### 3. Correção de evidências (auditoria Codex)

Após avaliação do auditor, as seguintes correções foram aplicadas:

| case_id | Antes | Depois |
|---|---|---|
| `triage-tavily-093` | in_scope com quote sintetizado (non-official_page) | in_scope com R1-R5 quotes reais da página oficial da FINEP 779 |
| `triage-tavily-082` | in_scope com quote sintetizado | needs_review — site FAPESC inacessível (transporte error); evidência insuficiente |
| `indicator-capital` | in_scope com quote de curated_record | in_scope com quote real do `/about`; jurisdição removida |
| `ict:embrapii:senai-cimatec` | needs_review (sem snapshot) | in_scope com 4 ICT codes quoteados da página oficial EMBRAPII |
| `ict:embrapii:isa-ct` | needs_review (sem evidência) | **removido** — sem identidade verificável |
| `pipe-fapesp` | in_scope com curated_record | in_scope com quote real de fapesp.br/pipe |
| `centelha` | in_scope com curated_record | in_scope com quote real de programacentelha.com.br |
| `agencia:finep` | in_scope com schema.md | in_scope com quote real de finep.gov.br/sobre-a-finep |
| `agencia:fapesp` | in_scope com schema.md | in_scope com quote real de fapesp.br |

5 páginas oficiais coletadas com sucesso via HTTP GET + SHA-256 hash:
- finep.gov.br/chamadas-publicas/chamadapublica/779
- indicator.capital/pt/about
- embrapii.org.br/unidades/soluces-industriais-cimatec
- fapesp.br/pipe
- programacentelha.com.br
- finep.gov.br/sobre-a-finep
- fapesp.br

### 4. Hermetic loader

`src/radar/core/eval/relevance_goldens.py` — classe `RelevanceGoldenLoader` que:

| Validação | Escopo |
|---|---|
| Tipos de veredicto | `RelevanceVerdict.model_validate` + `actor_verdict_adapter` |
| Unicidade de case_id | Intra-arquivo **e** entre arquivos |
| human_reviewed type | Exige `bool` |
| Correspondência kind/arquivo | kind deve bater com o arquivo |
| Manifest x datasets | IDs, total_cases, by_kind, by_decision |
| review_status | Deve ser `pending_owner` se houver `human_reviewed=false` |
| Source references | `source_ref` deve existir em `actor_sources.json` com kind + record_id correspondentes |
| Integridade dos snapshots | `hash_sha256` obrigatório e verificado; `url`, `retrieved_at`, `quote` requeridos |
| **Sem rede, banco, LLM ou arquivos não versionados** | |

`distribution()` agora expõe `kind:decision` (ex: `opportunity:in_scope`) em vez de apenas `decision`.

### 5. Testes

| Teste | Status |
|---|---|
| `tests/unit/test_relevance_goldens.py` (32 tests) | all passed |
| `ruff check` (3 arquivos) | all checks passed |

Testes novos (negativos):
- `test_nonexistent_source_id` — source_ref sem correspondente em actor_sources
- `test_cross_file_duplicate` — mesmo case_id em dois datasets
- `test_wrong_manifest_total` — total_cases do manifesto diverge do real
- `test_human_reviewed_not_bool` — campo human_reviewed como string
- `test_review_status_not_pending_when_unreviewed` — review_status `approved` com human_reviewed=false
- `test_actor_source_missing_hash` — hash_sha256 ausente em actor_source
- `test_source_ref_kind_mismatch` — actor_source kind difere do item referenciado

## Source-evidence final

| Kind | Fonte de evidência | Status |
|---|---|---|
| Opportunity (in_scope) | `triage.json` legado + página oficial FINEP 779 | Verificado |
| Opportunity (out_of_scope) | `triage.json` legado | Truncado (1500 char) |
| Opportunity (needs_review) | `triage.json` legado + site inacessível | FAPESC indisponível |
| Investor | Página oficial `/about` ou curated_record com nota | Indicator verificado; KPTL pendente |
| Program | Página oficial do operador | PIPE + Centelha verificados |
| ICT | Página oficial EMBRAPII | SENAI CIMATEC verificado |
| Agency | Página oficial da agência | FINEP + FAPESP verificados |
| Actor sources | 7 snapshots com hash SHA-256 e metadados completos | Todos referenciados |

## Decisões do proprietário aplicadas

1. `triage-tavily-098`: `needs_review` — beOn Claro é hub, não oportunidade direta
2. `triage-tavily-118`: `out_of_scope` — `X7_NO_ENTERPRISE_PATH` (credenciamento de incubadoras)
3. `triage-tavily-079`: `needs_review` — elegibilidade empresarial na chamada EU-LAC
4. `investidor:kptl`: `needs_review` — sem source_urls ou data de verificação
5. `ict:embrapii:isa-ct`: **removido** — sem identidade verificável
6. Todos os `in_scope` de ator agora têm snapshot oficial verificado com hash

## Casos aguardando revisão

| case_id | Kind | Pendência |
|---|---|---|
| `triage-tavily-079` | opportunity | Elegibilidade empresarial na chamada EU-LAC — precisa de decisão do proprietário |
| `triage-tavily-082` | opportunity | FAPESC site inacessível — decidir se usa terceira fonte (CONFAP) |
| `triage-tavily-098` | opportunity | beOn Claro é hub — precisa de identificação de desafio individual concreto |
| `investidor:kptl` | investor | Faltam source_urls e data de verificação — precisa de verificação de página oficial |

## Comandos e resultados

```bash
pytest tests/unit/test_relevance_goldens.py -x -v
# 32 passed
ruff check src/radar/core/eval/relevance_goldens.py src/radar/domain/relevance.py tests/unit/test_relevance_goldens.py
# All checks passed
git diff 656c32362 -- data/evaluation/golden/triage.json
# empty

# Integrity check (hermetic):
python -c "
from radar.core.eval.relevance_goldens import RelevanceGoldenLoader
l = RelevanceGoldenLoader(); l.load_all()
assert not l.validate_all()
assert not l.validate_actor_sources()
print(f'OK: {sum(len(v) for v in l.data.values())} cases, {len(l.distribution())} decision states')
"
# OK: 14 cases, 8 decision states
```

## RT00-T03 não iniciada

Nenhum classificador, prompt, staging, API ou alteração de runtime foi implementada. Esta task produziu apenas:
- tipos de domínio (enums + validação)
- datasets versionados (corrigidos após auditoria)
- loader hermético com validações expandidas
- testes proporcionais (positivos + negativos)
