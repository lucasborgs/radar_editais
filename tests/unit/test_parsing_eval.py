"""Testes das métricas intrínsecas de parsing/chunking (core/eval/metrics_parsing.py)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.eval.metrics_parsing import boundary_alignment, section_coverage, size_distribution

pytestmark = pytest.mark.unit


def test_size_distribution_pega_patologias():
    texts = ["x" * 100, "y" * 4000, "z" * 1000]  # 1 órfão (<300), 1 oversize (>3500)
    d = size_distribution(texts, oversize_threshold=3500, orphan_threshold=300)
    assert d["n"] == 3
    assert d["max"] == 4000
    assert d["pct_oversize"] == round(1 / 3, 4)
    assert d["pct_orphan"] == round(1 / 3, 4)


def test_size_distribution_vazio():
    d = size_distribution([], oversize_threshold=3500, orphan_threshold=300)
    assert d == {"n": 0, "p50": 0, "p95": 0, "max": 0, "pct_oversize": 0.0, "pct_orphan": 0.0}


def test_section_coverage():
    chunks = [{"text": "a", "section": "1) X"}, {"text": "b", "section": ""}, {"text": "c", "section": "1) X"}]
    d = section_coverage(chunks)
    assert d["n"] == 3
    assert d["pct_com_secao"] == round(2 / 3, 4)
    assert d["n_secoes_distintas"] == 1


def test_boundary_alignment():
    hdr = re.compile(r"(?m)^(?=\d+\)\s)")
    # "2)" após \n\n = fronteira limpa; cabeçalho colado no meio = sujo
    clean_units = ["1) A\n\n2) B"]
    assert boundary_alignment(clean_units, hdr)["pct_em_fronteira"] == 1.0
    # "1) A texto 2) B colado": "2)" não inicia linha → não casa (?m)^. O regex
    # nem casa o colado; usa um que casa em qualquer lugar:
    hdr_any = re.compile(r"\d+\)\s")
    d = boundary_alignment(["1) A\n\ntexto 2) colado"], hdr_any)
    assert d["n_headers"] == 2 and d["pct_em_fronteira"] == 0.5


def test_gold_recall_e_best_chunk():
    from core.eval.metrics_rag import gold_best_chunk_recall_at_k, gold_recall_at_k
    gold = "prazo de submissão das propostas encerra dia trinta"
    # resposta espalhada em 2 chunks (cada um metade) → union alta, best baixa
    spread = [{"text": "prazo de submissão das"}, {"text": "propostas encerra dia trinta"}]
    # resposta inteira num chunk → best alta
    whole = [{"text": "o prazo de submissão das propostas encerra dia trinta"}]
    assert gold_recall_at_k(spread, gold, 5) == 1.0
    assert gold_best_chunk_recall_at_k(spread, gold, 5) < 1.0   # nenhum chunk sozinho
    assert gold_best_chunk_recall_at_k(whole, gold, 5) == 1.0   # um chunk capturou tudo
    assert gold_recall_at_k([], gold, 5) == 0.0
    assert gold_recall_at_k(whole, "", 5) is None               # sem gold_text → N/A
