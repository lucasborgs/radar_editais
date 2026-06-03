"""Registro das suítes de avaliação. Adicionar uma suíte = uma linha aqui.

Hoje: matching (wired end-to-end). Próximas (mesma forma de `Suite`): rag
(reaproveita core/rag_eval.py) e writing (core/writing_eval.py) — ver os
scripts legados scripts/eval_{rag,agent_writing}.py como fonte do `task`.
"""
from __future__ import annotations

from core.eval import matching
from core.eval.harness import Suite

SUITES: dict[str, Suite] = {
    matching.SUITE.name: matching.SUITE,
}


def get_suite(name: str) -> Suite | None:
    return SUITES.get(name)
