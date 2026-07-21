"""Testes do filtro determinístico anti-classe (core/kg/canonicalize).

Os passes LLM/grafo da higiene de Conceitos morreram com o hipergrado (PR-C v3).
O que fica é `anti_class_verdict` — descarte determinístico consumido por
`core.kg.gold.normalize_tags`.
"""
from __future__ import annotations

import pytest

from core.kg.canonicalize import anti_class_verdict

pytestmark = pytest.mark.unit


def test_anti_class_verdict_flags_wrong_class():
    assert anti_class_verdict("TRL")["categoria"] == "metrica"
    assert anti_class_verdict("Technology Readiness Level")["categoria"] == "metrica"
    assert anti_class_verdict("nível de maturidade tecnológica")["categoria"] == "metrica"
    assert anti_class_verdict("LGPD")["categoria"] == "legal"
    assert anti_class_verdict("Lei nº 14.133")["categoria"] == "legal"
    assert anti_class_verdict("Marco Civil da Internet")["categoria"] == "legal"
    assert anti_class_verdict("Programa")["categoria"] == "generico"
    assert anti_class_verdict("tecnologia")["categoria"] == "generico"
    assert anti_class_verdict("consultoria")["categoria"] == "generico"
    # composto legítimo e tema real passam incólumes (não são descartados)
    for keep in ("tecnologia assistiva", "saúde digital", "inteligência artificial",
                 "eficiência energética", "internet das coisas"):
        assert anti_class_verdict(keep) is None


def test_anti_class_verdict_empty():
    assert anti_class_verdict("") is None
    assert anti_class_verdict("   ") is None
