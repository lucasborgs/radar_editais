"""Registro das suítes de avaliação. Adicionar uma suíte = uma linha aqui.

matching (gold v3), rag (retriever) e writing (agente de escrita), além das
suítes especializadas registradas abaixo. Todas exercitam o pipeline real.
"""
from __future__ import annotations

from core.eval import (
    extraction,
    matching,
    opportunity_type,
    profile_extractor,
    rag,
    reranker,
    structurer,
    triage,
    writing,
)
from core.eval.harness import Suite

SUITES: dict[str, Suite] = {
    matching.SUITE.name: matching.SUITE,
    rag.SUITE.name: rag.SUITE,
    writing.SUITE.name: writing.SUITE,
    extraction.SUITE.name: extraction.SUITE,
    opportunity_type.SUITE.name: opportunity_type.SUITE,
    triage.SUITE.name: triage.SUITE,
    profile_extractor.SUITE.name: profile_extractor.SUITE,
    reranker.SUITE.name: reranker.SUITE,
    structurer.SUITE.name: structurer.SUITE,
    writing.SUITE_WRITING_V2.name: writing.SUITE_WRITING_V2,
}


def get_suite(name: str) -> Suite | None:
    return SUITES.get(name)
