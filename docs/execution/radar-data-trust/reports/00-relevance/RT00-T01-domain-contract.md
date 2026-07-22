# RT00-T01 — Contrato de domínio

**Status:** `passed`
**Plano:** [`plans/00-relevance/RT00-T01-domain-contract.md`](../../plans/00-relevance/RT00-T01-domain-contract.md)
**Branch/commit-base:** `codex/radar-data-trust-00-t01` / `aa7ac35c3`
**Commits:** `02f98aa50`
**Implementador/modelo:** codex (deepseek-v4-flash-free)

## Realizado

- Criado `src/radar/domain/relevance.py` com tipos puros de domínio:
  - `RelevanceDecision` — enum `in_scope | out_of_scope | needs_review`
  - `InclusionCode` — 5 reason codes de inclusão (`R1`–`R5`)
  - `ExclusionCode` — 8 reason codes de exclusão (`X1`–`X8`)
  - `OpportunityReasonCode` — enum unificado (todos R + X)
  - `ActorReasonCode` — 8 reason codes próprios para atores (`A1`–`A8`)
  - `ClassificationKind` — 5 kinds: `opportunity`, `investor`, `ict`, `program`, `agency`
  - `EvidenceSource` — `landing_page | edital | anexo`
  - `EvidenceLocator` — documento + página
  - `RelevanceEvidence` — code, quote, source, locator
  - `RelevanceVerdict` — output canônico da Spec §7.1
  - `ActorVerdict` — base para atores, sem `exclusion_codes`
  - `InvestorVerdict`, `IctVerdict`, `ProgramVerdict`, `AgencyVerdict` — subtipos
  - `CLASSIFIER_VERSION` — constante `radar-data-trust-relevance-v1`
  - Utilitários `is_inclusion_code` / `is_exclusion_code`
- Atualizado `src/radar/domain/__init__.py` para exportar os novos tipos
- Criado `tests/unit/test_relevance.py` com 49 testes:
  - valores de enum e serialização string
  - round-trip JSON (pydantic) para todos os modelos
  - rejeição de estados inválidos
  - reason codes completos conforme spec
  - separação oportunidade × ator (kinds disjuntos, verdicts distintos)
  - constante versionada

## Divergências e decisões

- Nenhuma. Tudo segue o contrato lógico da Spec §7.1.
- `ActorVerdict` não possui `exclusion_codes` — atores têm reason codes próprios (`A1`–`A8`), sem o conceito de exclusão por `X1`–`X8` que é específico de oportunidades.
- A versão do classificador de atores é `radar-data-trust-relevance-v1.actor-v1` (sufixo `actor-v1` adicionado para evitar conflito com o classificador de oportunidades).

## Dados e migrations

- Não aplicável. Task puramente de tipos de domínio; nenhuma migration, tabela ou banco foi tocado.

## Validação

| Comando/verificação | Resultado |
|---|---|
| `pytest tests/unit/test_relevance.py -v` | 49 passed |
| `pytest tests/unit/test_hardening_pr4.py -v` (triagem) | 16 passed (compatibilidade mantida) |
| `ruff check src/radar/domain/relevance.py src/radar/domain/__init__.py tests/unit/test_relevance.py` | All checks passed |
| `python3 -c "from radar.domain.relevance import RelevanceVerdict"` | domain ok |
| `python3 -c "from radar.core.eval.triage import SUITE"` | triage suite imports ok |

## Pendências

- Nenhuma. RT00-T01 está completa e dentro do escopo.

## Auditoria Codex

**Veredito:** `pendente`
