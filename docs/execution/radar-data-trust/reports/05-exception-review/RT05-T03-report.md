# RT05-T03 — Detector Temporal em Shadow

**Data:** 2026-07-29
**Branch:** `codex/radar-data-trust-05-t03`
**Base:** `08a16fd63` (tip of T02)
**Commits:**
- `ac5b43da0` — feat(temporal): detector, fingerprint, best-effort integration
- `263d0f94b` — docs(temporal): T03 report + T02 auditoria aprovada
- `e833879d8` — fix(temporal): preserve raw status and explicit continuous evidence

**Auditoria Codex: pendente**

---

## Resumo

Detector temporal determinístico que conecta o contrato T01
(`evaluate_temporal`, `DataQualityException`) e o repositório T02
(`open_or_observe_exception`) ao fluxo de ingestão gold de editais. Opera
exclusivamente em **shadow**: mede e persiste exceções sem alterar estado
produtivo.

---

## Entregas

### 1. Detector — `src/radar/core/services/temporal_quality.py`

| Função | Comportamento |
|---|---|
| `_build_temporal_fingerprint` | SHA-256 canônico das entradas materiais: deadline, status com `strip().lower()` (vazio → `null`), hashes versionados de evidência ordenados e deduplicados, bundle_hash e producer_version fixo `temporal_quality:v1` |
| `detect_temporal_exception` | Avalia temporalidade via `evaluate_temporal`; somente `continuous_evidence` comprova continuidade; retorna `DataQualityException` apenas se `validity_state == needs_review` |
| `check_edital_temporal_quality` | Wrapper best-effort: chama `detect_temporal_exception` + `open_or_observe_exception`; `DataQualityStorageError` e `ValueError` são logados (categórico) e engolidos |

**Fingerprint** = `sha256:` + SHA-256 de JSON canônico (`sort_keys=True`) com:

```json
{
  "producer_version": "temporal_quality:v1",
  "deadline": "2026-12-31" | null,
  "status": "aberta" | "encerrada" | null,
  "evidence_hashes": ["sha256:..."],
  "bundle_hash": "sha256:..." | null
}
```

As identidades de evidência são exclusivamente valores versionados reais
recebidos em `canonical_content_hash`, `silver_source_hash`, `bundle_hash` e
`content_hash`. Nenhum dado de documento, URL, locator, quote ou hash é
fabricado.

O contrato separa:

- `evidence_refs`: referências genéricas recuperáveis de prazo/status, que são
  preservadas na exceção mas não provam fluxo contínuo;
- `continuous_evidence`: referência explicitamente classificada como evidência
  de continuidade, única entrada encaminhada como tal a `evaluate_temporal`.

Quando uma exceção existe, `continuous_evidence` é incluída nas referências
persistidas uma única vez.

O `as_of` continua injetável. Quando omitido, usa a data corrente em
`America/Sao_Paulo`, conforme a semântica de encerramento da spec.

### 2. Integração — `src/radar/core/kg/gold.py`

```python
# RT05-T03: temporal quality detector (shadow, best-effort)
_check_temporal(
    subject_id=native_id,
    deadline=md["deadline"],
    status=md.get("raw_status", md["status"]),
)
```

Executada:
- **depois** do commit da entidade (`with conn.transaction()` + upsert)
- **depois** de metadata, proveniência e match_chunks conhecidos
- **somente** para `kind=edital`
- **best-effort**: `DataQualityStorageError` não derruba ingestão, não incrementa `stats["errors"]`
- **logs**: apenas `subject_id` e `issue_code` — sem conteúdo bruto
- **no-op** quando Supabase não configurado

`_edital_metadata()` preserva o status bruto apenas na chave transitória
`raw_status`. A entidade, o banco, o payload e `metadata` continuam recebendo
exatamente o status normalizado legado. Na ausência da chave interna, a
integração usa o status normalizado como fallback.

Uma referência baseada em `silver_source_hash`, quando recebida, participa do
fingerprint, mas continua sendo evidência genérica: não constitui evidência de
continuidade temporal.

### 3. Semântica temporal

| Condição | `issue_code` | Abre exceção |
|---|---|---|
| Finep/Eureka (deadline=None, status="aberta", sem evidência) | `temporal_status_without_basis` | Sim |
| Prazo futuro coerente (deadline >= hoje) | — | Não |
| Encerrado coerente (deadline passado, status="encerrada") | — | Não |
| Fluxo contínuo comprovado (evidence_ref oficial) | — | Não |
| ABERTA + deadline passado | `temporal_status_conflict` | Sim |
| Fechado + deadline futuro | `temporal_status_conflict` | Sim |
| deadline + evidência contínua simultâneos | `temporal_status_conflict` | Sim |
| deadline=None + status="fluxo_continuo" sem evidência | `critical_fact_missing` | Sim |

### 4. Testes — `tests/unit/test_temporal_quality_detector.py`

65 testes no arquivo focal. A cobertura adicionada pela correção inclui:

- `ABERTA` bruta + prazo passado e `ENCERRADA` bruta + prazo futuro, ambos
  atravessando a ingestão gold e abrindo `temporal_status_conflict`;
- invariância da entidade gold: status/deadline legados permanecem normalizados
  e `raw_status` não entra na entidade nem em `metadata`;
- evidência genérica sem prazo/status aberto permanece `needs_review`;
- `continuous_evidence` explícita permite `continuous/active`;
- conflito com evidência contínua preserva a referência uma única vez;
- normalização de status no fingerprint, inclusive vazio → `null`;
- `silver_source_hash`, `bundle_hash` e `content_hash` como identidades
  versionadas reais de evidência;
- ordenação e deduplicação das identidades antes do JSON canônico; e
- data default calculada em `America/Sao_Paulo`, com `as_of` ainda injetável.

---

## Validação

```text
ENVIRONMENT=test PYTHONPATH=src pytest -q \
  tests/unit/test_temporal_quality_detector.py \
  tests/unit/test_data_quality_exceptions.py \
  tests/unit/test_temporal_exception_contract.py \
  tests/unit/test_gold_provenance_dualwrite.py \
  tests/unit/test_gold_provenance_sources.py

  → 236 passed

ruff check dos 3 arquivos                                 → pass
git diff --check 08a16fd63..HEAD                          → pass
```

---

## Pontos de verificação (pare se)

1. **Ponto de integração posterior à composição da proveniência** ✓
   `stats["edital"] += 1` ocorre após `build_edital_fact_provenance` e após
   o commit da entidade no `with conn.transaction()`.

2. **Finep/Eureka sem evidência fabricada** ✓
   `evidence_refs` genérica e `continuous_evidence` são canais distintos.
   Nenhuma referência é criada pelo detector; na ausência de evidência real,
   a exceção preserva `[]`.

3. **Sem alteração em schema gold, migration ou consumidores** ✓
   Nenhuma migration nova, nenhuma coluna alterada, nenhum consumidor tocado.

4. **Sem backfill integral** ✓
   O detector opera incrementalmente sobre editais recém-ingestados. Legado
   não é revisitado.

---

## Limitações

1. **Shadow apenas**: consumidores (match, Stage 0, API, frontend, escrita)
   continuam com o comportamento legado. O enforcement será integrado na T07.

2. **Fingerprint não inclui `as_of`**: `as_of` é contexto de avaliação, não
   entrada material. Duas avaliações no mesmo dia com os mesmos deadline/status
   produzem o mesmo fingerprint. Isto é deliberado — evita criar exceções
   duplicadas para a mesma evidência factual.

3. **Sem backfill**: catálogo existente não é revisitado. A T04/T06 pode
   reingestar editais com novos metadados que passem pelo detector.

---

## Histórico de Commits

- `ac5b43da0` — `feat(temporal): detector, fingerprint, best-effort integration`
  Inclui: `temporal_quality.py`, integração em `gold.py`,
  `test_temporal_quality_detector.py`

- `263d0f94b` — `docs(temporal): T03 report + T02 auditoria aprovada`
  Inclui: `RT05-T03-report.md`, atualização `RT05-T02-report.md`

- `e833879d8` —
  `fix(temporal): preserve raw status and explicit continuous evidence`
  Inclui: preservação transitória do status bruto, contrato explícito de
  continuidade, fingerprint versionado, fuso de São Paulo e testes de auditoria.
