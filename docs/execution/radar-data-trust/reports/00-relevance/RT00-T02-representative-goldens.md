# RT00-T02 — Goldens representativos

**Status:** `awaiting_owner_review`
**Plano:** [`plans/00-relevance/RT00-T02-representative-goldens.md`](../../plans/00-relevance/RT00-T02-representative-goldens.md)
**Branch/commit-base:** `codex/radar-data-trust-00-t02` / `656c32362`
**Commits de implementação:** (pendente — 2 commits a criar)

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

Criados 7 arquivos em `data/evaluation/golden/relevance/`:

| Arquivo | Casos | Decisões |
|---|---|---|
| `manifest.json` | 15 IDs referenciados | revisão pendente |
| `opportunities.json` | 7 | 2 in_scope, 3 out_of_scope, 2 needs_review |
| `investors.json` | 2 | 1 in_scope, 1 needs_review |
| `icts.json` | 2 | 2 needs_review |
| `programs.json` | 2 | 2 in_scope |
| `agencies.json` | 2 | 2 in_scope |
| `actor_sources.json` | 7 snapshots | hashes SHA-256 verificados |

**Total:** 15 casos, 3 estados no corpus total (5 in_scope, 3 out_of_scope, 7 needs_review).

`triage.json` permanece byte a byte idêntico (122 casos booleanos, lido pela suíte `triage`).

### 3. Hermetic loader

Criado `src/radar/core/eval/relevance_goldens.py` — classe `RelevanceGoldenLoader` que:
- Carrega os 5 datasets + manifest
- Valida tipos (veredictos via `RelevanceVerdict.model_validate` e `actor_verdict_adapter`)
- Valida unicidade de case_id
- Valida correspondência kind/arquivo
- Valida IDs do manifesto contra datasets
- Valida hashes SHA-256 dos snapshots em `actor_sources.json`
- Expõe distribuição por kind e decision
- **Sem rede, banco, LLM ou arquivos não versionados**

### 4. Testes

| Teste | Status |
|---|---|
| `tests/unit/test_relevance.py` (148 tests) | all passed |
| `tests/unit/test_relevance_goldens.py` (17 tests) | all passed |
| `tests/unit/test_hardening_pr4.py` (16 tests) | all passed |
| `ruff check` (5 arquivos) | all checks passed |
| `git diff --check` | clean |
| `git diff 656c32362 -- triage.json` | vazio |

Testes novos incluem: parse/round-trip dos códigos por kind, rejeição cross-kind, invariantes de ator in_scope, loader hermético, unicidade/completude do manifesto, integridade de hashes, distribuição tri-state, manutenção do triage legado.

## Source-evidence utilizada

| Kind | Fonte de evidência | Limitação |
|---|---|---|
| Opportunity | `triage.json` (legado, 1500-char truncado) | Quotes extraídos de title/snippet; conteúdo truncado |
| Investor | `data/silver/investidores.json` (curated_record) | Sem verificação de página oficial — curado, não validado |
| Program | `data/silver/programas.json` (curated_record) | idem |
| ICT | Conhecimento público (official_page) | Bronze efêmero não versionado; sem snapshot oficial verificado |
| Agency | `docs/domain/schema.md` + `docs/domain/sources/*.md` (official_page) | Schema-driven, sem entidade gold verificada |

## Decisões do proprietário aplicadas

1. Hubs como beOn/Tupy são fontes de descoberta, não oportunidades publicáveis → `triage-tavily-098`: `needs_review` com nota
2. `triage-tavily-118`: `X7_NO_ENTERPRISE_PATH` (credenciamento de incubadoras)
3. ICT golden sem artifact versionado → `needs_review` para ambos os casos
4. Ticket ausente não bloqueia `in_scope` → `indicator-capital`: `in_scope` com ticket_range em `missing_information`
5. Vigência e relevância separadas → nenhum X code por encerramento

## Limitações

- **ICT golden todo em needs_review:** dados EMBRAPII são efêmeros (bronze não versionado). A autorização do proprietário para coleta de snapshots oficiais não foi exercida porque as páginas oficiais não estavam acessíveis durante esta task.
- **Quotes de oportunidade truncados:** o conteúdo do `triage.json` é limitado a 1500 caracteres. `triage-tavily-093` (MCTI R$300mi) tem conteúdo de nav HTML que não serve como quote — a evidência veio do title.
- **Investidor indicator-capital sem verificação de página oficial:** o `site` e `source_urls` do registro curado são usados como evidência, mas não há snapshot verificado.
- **Agências usam schema.md como evidência:** a identidade e mandato de inovação vêm de documentação do repositório, não de página oficial coletada.

## Casos aguardando revisão

| case_id | Kind | Pendência |
|---|---|---|
| `triage-tavily-079` | opportunity | Elegibilidade empresarial na chamada EU-LAC — precisa de decisão do proprietário |
| `triage-tavily-098` | opportunity | beOn Claro é hub — precisa de identificação de desafio individual concreto |
| `investidor:kptl` | investor | Faltam source_urls e data de verificação — precisa de verificação de página oficial |
| `ict:embrapii:senai-cimatec` | ict | Dados de conhecimento público sem snapshot oficial — precisa de coleta |
| `ict:embrapii:isa-ct` | ict | Sem evidência alguma — caso ilustrativo de insuficiência |

## Comandos e resultados

```bash
pytest tests/unit/test_relevance.py tests/unit/test_relevance_goldens.py tests/unit/test_hardening_pr4.py
# 148 passed
ruff check src/radar/domain/relevance.py src/radar/domain/__init__.py src/radar/core/eval/relevance_goldens.py tests/unit/test_relevance.py tests/unit/test_relevance_goldens.py
# All checks passed
git diff --check
# clean
git diff 656c32362 -- data/evaluation/golden/triage.json
# empty
```

## RT00-T03 não iniciada

Nenhum classificador, prompt, staging, API ou alteração de runtime foi implementada. Esta task produziu apenas:
- tipos de domínio (enums + validação)
- datasets versionados
- loader hermético
- testes proporcionais

## Auditoria Codex

Pendente.
