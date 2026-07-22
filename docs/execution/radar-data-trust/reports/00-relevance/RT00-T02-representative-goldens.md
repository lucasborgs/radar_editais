# RT00-T02 — Goldens representativos

**Status:** `awaiting_owner_review`
**Plano:** [`plans/00-relevance/RT00-T02-representative-goldens.md`](../../plans/00-relevance/RT00-T02-representative-goldens.md)
**Branch/commit-base:** `codex/radar-data-trust-00-t02` / `656c32362`

| Commit | Assunto |
|---|---|---|
| `656c32362` | docs: approve RT00-T01 audit (base) |
| `cc61b31db` | feat: add representative relevance golden drafts |
| `729d279c0` | docs: report RT00-T02 review packet |
| `776d4ca6b` | fix: verify golden evidence with real official-page snapshots |
| `5eec3afd5` | docs: correct RT00-T02 review report |
| `6c39698a9` | fix: bind golden evidence to versioned sources |
| `7b1b8b3d2` | docs: finalize RT00-T02 review report |
| `286146618` | fix: enforce reason_code/evidence correspondence |
| `74c35264a` | docs: update RT00-T02 report for final audit findings |

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
| `actor_sources.json` | 7 snapshots | hashes SHA-256, cross-referenciados |

**Total:** 14 casos (1 removido: ISA-CT), 3 estados no corpus total (7 in_scope, 3 out_of_scope, 4 needs_review).

`triage.json` permanece byte a byte idêntico (122 casos booleanos, lido pela suíte `triage`).

### 3. Correção de evidências (2 ciclos de auditoria)

**1º ciclo** (commit 776d4ca6b): substituição de evidências sintetizadas por snapshots reais.

| case_id | Antes | Depois |
|---|---|---|
| `triage-tavily-093` | quote sintetizado | quote literal da FINEP 779 |
| `triage-tavily-082` | quote sintetizado | needs_review (FAPESC inacessível) |
| `indicator-capital` | quote de curated_record | quote real do `/about` |
| `ict:embrapii:senai-cimatec` | needs_review sem snapshot | in_scope com EMBRAPII oficial |
| `ict:embrapii:isa-ct` | needs_review sem evidência | **removido** |
| `pipe-fapesp` | quote de curated_record | quote real de fapesp.br/pipe |
| `centelha` | quote de curated_record | quote real de programacentelha.com.br |
| `agencia:finep` | quote de schema.md | quote real de finep.gov.br/sobre-a-finep |
| `agencia:fapesp` | quote de fapesp.br | quote real de centrodememoria.fapesp.br/sobre-a-fapesp |

**2º ciclo** (commit 6c39698a9): vinculação estrita de cada evidence.quote ao snapshot.

- **Todos os `src:*`:** cada `evidence[].quote` na íntegra é substring do `actor_sources.quote` correspondente (normalização de espaços apenas).
- **Todos os `legacy_triage_case`:** `source_record_id` confirmado em `triage.json`; cada `evidence[].quote` presente em `title | snippet | content`.
- **`curated_record` (KPTL):** `source_record_id` confirmado em `data/silver/investidores.json`.
- **FAPESP:** snapshot trocado de `fapesp.br` (página genérica) para `centrodememoria.fapesp.br/sobre-a-fapesp/` (página institucional histórica com conteúdo substantivo).

**3º ciclo** (commit 286146618): correção de reason_codes e validação de evidência.

- `triage-tavily-082`: `R4_RELEVANT_BENEFIT` removido de `reason_codes` (sem evidência disponível); permanece em `missing_information`.
- `triage-tavily-079`: `R1_ENTERPRISE_PATH` removido de `reason_codes` (sem evidência disponível); permanece em `missing_information`.
- Loader rejeita `reason_code` sem `evidence` com o mesmo `code`.
- Loader rejeita `reason_code` que também aparece em `missing_information`.

7 snapshots oficiais, todos com hash SHA-256 verificado:
- `finep.gov.br/chamadas-publicas/chamadapublica/779` — `6624a764…`
- `indicator.capital/pt/about` — `5d1e3c35…`
- `embrapii.org.br/unidades/soluces-industriais-cimatec` — `6897b062…`
- `fapesp.br/pipe` — `155f38fb…`
- `programacentelha.com.br` — `34918f09…`
- `finep.gov.br/sobre-a-finep` — `0839d828…`
- `centrodememoria.fapesp.br/sobre-a-fapesp/` — `43bb2135…`

### 4. Hermetic loader

`src/radar/core/eval/relevance_goldens.py` — classe `RelevanceGoldenLoader`:

| Validação | Escopo |
|---|---|
| Tipos de veredicto | `RelevanceVerdict.model_validate` + `actor_verdict_adapter` |
| Unicidade de case_id | Intra-arquivo **e** entre arquivos |
| Unicidade de source_id | `validate_actor_sources()` rejeita duplicatas |
| human_reviewed type | Exige `bool` |
| Correspondência kind/arquivo | kind deve bater com o arquivo |
| Manifest x datasets | IDs, total_cases, by_kind, by_decision |
| review_status | Deve ser `pending_owner` se houver `human_reviewed=false` |
| Source references | `source_ref` deve existir em `actor_sources.json` com kind + record_id correspondentes |
| Evidence quote integrity | Cada `evidence[].quote` é substring do snapshot (`src:*`), do triage entry (`legacy_triage_case`), ou o `source_record_id` existe no catálogo prata (`curated_record`) |
| Integridade dos snapshots | `hash_sha256` obrigatório e verificado; `url`, `retrieved_at`, `quote` requeridos |
| Orphaned detection | Actor source sem dataset correspondente é rejeitado (salvo se justificado no manifest) |
| **Sem rede, banco, LLM ou arquivos não versionados** | |

### 5. Testes

| Suite | Testes | Status |
|---|---|---|
| `tests/unit/test_relevance.py` | 115 | all passed |
| `tests/unit/test_relevance_goldens.py` | 37 | all passed |
| `tests/unit/test_hardening_pr4.py` | 8 | all passed |
| **Total** | **160** | **all passed** |

Testes negativos (16 novos nesta task):

| Teste | Validacão |
|---|---|
| `test_nonexistent_source_id` | source_ref sem correspondente em actor_sources |
| `test_cross_file_duplicate` | mesmo case_id em dois datasets |
| `test_wrong_manifest_total` | total_cases do manifesto diverge do real |
| `test_human_reviewed_not_bool` | campo human_reviewed como string |
| `test_review_status_not_pending_when_unreviewed` | review_status `approved` com `human_reviewed=false` |
| `test_actor_source_missing_hash` | hash_sha256 ausente em actor_source |
| `test_source_ref_kind_mismatch` | actor_source kind difere do item referenciado |
| `test_duplicate_source_id` | source_id duplicado em actor_sources |
| `test_evidence_quote_not_in_snapshot` | evidence quote não é substring do snapshot |
| `test_legacy_case_id_not_in_triage` | source_record_id não existe em triage.json |
| `test_legacy_quote_not_in_triage_body` | evidence quote ausente do body do triage |
| `test_curated_record_id_not_in_silver` | source_record_id não existe no catálogo prata |
| `test_reason_code_without_evidence` | reason_code sem evidence entry correspondente |
| `test_reason_code_also_in_missing_information` | reason_code aparece em reason_codes e missing_information |

## Source-evidence final

| Kind | Fonte | Verificação |
|---|---|---|
| Opportunity (in_scope) | Página oficial FINEP 779 | R1-R5 substrings do snapshot |
| Opportunity (out_of_scope) | triage.json legado | Quotes no title/snippet/content |
| Opportunity (needs_review) | triage.json legado | Quotes no title/snippet/content |
| Investor (Indicator) | Página oficial /about | 3 quotes substrings do snapshot |
| Investor (KPTL) | curated_record | source_record_id em investidores.json |
| Program (PIPE) | Página oficial fapesp.br/pipe | 3 quotes substrings do snapshot |
| Program (Centelha) | Página oficial programacentelha.com.br | 3 quotes substrings do snapshot |
| ICT (SENAI CIMATEC) | Página oficial EMBRAPII | 4 quotes substrings do snapshot |
| Agency (FINEP) | Página oficial finep.gov.br | 3 quotes substrings do snapshot |
| Agency (FAPESP) | Página centrodememoria.fapesp.br | 3 quotes substrings do snapshot |

## Decisões do proprietário aplicadas

1. `triage-tavily-098`: `needs_review` — beOn Claro é hub, não oportunidade direta
2. `triage-tavily-118`: `out_of_scope` — `X7_NO_ENTERPRISE_PATH` (credenciamento de incubadoras)
3. `triage-tavily-079`: `needs_review` — elegibilidade empresarial na chamada EU-LAC
4. `investidor:kptl`: `needs_review` — sem source_urls ou data de verificação
5. `ict:embrapii:isa-ct`: **removido** — sem identidade verificável

## Casos aguardando revisão

| case_id | Kind | Pendência |
|---|---|---|
| `triage-tavily-079` | opportunity | Elegibilidade empresarial na chamada EU-LAC |
| `triage-tavily-082` | opportunity | FAPESC inacessível — fonte terceira? |
| `triage-tavily-098` | opportunity | beOn Claro é hub — desafio individual? |
| `investidor:kptl` | investor | source_urls e verificado_em ausentes |

## Comandos e resultados

```bash
# Testes completos
PYTHONPATH=src pytest -q tests/unit/test_relevance.py tests/unit/test_relevance_goldens.py tests/unit/test_hardening_pr4.py
# 160 passed

# Ruff
ruff check src/radar/core/eval/relevance_goldens.py src/radar/domain/relevance.py tests/unit/test_relevance.py tests/unit/test_relevance_goldens.py tests/unit/test_hardening_pr4.py
# All checks passed

# Triage inalterado
git diff 656c32362 -- data/evaluation/golden/triage.json
# (vazio)

# Integrity check
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
- datasets versionados (corrigidos após 2 ciclos de auditoria)
- loader hermético com 12 validações
- 160 testes (115 + 37 + 8)

## Auditoria Codex

**Veredito:** `pendente`
