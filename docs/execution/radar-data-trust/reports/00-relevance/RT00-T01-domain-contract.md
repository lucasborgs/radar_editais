# RT00-T01 — Contrato de domínio

**Status:** `passed`
**Plano:** [`plans/00-relevance/RT00-T01-domain-contract.md`](../../plans/00-relevance/RT00-T01-domain-contract.md)
**Branch/commit-base:** `codex/radar-data-trust-00-t01` / `aa7ac35c3`
**Commits de implementação:** `8a35e19d3` (contrato inicial), `4c32ef89c` (correções pós-auditoria), `9d85ab6a7` (fechamento de invariantes)
**Implementador/modelo:** codex (deepseek-v4-flash-free)

## Realizado

### Commit 1: `8a35e19d3` — contrato inicial

- Criado `src/radar/domain/relevance.py` com tipos puros de domínio:
  - `RelevanceDecision` — enum `in_scope | out_of_scope | needs_review`
  - `InclusionCode` — 5 reason codes de inclusão (`R1_R5`)
  - `ExclusionCode` — 8 reason codes de exclusão (`X1_X8`)
  - `OpportunityReasonCode` — enum unificado (depois removido)
  - `ActorReasonCode` — 8 reason codes provisórios `A1_A8`
  - `ClassificationKind` — 5 kinds
  - `EvidenceSource`, `EvidenceLocator`, `RelevanceEvidence`
  - `RelevanceVerdict`, `ActorVerdict`, `InvestorVerdict`, `IctVerdict`, etc.
  - `CLASSIFIER_VERSION` — constante `radar-data-trust-relevance-v1`
- Criados 49 testes iniciais

### Commit 2: `4c32ef89c` — correções pós-auditoria

**Remoções (decisões indevidas):**
- `ActorReasonCode` (`A1_A8`) removido — taxonomia de atores adiada para RT00-T02
- `OpportunityReasonCode` (enum unificado duplicado) removido
- Sufixo `.actor-v1` removido de `ActorVerdict.classifier_version`
- `is_inclusion_code` / `is_exclusion_code` corrigidos: verificam pertencimento real ao enum

**Correções estruturais:**
- `model_config = {"extra": "forbid"}` em todos os modelos (campos extras rejeitados)
- `RelevanceEvidence.code` tipado como `InclusionCode | ExclusionCode`
- `EvidenceSource` estendido: `official_page` e `curated_record`
- `EvidenceLocator.page` validado: rejeita 0 e negativos (1-based)
- `ActorEvidence` separado de `RelevanceEvidence` — `code: str` até RT00-T02

**Invariantes (model validators em `RelevanceVerdict`):**
- `in_scope` exige todos os 5 `InclusionCode` e `exclusion_codes` vazio
- `out_of_scope` exige ao menos 1 `ExclusionCode` e consistência com `reason_codes`
- `needs_review` sem validação extra

**Serialização de atores:**
- `ActorVerdict.kind: ClassificationKind` obrigatório
- Subtipos fixam `kind` como default; round-trip JSON preserva o discriminador

### Commit 3: `9d85ab6a7` — fechamento de invariantes

**Invariantes reforçados:**
- `in_scope` agora rejeita **ExclusionCode em reason_codes** (não apenas exclusion_codes vazio)
- `out_of_scope` agora exige que o **conjunto de ExclusionCode em reason_codes seja exatamente igual** a exclusion_codes (nem mais, nem menos)

**Discriminador dos atores com Literal:**
- `InvestorVerdict.kind: Literal[ClassificationKind.INVESTOR]` — `kind="ict"` falha em validação
- Mesmo padrão para `IctVerdict`, `ProgramVerdict`, `AgencyVerdict`
- Criada `ActorVerdictUnion` = união discriminada dos 4 subtipos
- Exportado `actor_verdict_adapter` (`TypeAdapter`) que serializa/desserializa preservando o subtipo concreto

**Testes (87 no total, +14 em relação ao commit anterior):**
- `test_in_scope_x_in_reason_codes_rejected` — X em reason_codes de in_scope falha
- `test_out_of_scope_extra_x_in_reason_codes_rejected` — X extra em reason_codes falha
- `test_wrong_kind_rejected` para cada subtipo de ator
- `TestActorVerdictDiscriminatedUnion` com 7 testes de TypeAdapter

## Divergências e decisões

- **ActorReasonCode A1_A8 e sufixo `.actor-v1` foram decisões indevidas.** O proprietário determinou que a taxonomia de reason codes de atores deve ser derivada dos casos reais em RT00-T02. Ambos foram removidos.
- `InclusionCode | ExclusionCode` como tipo de `reason_codes` no `RelevanceVerdict`: necessário para permitir que exclusion codes também figurem em `reason_codes`, mantendo a consistência entre os dois campos sem perder a tipagem.
- `ActorEvidence.code: str` é propositalmente livre até RT00-T02 definir a taxonomia.
- Nenhuma decisão de produto foi reinterpretada. Invariantes seguem estritamente a Spec §7.2.

## Dados e migrations

- Não aplicável. Nenhuma migration, tabela, banco, API, frontend ou prompt foi alterado.

## Validação

| Comando/verificação | Resultado |
|---|---|
| Comando/verificação | Resultado |
|---|---|---|
| `pytest tests/unit/test_relevance.py` | 87 passed |
| `pytest tests/unit/test_hardening_pr4.py` (triagem) | 16 passed |
| `ruff check src/radar/domain/relevance.py src/radar/domain/__init__.py tests/unit/test_relevance.py` | All checks passed |
| Import `radar.domain.relevance` | ok |
| Import `radar.core.eval.triage` (compatibilidade) | ok |

## Pendências

- Nenhuma.

## Auditoria Codex

**Veredito:** `pendente`
