"""Registro das suítes de avaliação. Adicionar uma suíte = uma linha aqui.

matching (HybridMatch), rag (retriever) e writing (agente de escrita), todas
wired end-to-end reaproveitando core/{matching,rag,writing}_eval.py.
"""
from __future__ import annotations

from core.eval import extraction, matching, rag, writing
from core.eval.harness import Suite

SUITES: dict[str, Suite] = {
    matching.SUITE.name: matching.SUITE,
    rag.SUITE.name: rag.SUITE,
    writing.SUITE.name: writing.SUITE,
    extraction.SUITE.name: extraction.SUITE,
}


def get_suite(name: str) -> Suite | None:
    return SUITES.get(name)
