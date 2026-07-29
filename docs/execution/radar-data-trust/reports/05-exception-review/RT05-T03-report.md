# RT05-T03 — Detector Temporal em Shadow

**Data:** 2026-07-29
**Branch:** `codex/radar-data-trust-05-t03`
**Base:** `08a16fd63` (tip of T02)
**Commits:**
- `(primeiro)` feat(temporal): detector, fingerprint, best-effort integration
- `(segundo)` docs(temporal): T03 report + T02 auditoria aprovada

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
| `_build_temporal_fingerprint` | SHA-256 canônico das entradas materiais: deadline, status normalizado, evidence_hashes (ordenados), bundle_hash e producer_version fixo `temporal_quality:v1` — JSON canônico + hashlib, sem `hash()` |
| `detect_temporal_exception` | Avalia temporalidade via `evaluate_temporal`; retorna `DataQualityException` somente se `validity_state == needs_review`; nenhum efeito colateral |
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

### 2. Integração — `src/radar/core/kg/gold.py:1225-1231`

```python
# RT05-T03: temporal quality detector (shadow, best-effort)
_check_temporal(
    subject_id=native_id,
    deadline=md["deadline"],
    status=md["status"],
)
```

Executada:
- **depois** do commit da entidade (`with conn.transaction()` + upsert)
- **depois** de metadata, proveniência e match_chunks conhecidos
- **somente** para `kind=edital`
- **best-effort**: `DataQualityStorageError` não derruba ingestão, não incrementa `stats["errors"]`
- **logs**: apenas `subject_id` e `issue_code` — sem conteúdo bruto
- **no-op** quando Supabase não configurado

Evidência: o único `EvidenceRef` disponível no pipeline gold de edital é o
silver source_hash — que não constitui evidência de continuidade temporal.
O detector **não fabrica** `EvidenceRef`, documento, quote, URL, bundle_hash
ou locator.

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

49 testes em 16 grupos:

| Grupo | Count | O quê |
|---|---|---|
| `TestFingerprint` | 8 | Determinístico, difere por deadline/status/evidence/bundle, version fixo, sorted hashes |
| `TestFinepEureka` | 4 | Abre exceção, rerun não duplica, fingerprint novo supersede |
| `TestFutureDeadline` | 3 | deadline futuro não abre, hoje não abre, não persiste |
| `TestClosedDeadline` | 3 | deadline passado + fechado não abre, sem deadline + fechado não abre, não persiste |
| `TestContinuousWithEvidence` | 2 | EvidenceRef recuperável → active, não persiste |
| `TestContinuousWithoutEvidence` | 2 | Sem evidência → needs_review; status textual não constitui evidência |
| `TestConflict` | 4 | deadline futuro + fechado, passado + aberto, evidência + deadline, persistência |
| `TestEvidenceNotFabricated` | 6 | evidence_refs vazio/none preservados, bundle_hash não fabricado |
| `TestStorageFailure` | 4 | StorageError não derruba, não crasha ingest, não vaza mensagem bruta, gold inalterado |
| `TestSupabaseAbsent` | 2 | No-op sem configuração, detect ainda funciona |
| `TestShadowInvariant` | 1 | Detector não altera entities gold |
| `TestFingerprintUniqueness` | 1 | Fingerprint único para inputs diferentes |
| `TestGoldIntegration` | 2 | Detector chamado durante ingest, após upsert |
| `TestProducedValue` | 3 | produced_value = deadline/status/unknown |
| `TestProducerVersion` | 2 | producer_version fixo |
| `TestFingerprintInException` | 3 | Fingerprint presente, determinístico, muda com input |

---

## Validação

```text
ENVIRONMENT=test PYTHONPATH=src pytest -q \
  tests/unit/test_temporal_quality_detector.py \
  tests/unit/test_data_quality_exceptions.py \
  tests/unit/test_temporal_exception_contract.py \
  tests/unit/test_gold_provenance_dualwrite.py \
  tests/unit/test_gold_provenance_sources.py

  → 212 passed

ruff check src/radar/core/services/temporal_quality.py  → pass
ruff check src/radar/core/kg/gold.py                     → pass
ruff check tests/unit/test_temporal_quality_detector.py  → pass
git diff --check                                          → pass
```

---

## Pontos de verificação (pare se)

1. **Ponto de integração posterior à composição da proveniência** ✓
   `stats["edital"] += 1` ocorre após `build_edital_fact_provenance` e após
   o commit da entidade no `with conn.transaction()`.

2. **Finep/Eureka sem evidência fabricada** ✓
   O detector nunca recebe `EvidenceRef` do gold pipeline — o parâmetro
   `evidence_refs` é omitido, resultando em `[]`. A semântica do contrato
   T01 trata `None` como ausência de evidência.

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

3. **Sem detecção de conflito com status bronze raw**: O detector recebe o
   status **já normalizado por gold** (`_normalize_status`), que resolve
   deadline vs. status a favor do deadline. Conflitos entre bronze raw e
   deadline são absorvidos pela normalização e não geram exceção. Se a raw
   disser "ABERTA" e o deadline estiver no passado, gold armazena
   status="encerrada" — e o detector vê um estado consistente. A detecção
   desses conflitos é uma melhoria possível para T07.

4. **Sem backfill**: catálogo existente não é revisitado. A T04/T06 pode
   reingestar editais com novos metadados que passem pelo detector.

---

## Histórico de Commits

- `feat(temporal): detector, fingerprint, best-effort integration`
  Inclui: `temporal_quality.py`, integração em `gold.py`,
  `test_temporal_quality_detector.py`

- `docs(temporal): T03 report + T02 auditoria aprovada`
  Inclui: `RT05-T03-report.md`, atualização `RT05-T02-report.md`
