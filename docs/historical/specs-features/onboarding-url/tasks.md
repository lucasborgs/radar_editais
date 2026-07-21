# Tasks: Onboarding por URL

## T1 — `core/ingestion/profile_extractor.py` (backend)
**O quê:** Novo módulo com `ProfileExtractor.extract(url)`.
- Adiciona `fetch_text(url) -> str` ao `LiveFetcher` (texto limpo, sem seções)
- `ProfileExtractor` chama `fetch_text` + LLM com o prompt de extração
- Retorna `ExtractResult(profile: CompanyProfile, confidence: dict, source_title: str, error: str | None)`
- Sem LLM disponível → retorna EMPTY_PROFILE + `error="llm_unavailable"`

**Onde:** `core/ingestion/profile_extractor.py`, `core/live_fetcher.py`
**Done when:** `ProfileExtractor().extract("https://exemplo.com.br")` retorna profile com ≥1 campo preenchido para um site real

---

## T2 — `POST /profile/extract` (backend)
**O quê:** Novo endpoint no `backend/api.py`.
- Valida URL (scheme http/https obrigatório)
- Chama `ProfileExtractor().extract(url)`
- Retorna `{ profile, confidence, source_title, low_confidence: bool }`
- Timeout: 15s (via `asyncio.wait_for` ou `requests` timeout)

**Onde:** `backend/api.py`
**Depende de:** T1
**Done when:** `curl -X POST /profile/extract -d '{"url":"..."}' ` retorna JSON válido com profile

---

## T3 — tipos e API client (frontend)
**O quê:** Novos tipos e função no cliente.
- `types/profile.ts`: `FieldConfidence`, `ExtractProfileResponse`
- `lib/api.ts`: `extractProfileFromUrl(url)`

**Onde:** `frontend/src/types/profile.ts`, `frontend/src/lib/api.ts`
**Done when:** TypeScript compila sem erros

---

## T4 — tela URL-input no onboarding (frontend)
**O quê:** Novo estado inicial `"url-input"` no `OnboardingPage`.
- Input de URL + botão "Extrair perfil"
- Link "Prefiro preencher manualmente →" (pula para step 0 do wizard)
- Loading skeleton (≤15s com countdown visual)
- Em sucesso: popula `profile` state + avança para step 0 (revisão)
- Em falha / low_confidence: banner com opções (tentar novamente ou preencher manualmente)

**Onde:** `frontend/src/app/onboarding/page.tsx`
**Depende de:** T3
**Done when:** colar URL válida → wizard abre com campos pré-preenchidos; URL inválida → erro limpo

---

## T5 — indicadores de confiança nos campos (frontend)
**O quê:** Componente `Field` recebe prop `confidence?: FieldConfidence`.
- `"missing"` + campo obrigatório → label âmbar + ícone ⚠
- `"high"` → ícone ✓ verde sutil
- Ausente (modo wizard normal) → sem indicador

**Onde:** `frontend/src/app/onboarding/page.tsx`
**Depende de:** T4
**Done when:** campos obrigatórios não extraídos destacados visualmente; campos extraídos com ✓

---

## Ordem de execução

```
T1 → T2 (sequencial, backend)
T3 → T4 → T5 (sequencial, frontend)
T2 e T3 podem rodar em paralelo (backend e frontend independentes até T4)
```
