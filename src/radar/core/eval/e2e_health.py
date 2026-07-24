"""
Radar Data Trust 02 — Sinal E2E `e2e_health` (RT02-T04, spec
docs/specs/radar-data-trust-02-quality-gates.md §7.2).

Caminho mínimo e determinístico descoberta→gold→consumo: um fato CONHECIDO
(o requisito "A duração máxima de cada projeto será de 2 (dois) anos.",
verbatim no silver do edital finep:602 — mesma fixture usada por
`tests/unit/test_gold_provenance_dualwrite.py`) atravessa:

  1. **descoberta/estrutura** — o silver já materializado da fixture
     (`tests/fixtures/gold_equivalence/silver/structured_docs/finep/602.jsonl`),
     tratado como o produto já discovered+structured que o gold consome;
  2. **gold** — o ingest REAL (`radar.core.kg.gold.ingest_all`, restrito ao
     edital finep:602), sob os seams herméticos de infraestrutura (banco,
     tagger LLM, constraints producer, embeddings) já construídos por
     `tests/helpers/gold_projection.py` (RT01-T02) — reusados aqui via
     import, NÃO duplicados. Este módulo só sobrepõe LOCALMENTE
     `stub_produce_from_text` (mesmo padrão de subclasse local usado em
     `tests/unit/test_gold_provenance_dualwrite.py`), porque o stub
     compartilhado devolve um texto fixo que não é verbatim nesta fixture —
     correto para o gate de equivalência estrutural do T02, mas incapaz de
     provar aqui a sobrevivência de um fato `stated`;
  3. **consumo** — a leitura pública de proveniência
     (`radar.core.kg.provenance_read.public_provenance`, RT01-T10: a mesma
     função que um consumidor real — API/Explore — chamaria), mais uma
     re-resolução INDEPENDENTE do mesmo quote contra o silver cru via
     `radar.core.kg.evidence_resolver.resolve_quote`, para confirmar que a
     coordenada exposta ao consumidor não divergiu da gravada pelo gold.

Sinais diagnósticos agregados (booleans/contagens, SEM threshold): as
camadas conectaram, o fato sobreviveu com state=stated, a citação pública
preserva o quote e a coordenada. Uma camada que não conecta é um sinal
REPORTADO (o agregado registra `False`/`None`), nunca mascarado ou
contornado — nenhum try/except amplo esconde uma desconexão real; só o
`prereqs` evita falha obscura quando falta um artefato local (fixture ou o
harness de captura).

Determinismo: SEM LLM real, SEM rede, SEM banco real, SEM prod — os únicos
seams de infra tocados pelo ingest gold são interceptados pelos stubs do
harness T02 (vetores sintéticos, tagger fixo, constraints fixo, DB
in-memory). `classification="diagnostic"`, `criteria=()` — nenhum threshold,
nenhum gate.

Uso:
    python -m radar.core.eval run e2e_health
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite

FIXTURES_DIR = ROOT / "tests" / "fixtures" / "gold_equivalence"
FINEP_SILVER_DIR = FIXTURES_DIR / "silver" / "structured_docs" / "finep"
FINEP_JSONL = FINEP_SILVER_DIR / "602.jsonl"
FINEP_META = FINEP_SILVER_DIR / "602.meta.json"
GOLD_CAPTURE_HELPER = ROOT / "tests" / "helpers" / "gold_projection.py"

FINEP_SOURCE = "finep"
FINEP_STEM = "602"
FINEP_EDITAL_ID = f"{FINEP_SOURCE}:{FINEP_STEM}"
FINEP_ENTITY_KEY = f"edital|{FINEP_SOURCE}|{FINEP_EDITAL_ID}"
REQUISITO_PATH = "requisitos_texto.0"

KNOWN_QUOTE = "A duração máxima de cada projeto será de 2 (dois) anos."
KNOWN_DOCUMENT = "Edital.pdf"
KNOWN_PAGE = 5

_CASE_ID = "e2e-gold-consumo-finep-602"


# =============================================================================
# Leitura direta (pura) do silver — usada só para a re-resolução independente
# =============================================================================


def _read_blocks(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _silver_hash_ref() -> str | None:
    if not FINEP_META.exists():
        return None
    try:
        raw = json.loads(FINEP_META.read_text(encoding="utf-8")).get("source_hash")
    except (json.JSONDecodeError, OSError):
        return None
    return f"md5:{raw}" if raw else None


# =============================================================================
# Load data — 1 caso mínimo (sinal, não matriz)
# =============================================================================


def load_data() -> list[dict]:
    return [
        {
            "input": {"source": FINEP_SOURCE, "edital_id": FINEP_EDITAL_ID, "quote": KNOWN_QUOTE},
            "expected_output": {
                "state": "stated",
                "locator_quality": "exact",
                "document": KNOWN_DOCUMENT,
                "page": KNOWN_PAGE,
            },
            "metadata": {"case_id": _CASE_ID},
        }
    ]


# =============================================================================
# Camada gold — ingest real, hermético (reusa o harness de captura do T01/T02)
# =============================================================================


def _run_gold_capture() -> tuple[dict[str, dict], dict]:
    """Roda `radar.core.kg.gold.ingest_all` de verdade, restrito ao edital
    finep:602, sob os stubs herméticos de `tests/helpers/gold_projection.py`
    (mesma técnica documentada no próprio módulo para uso fora de pytest —
    ver `regenerate_baseline`). Retorna `(entity_provenance_por_chave,
    stats)`. Import tardio (dentro da função) para não acoplar o import-time
    do pacote `radar.core.eval` a `tests/`."""
    import pytest

    from radar.core.kg import equivalence, gold
    from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

    class _KnownFactHarness(GoldCaptureHarness):
        """Subclasse LOCAL desta suíte (mesmo padrão de
        `test_gold_provenance_dualwrite.py::_ProvenanceCapturingHarness`):
        reusa toda a lógica de correlação/projeção da classe base e só
        adiciona a captura de `provenance` por entidade + um requisito FIXO
        e verbatim no silver (o stub compartilhado usa outro texto, correto
        para o gate de equivalência do T02, mas não verbatim nesta
        fixture)."""

        def __init__(self) -> None:
            super().__init__()
            self.entity_provenance: dict[str, dict] = {}

        def stub_upsert_entity(self, cur: Any, **f: Any) -> str:
            synthetic_id = super().stub_upsert_entity(cur, **f)
            key = self._id_to_key[synthetic_id]
            self.entity_provenance[key] = f.get("provenance") or {}
            return synthetic_id

        def stub_produce_from_text(
            self, text: str, *, client: Any = None, model: str | None = None,
        ) -> tuple[list[dict], list[str], list[str], list[str]]:
            self._constraints_queue.append(equivalence.sha256_of(text))
            return ([], [KNOWN_QUOTE], [], [])

    harness = _KnownFactHarness()
    with pytest.MonkeyPatch.context() as mp:
        if not os.environ.get("OPENAI_API_KEY"):
            # Fora de um teste pytest o autouse `_ensure_llm_keys` do
            # conftest não roda; o client OpenAI só é CONSTRUÍDO (nunca
            # chamado — `_tag_edital` está stubado), então uma chave dummy
            # basta (mesmo padrão de `gold_projection.regenerate_baseline`).
            mp.setenv("OPENAI_API_KEY", "test-dummy-openai-api-key")
        harness.apply_patches(mp, DEFAULT_FIXTURES_DIR)
        stats = gold.ingest_all(sources=["edital"], edital_ids=[FINEP_EDITAL_ID], skip_unchanged=True)
    return harness.entity_provenance, dict(stats)


# =============================================================================
# Task — descoberta(silver)→gold(ingest real)→consumo(leitura pública + re-resolução)
# =============================================================================


def _run_e2e(*, item: dict) -> dict:  # noqa: ARG001 — item é fixo (1 caso mínimo)
    from radar.core.kg import provenance_read
    from radar.core.kg.evidence_resolver import resolve_quote

    entity_provenance, stats = _run_gold_capture()
    gold_ran = True
    edital_ingested = int(stats.get("edital", 0))

    entity_prov = entity_provenance.get(FINEP_ENTITY_KEY) or {}
    fact = entity_prov.get(REQUISITO_PATH)
    known_fact_present = fact is not None
    known_fact_state = fact.get("state") if isinstance(fact, dict) else None
    known_fact_stated = known_fact_state == "stated"

    consumption_view = provenance_read.public_provenance(entity_prov)
    consumption_entry = consumption_view.get(REQUISITO_PATH)
    consumption_present = consumption_entry is not None
    citations = list((consumption_entry or {}).get("citations") or [])
    citation = citations[0] if citations else None

    blocks = _read_blocks(FINEP_JSONL)
    independent = resolve_quote(
        KNOWN_QUOTE, blocks, source=FINEP_SOURCE, edital_id=FINEP_EDITAL_ID,
        native_id=FINEP_STEM, silver_source_hash=_silver_hash_ref(),
    )
    independent_ref = independent.evidence_ref

    quote_survives = bool(citation) and citation.get("quote") == KNOWN_QUOTE
    coordinates_match = bool(
        citation is not None and independent_ref is not None
        and citation.get("document") == independent_ref.document
        and citation.get("page") == independent_ref.page
    )

    layers_connected = bool(
        gold_ran and edital_ingested >= 1 and known_fact_present
        and known_fact_stated and consumption_present and quote_survives
        and coordinates_match
    )

    return {
        "gold_ran": gold_ran,
        "edital_ingested": edital_ingested,
        "known_fact_present": known_fact_present,
        "known_fact_state": known_fact_state,
        "known_fact_stated": known_fact_stated,
        "consumption_present": consumption_present,
        "citation_count": len(citations),
        "citation_quote": citation.get("quote") if citation else None,
        "quote_survives": quote_survives,
        "independent_locator_quality": (
            independent_ref.locator_quality.value if independent_ref else None
        ),
        "coordinates_match": coordinates_match,
        "layers_connected": layers_connected,
        "stats": stats,
    }


# =============================================================================
# Evaluators — 1 Evaluation por sinal, tolerantes a erro operacional
# =============================================================================


def _bool_evaluator(name: str, field: str) -> Any:
    def _ev(*, output: Any, **_: Any) -> Evaluation:
        if not isinstance(output, dict) or "error" in output:
            return {"name": name, "value": None, "comment": (output or {}).get("error", "output inválido")}
        value = output.get(field)
        return {"name": name, "value": bool(value), "comment": f"{field}={value}"}

    _ev.__name__ = name
    return _ev


eval_gold_ran = _bool_evaluator("gold_ran", "gold_ran")
eval_known_fact_stated = _bool_evaluator("known_fact_stated", "known_fact_stated")
eval_consumption_present = _bool_evaluator("consumption_present", "consumption_present")
eval_quote_survives = _bool_evaluator("quote_survives", "quote_survives")
eval_coordinates_match = _bool_evaluator("coordinates_match", "coordinates_match")
eval_layers_connected = _bool_evaluator("layers_connected", "layers_connected")


def eval_citation_count(*, output: Any, **_: Any) -> Evaluation:
    if not isinstance(output, dict) or "error" in output:
        return {"name": "citation_count", "value": None, "comment": (output or {}).get("error", "output inválido")}
    return {"name": "citation_count", "value": output.get("citation_count"),
            "comment": f"quote={output.get('citation_quote')!r}"}


def eval_operational_error(*, output: Any, **_: Any) -> Evaluation:
    is_error = isinstance(output, dict) and "error" in output
    comment = (output or {}).get("error", "") if is_error else ""
    return {"name": "operational_error", "value": 1 if is_error else 0, "comment": comment}


# =============================================================================
# Prerequisites — pula honestamente, nunca falha obscura no meio da rodada
# =============================================================================


def _prereqs() -> str | None:
    try:
        import pytest  # noqa: F401
    except ImportError:
        return "requer pytest (extra dev) para o harness hermético de captura gold"
    if not FINEP_JSONL.exists():
        return f"fixture silver ausente: {FINEP_JSONL}"
    if not FINEP_META.exists():
        return f"fixture meta ausente: {FINEP_META}"
    if not GOLD_CAPTURE_HELPER.exists():
        return f"harness de captura gold ausente: {GOLD_CAPTURE_HELPER}"
    return None


# =============================================================================
# Suite definition
# =============================================================================

SUITE = Suite(
    name="e2e_health",
    description=(
        "Sinal E2E diagnóstico (RT02-T04, spec radar-data-trust-02-quality-gates "
        "§7.2): um fato conhecido (requisito verbatim do edital finep:602) "
        "atravessa o ingest gold real (radar.core.kg.gold.ingest_all, sob os "
        "seams herméticos de tests/helpers/gold_projection.py — sem LLM, rede "
        "ou banco reais) e a leitura pública de proveniência "
        "(radar.core.kg.provenance_read.public_provenance, RT01-T10), "
        "confirmando que as camadas descoberta→gold→consumo conectam e o fato "
        "sobrevive com sua coordenada de origem. Sinal mínimo, não matriz de "
        "casos. Diagnóstica, sem threshold ou gate bloqueante."
    ),
    load_data=load_data,
    task=_run_e2e,
    evaluators=[
        eval_gold_ran,
        eval_known_fact_stated,
        eval_consumption_present,
        eval_quote_survives,
        eval_coordinates_match,
        eval_citation_count,
        eval_layers_connected,
        eval_operational_error,
    ],
    prereqs=_prereqs,
    classification="diagnostic",
    version="1",
    dataset_paths=[FINEP_JSONL, FINEP_META],
    expected_cases=1,
    expected_case_ids=[_CASE_ID],
)
