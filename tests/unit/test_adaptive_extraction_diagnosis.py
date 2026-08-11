from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar.core.ingestion.adaptive_extraction import (
    AdaptiveDocumentExtraction,
    document_asset_from_blocks,
)
from radar.core.services.data_quality_metrics import compute_data_quality_diagnostics
from radar.domain.adaptive_extraction import ExtractionTarget
from radar.domain.provenance import FactState, LocatorQuality

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "gold_equivalence" / "silver" / "structured_docs"


class _MemoryRepository:
    def __init__(self):
        self.items = {}

    def load(self, fingerprint):
        return self.items.get(fingerprint)

    def save(self, artifact):
        self.items[artifact.fingerprint] = artifact
        return True


def _blocks(source: str, stem: str) -> list[dict]:
    path = _ROOT / source / f"{stem}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize(
    ("source", "stem", "quote"),
    [
        ("finep", "602", "projetos cooperativos entre Instituições Científicas, Tecnológicas e de Inovação (ICTs) e empresas"),
        ("fapesp", "16466", "Pesquisadores do estado de São Paulo devem consultar a FAPESP sobre sua elegibilidade"),
        ("fapesc", "35-2026", "Serão elegíveis para apresentar propostas de credenciamento e recredenciamento as incubadoras de empresas inovadoras"),
        ("web", "ce032edb720c", "cadastre sua startup"),
    ],
)
def test_t04_texto_nativo_resolve_alvo_critico_sem_escalada(source, stem, quote):
    """Amostras locais cobrem PDF/HTML e fontes determinísticas.

    Não há caso reproduzível de associação de tabela, página sem camada textual
    ou conteúdo visual residual que justifique uma rota T05; portanto a decisão
    desta implementação é ``no_escalation``/T05 ``not_applicable``.
    """
    blocks = _blocks(source, stem)
    document = document_asset_from_blocks(
        subject_id=f"{source}:{stem}", source=source, doc_name=blocks[0]["doc"], blocks=blocks,
    )
    target = ExtractionTarget(
        field_path="eligible_entities", value_type="list[str]",
        required_for="eligibility", criticality="decision",
    )
    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    payload = json.loads(kwargs["messages"][1]["content"])
                    text = payload["text"]
                    evidence = next(line.strip() for line in text.splitlines() if line.strip())
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps({"claims": {
                            "eligible_entities": {
                                "value": ["alvo"], "state": "stated", "evidence": evidence,
                            },
                        }})
                    ))])

    service = AdaptiveDocumentExtraction(repository=_MemoryRepository(), llm_client=FakeClient())

    artifact = service.extract(document, [target])

    claim = artifact.claims[0]
    assert claim.provenance.state is FactState.STATED
    assert claim.provenance.evidence_refs[0].locator_quality in {
        LocatorQuality.EXACT, LocatorQuality.DOCUMENT_ONLY,
    }
    assert artifact.route_trace[0].route.value == "text"
    assert all(trace.route.value == "text" for trace in artifact.route_trace)
    diagnostics = compute_data_quality_diagnostics([], as_of=date(2026, 8, 10))
    assert diagnostics.spec06_signals.layout_or_ocr_candidates == ()
    assert diagnostics.spec06_signals.document_incomplete == ()
