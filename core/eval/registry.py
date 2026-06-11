"""Registro das suítes de avaliação. Adicionar uma suíte = uma linha aqui.

matching (HybridMatch), rag (retriever) e writing (agente de escrita), todas
wired end-to-end reaproveitando core/{matching,rag,writing}_eval.py.
"""
from __future__ import annotations

from core.eval import (
    extraction,
    investor_match,
    matching,
    opportunity_type,
    rag,
    triage,
    writing,
)
from core.eval.harness import Suite

SUITES: dict[str, Suite] = {
    matching.SUITE.name: matching.SUITE,
    rag.SUITE.name: rag.SUITE,
    writing.SUITE.name: writing.SUITE,
    extraction.SUITE.name: extraction.SUITE,
    investor_match.SUITE.name: investor_match.SUITE,
    opportunity_type.SUITE.name: opportunity_type.SUITE,
    triage.SUITE.name: triage.SUITE,
}


def get_suite(name: str) -> Suite | None:
    return SUITES.get(name)
