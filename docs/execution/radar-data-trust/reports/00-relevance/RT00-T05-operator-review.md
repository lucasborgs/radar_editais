# RT00-T05 — Revisão do operador

**Status:** `completed`
**Plano:** [`RT00-T05-operator-review.md`](../../plans/00-relevance/RT00-T05-operator-review.md)
**Branch/base:** `codex/radar-data-trust-00-t05` / `b0a11056d`
**Base de referência:** RT00-T04 concluída em `b0a11056d`

## Commits

| Commit | Assunto |
|---|---|
| `34859e351` | API: adiciona relevance_status/verdict/error/classified_at à _LIST_COLS |
| `34859e351` | Frontend: tipos RelevanceStatus/RelevanceVerdict + badge + progressive disclosure |
| `34859e351` | Testes: contrato da API (10 testes comportamentais + estruturais) |

## Contrato da API

### Colunas adicionadas a `_LIST_COLS`

`src/radar/api/routers/discovered.py:46`:

```
relevance_status, relevance_verdict, relevance_error, relevance_classified_at
```

### Payload de listagem

```json
{
  "opportunities": [
    {
      "...campos legados...",
      "relevance_status": "unclassified | classified | error",
      "relevance_verdict": {
        "decision": "in_scope | out_of_scope | needs_review",
        "reason_codes": ["R1_ENTERPRISE_PATH", ...],
        "exclusion_codes": ["X1_ACADEMIC_ONLY", ...],
        "evidence": [
          {
            "code": "R1_ENTERPRISE_PATH",
            "quote": "...",
            "source": "landing_page",
            "locator": {"document": "...", "page": 3}
          }
        ],
        "missing_information": ["..."],
        "classifier_version": "radar-data-trust-relevance-v1"
      } | null,
      "relevance_error": "timeout: ..." | null,
      "relevance_classified_at": "2026-07-21T12:00:00Z" | null
    }
  ]
}
```

### Compatibilidade

- Registro legado sem campos novos → `relevance_status=unclassified`, demais `null`
- Linha classificada → `relevance_status=classified`, `relevance_verdict` preservado
- Linha com erro → `relevance_status=error`, apenas a mensagem sanitizada (nunca conteúdo bruto)
- `promotion_run` e campos editoriais (`status`, `reviewed_at`, etc.) permanecem no payload
- Promote/reject ignoram colunas de relevância
- Auth administrativa (`AdminUserId`) permanece como gate em todos os endpoints

## Mapeamento dos cinco estados visuais

| Estado `relevance_status` | `relevance_verdict.decision` | Badge na UI | Cor |
|---|---|---|---|
| `classified` | `in_scope` | "no escopo" | verde (`bg-green-100 text-green-700`) |
| `classified` | `out_of_scope` | "fora do escopo" | laranja (`bg-orange-100 text-orange-700`) |
| `classified` | `needs_review` | "revisar" | âmbar (`bg-amber-100 text-amber-700`) |
| `unclassified` | `null` | "não classificado" | cinza (`bg-gray-100 text-gray-500`) |
| `error` | `null` | "erro de classificação" | vermelho (`bg-red-100 text-red-700`) |

Nenhum desses estados oculta, reordena ou filtra candidatos. A classificação é informação auxiliar independente do status editorial.

## Progressive disclosure

A seção de classificação fica recolhida por padrão. Ao expandir, mostra:

- **`in_scope`** → `reason_codes` + evidências
- **`out_of_scope`** → `exclusion_codes` + evidências
- **`needs_review`** → `missing_information` explicitamente + demais códigos
- **`error`** → apenas a mensagem sanitizada (`relevance_error`)
- **`unclassified`** → explicação: "Registro legado ou ainda não processado pelo classificador de relevância."

Evidências são exibidas com `code`, `quote`, `source` e `locator` quando existem.

Não há modal, nova rota, filtro ou ação associada.

## Ações editoriais inalteradas

- `POST /discovered-opportunities/{id}/promote` — mesma implementação, sem referência a colunas de relevância
- `POST /discovered-opportunities/{id}/reject` — mesma implementação, sem referência a colunas de relevância
- `POST /discovered-opportunities/{id}/promotion/retry` — mesma implementação
- `PATCH /discovered-opportunities/{id}/edital-link` — mesma implementação

Nenhuma ação editorial foi criada, alterada ou bloqueada pelo classificador.

## Arquivos alterados

| Arquivo | Tipo | Alteração |
|---|---|---|
| `src/radar/api/routers/discovered.py` | Python | Adiciona 4 colunas de relevância a `_LIST_COLS` (1 linha alterada) |
| `frontend/src/lib/api.ts` | TypeScript | Adiciona tipos `RelevanceStatus`, `RelevanceDecision`, `RelevanceEvidence`, `RelevanceVerdict`; adiciona 4 campos a `DiscoveredOpportunity` |
| `frontend/src/app/discovered/page.tsx` | TypeScript React | Adiciona `relevanceBadge()`, `relevanceDetails()`, estado `expandedRelevance`, rendering do badge no header, seção expansível de classificação |
| `tests/unit/test_discovery_api_contract.py` | Python (novo) | 10 testes do contrato da API: estruturais (colunas, promote/reject independence) + comportamentais (veredicto, legado, erro, promotion_run) |

## Testes e checks executados

### Testes Python (46 passed)

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/unit/test_discovery_api_contract.py \
  tests/unit/test_relevance_staging.py \
  tests/unit/test_discovery_promotion.py \
  tests/unit/test_admin_gate.py
# 46 passed
```

### Testes novos (`test_discovery_api_contract.py` — 10 testes)

| Teste | Tipo | O que comprova |
|---|---|---|
| `test_list_cols_includes_relevance_fields` | estrutural | `_LIST_COLS` contém os 4 campos novos |
| `test_list_cols_preserves_legacy_fields` | estrutural | `_LIST_COLS` preserva todos os campos legados |
| `test_promote_endpoint_unchanged_source` | estrutural | promote não referencia colunas de relevância |
| `test_reject_endpoint_unchanged_source` | estrutural | reject não referencia colunas de relevância |
| `test_classified_row_preserves_verdict` | comportamental | verdict intacto na resposta da listagem |
| `test_legacy_row_is_unclassified` | comportamental | registro legado → `unclassified` |
| `test_error_row_preserves_sanitized_message` | comportamental | erro sem conteúdo bruto |
| `test_promotion_run_compatible_with_relevance` | comportamental | `promotion_run` convive com campos novos |
| `test_include_reviewed_still_works` | comportamental | filtro `include_reviewed` compatível |
| `test_auth_remains_admin_gate` | estrutural | todos os endpoints têm `AdminUserId` |

### Ruff

```bash
.venv/bin/ruff check src/radar/api/routers/discovered.py \
  tests/unit/test_discovery_api_contract.py
# All checks passed
```

### TypeScript

```bash
cd frontend && npx tsc --noEmit
# (no output — OK)

cd frontend && npm run lint
# Only pre-existing warnings in auth.tsx (not our code)
```

### `git diff --check`

Limpo (sem whitespace errors).

## QA manual direcionado

Testes de UI (6 cenários):

1. **classified/in_scope** — badge verde "no escopo" visível; ao expandir, mostra reason_codes + evidências
2. **classified/out_of_scope** — badge laranja "fora do escopo"; ao expandir, exclusion_codes + evidências
3. **classified/needs_review** — badge âmbar "revisar"; ao expandir, missing_information + reason_codes
4. **error** — badge vermelho "erro de classificação"; ao expandir, mensagem sanitizada
5. **unclassified** — badge cinza "não classificado"; ao expandir, mensagem de registro legado
6. **botões Promover/Rejeitar** — inalterados, independentes da classificação

Não foi executado LLM real (T05 não muda prompt nem modelo).

## Divergências e limitações

1. **Frontend sem harness unitário:** validação TypeScript via `tsc --noEmit` e `npm run lint` apenas; sem testes de componente.
2. **Testes da API usam mock de Supabase:** validam o contrato do endpoint Python, mas não testam contra banco real.
3. **Progressive disclosure sem animação:** expansão/recolhimento é instantânea, sem transição CSS — coerente com a simplicidade exigida.
4. **Detalhes de evidência sem truncamento:** `quote` pode ser longo, mas não há limite de caracteres no display.

## Confirmação final

- [x] API: 4 campos novos em `_LIST_COLS`, payload tipado, compatibilidade com legados
- [x] UI: badge + progressive disclosure, 5 estados visuais, sem ocultar/reordenar/filtrar
- [x] promote/reject continuam sem referência a relevância
- [x] auth administrativa (`AdminUserId`) inalterada
- [x] Nenhuma ação editorial nova, nenhum bloqueio por classificador
- [x] Nenhuma alteração em prompts, taxonomias, goldens ou labels da T03
- [x] Nenhuma alteração em migrations, cache, gold ou promote/reject
- [x] RT00-T06 não foi iniciada
