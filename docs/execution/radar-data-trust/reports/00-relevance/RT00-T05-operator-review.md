# RT00-T05 — Revisão do operador

**Status:** `completed`
**Plano:** [`RT00-T05-operator-review.md`](../../plans/00-relevance/RT00-T05-operator-review.md)
**Branch/base:** `codex/radar-data-trust-00-t05` / `b0a11056d`
**Base de referência:** RT00-T04 concluída em `b0a11056d`

## Commits

| Commit | Assunto |
|---|---|---|
| `543a0f430` | API tipada com Pydantic + normalizador + testes |
| `2e53aff7c` | Frontend: compatibilidade null + labels PT-BR + relatório |
| `50de3e8da` | Remoção de `_ERROR_CANONICAL_MESSAGES` (privado) — uso de `validate_opportunity_result` (público) + 5 novos testes de sanitização |

## Contrato da API

### Response models (Pydantic)

`src/radar/api/routers/discovered.py`:

```python
class DiscoveredItem(BaseModel):
    id: str
    ...
    relevance_status: Literal["unclassified", "classified", "error"] = "unclassified"
    relevance_verdict: RelevanceVerdict | None = None   # reutiliza o domínio
    relevance_error: str | None = None
    relevance_classified_at: str | None = None
    promotion_run: dict[str, Any] | None = None

class DiscoveredListResponse(BaseModel):
    opportunities: list[DiscoveredItem]
```

`response_model=DiscoveredListResponse` no `GET /discovered-opportunities`.

### Normalizador `_normalize_row(row)`

Garantias por estado de entrada:

| Estado de entrada | `relevance_status` saída | `relevance_verdict` | `relevance_error` | Regra |
|---|---|---|---|---|
| Sem campos de relevância | `unclassified` | `null` | `null` | legado |
| `relevance_status` inválido/ausente | `unclassified` | `null` | `null` | segurança |
| `classified` + verdict válido | `classified` | intacto | `null` | preservado |
| `classified` + verdict ausente | `error` | `null` | `contract_violation:` | segurança |
| `classified` + verdict malformado | `error` | `null` | `contract_violation:` | segurança |
| `error` + erro com prefixo conhecido | `error` | `null` | mensagem canônica (sufixo bruto descartado) | sanitização |
| `error` + erro sem prefixo conhecido | `error` | `null` | `contract_violation:` | sanitização |
| `error` + string vazia | `error` | `null` | `contract_violation:` | sanitização |
| `error` + `error=None` | `error` | `null` | `null` | preservado |

A canonicalização usa `validate_opportunity_result` do `relevance_classifier.py` (contrato T04,
API pública). Conteúdo arbitrário, traceback ou stack dump nunca são expostos.

`promotion_run` e campos editoriais não são afetados pela normalização.

## Compatibilidade

- Registro legado sem campos novos → `relevance_status=unclassified`, demais `null`
- Linha classificada → `relevance_status=classified`, `relevance_verdict` preservado
- Linha com erro → `relevance_status=error`, apenas a mensagem sanitizada (nunca conteúdo bruto)
- Linha malformada → normalizada para `error` com `contract_violation`
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

Nenhum desses estados oculta, reordena ou filtra candidatos. A classificação é
informação auxiliar independente do status editorial.

## Progressive disclosure

A seção de classificação fica sempre visível (recolhida por padrão). O badge
aparece no header do card. Ao expandir:

- **`in_scope`** → Critérios confirmados (`reason_codes`) + evidências
- **`out_of_scope`** → Critérios de exclusão (`exclusion_codes`) + evidências
- **`needs_review`** → Informação faltante (`missing_information`) explicitamente + demais códigos
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
| `src/radar/api/routers/discovered.py` | Python | Modelos Pydantic (`DiscoveredItem`, `DiscoveredListResponse`), normalizador `_normalize_row`, `response_model` no endpoint, importa `RelevanceVerdict` do domínio e `validate_opportunity_result` do relevance_classifier; `_canonicalize_error` derivada da API pública |
| `frontend/src/lib/api.ts` | TypeScript | Tipos `RelevanceStatus`, `RelevanceDecision`, `RelevanceEvidence`, `RelevanceVerdict`; 4 campos novos em `DiscoveredOpportunity` |
| `frontend/src/app/discovered/page.tsx` | TypeScript React | Badge + progressive disclosure (sempre renderizado), labels em PT-BR ("Critérios confirmados", "Critérios de exclusão") |
| `tests/unit/test_discovery_api_contract.py` | Python (novo) | 26 testes: `_normalize_row` (legado, classified, error, promotion_run), `list_discovered` (mock), promote/reject independence, auth gate |

## Testes e checks executados

### Testes Python (65 passed)

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/unit/test_discovery_api_contract.py \
  tests/unit/test_relevance_staging.py \
  tests/unit/test_discovery_promotion.py \
  tests/unit/test_admin_gate.py
# 65 passed
```

### Testes novos (`test_discovery_api_contract.py` — 29 testes)

#### Estruturais (4)

| Teste | O que comprova |
|---|---|
| `test_list_cols_includes_relevance_fields` | `_LIST_COLS` contém os 4 campos novos |
| `test_list_cols_preserves_legacy_fields` | `_LIST_COLS` preserva todos os campos legados |
| `test_promote_not_called_by_staging` | promote não referenciado no staging T04 |
| `test_reject_not_called_by_staging` | reject não referenciado no staging T04 |

#### `_normalize_row` — legado (3)

| Teste | O que comprova |
|---|---|
| `test_legacy_row_missing_all_relevance_fields` | Linha SEM os 4 campos → unclassified |
| `test_legacy_row_with_none_status` | relevance_status=None → unclassified |
| `test_legacy_row_with_unknown_status` | relevance_status inválido → unclassified |

#### `_normalize_row` — classified (6)

| Teste | O que comprova |
|---|---|
| `test_classified_in_scope_preserves_verdict` | in_scope completo preservado |
| `test_classified_out_of_scope_preserves_verdict` | out_of_scope preservado |
| `test_classified_needs_review_preserves_verdict` | needs_review preservado |
| `test_classified_without_verdict_normalized_to_error` | classified sem verdict → error contract_violation |
| `test_classified_with_empty_verdict_normalized_to_error` | verdict vazio → error |
| `test_classified_with_malformed_verdict_normalized_to_error` | verdict sem "decision" → error |
| `test_classified_with_invalid_decision_normalized_to_error` | decision inválida → error |

#### `_normalize_row` — error (6)

| Teste | O que comprova |
|---|---|
| `test_error_with_raw_suffix_returns_only_canonical` | 5 prefixos conhecidos + sufixo bruto → só mensagem canônica |
| `test_provider_error_raw_suffix_stripped` | `provider_error: SEGREDO_BRUTO` → canônico, sem o segredo |
| `test_timeout_with_traceback_returns_only_canonical` | `timeout: traceback...` → canônico, sem traceback |
| `test_error_without_prefix_returns_contract_violation` | Erro sem prefixo → contract_violation |
| `test_error_empty_string_returns_contract_violation` | String vazia → contract_violation |
| `test_error_none_preserved` | error=None → permanece None |

#### `_normalize_row` — promotion_run (2)

| Teste | O que comprova |
|---|---|
| `test_promotion_run_preserved` | promotion_run não é removido pela normalização |
| `test_promotion_run_preserved_even_with_error_normalization` | promotion_run sobrevive mesmo quando relevância vira error |

#### `list_discovered` mock (6)

| Teste | O que comprova |
|---|---|
| `test_list_discovered_legacy_row_normalized` | Endpoint retorna legado como unclassified |
| `test_list_discovered_classified_row` | Endpoint retorna classified com verdict intacto |
| `test_list_discovered_malformed_row_normalized` | Endpoint normaliza linha inválida |
| `test_list_discovered_error_arbitrary_normalized` | Endpoint normaliza erro arbitrário |
| `test_list_discovered_preserves_editorial_fields` | Campos editoriais não alterados |
| `test_list_discovered_promotion_run_compatible` | promotion_run convive com relevância |

#### Auth (1)

| Teste | O que comprova |
|---|---|
| `test_auth_remains_admin_gate` | Todos os endpoints têm `AdminUserId` como dependência |

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

**Pendente.** A página não foi aberta em navegador porque o ambiente local
(frontend dev server + backend) não estava disponível durante a execução.
Validou-se apenas:

- TypeScript: `tsc --noEmit` — sem erros
- ESLint: `npm run lint` — sem warnings novos
- Python API: 62 testes passam, cobrindo todos os estados de relevância

Cenários pendentes de validação visual:

1. **classified/in_scope** — badge verde "no escopo" visível; ao expandir,
   mostra "Critérios confirmados" + evidências
2. **classified/out_of_scope** — badge laranja "fora do escopo"; ao expandir,
   mostra "Critérios de exclusão" + evidências
3. **classified/needs_review** — badge âmbar "revisar"; ao expandir, mostra
   "Informação faltante" + demais códigos
4. **error** — badge vermelho "erro de classificação"; ao expandir, mensagem
   sanitizada
5. **unclassified** — badge cinza "não classificado"; ao expandir, explicação
   de registro legado
6. **botões Promover/Rejeitar** — inalterados

Não foi executado LLM real (T05 não muda prompt nem modelo).

## Divergências e limitações

1. **Frontend sem harness unitário:** validação TypeScript via `tsc --noEmit` e
   `npm run lint` apenas; sem testes de componente.
2. **Testes da API usam mock de Supabase:** validam o contrato do endpoint
   Python, mas não testam contra banco real.
3. **QA manual da UI pendente:** não foi possível abrir a página no navegador
   para validação visual dos 6 cenários.
4. **Progressive disclosure sem animação:** expansão/recolhimento é instantânea,
   sem transição CSS — coerente com a simplicidade exigida.
5. **Detalhes de evidência sem truncamento:** `quote` pode ser longo, mas não
   há limite de caracteres no display.
6. *(Resolvido)* O import privado `_ERROR_CANONICAL_MESSAGES` foi substituído
   por `validate_opportunity_result` (API pública) na correção do commit
   `50de3e8da`. Não há mais dependência de símbolo privado.

## Confirmação final

- [x] API: modelos Pydantic `DiscoveredItem`+`DiscoveredListResponse`,
      `response_model` no endpoint, `RelevanceVerdict` reutilizado do domínio
- [x] `_normalize_row` sanitiza: legado→unclassified, erro com/sem prefixo→canônico,
      classified sem verdict→error, combinação inválida→error
- [x] Conteúdo bruto nunca exposto (erro com prefixo conhecido → só mensagem canônica,
      sem o sufixo arbitrário)
- [x] Import privado removido — `validate_opportunity_result` (público) rege a
      canonicalização; testes derivam expectativas da mesma API pública
- [x] UI: badge + progressive disclosure sempre visível, 5 estados, labels PT-BR
- [x] promote/reject continuam sem referência a relevância
- [x] auth administrativa (`AdminUserId`) inalterada
- [x] Nenhuma ação editorial nova, nenhum bloqueio por classificador
- [x] Nenhuma alteração em prompts, taxonomias, goldens ou labels da T03
- [x] Nenhuma alteração em migrations, cache, gold ou promote/reject
- [x] RT00-T06 não foi iniciada
