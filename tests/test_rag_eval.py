"""
Unidade pra `core.rag_eval` — métricas de avaliação do RAG.

Testes puros: NÃO chamam OpenAI nem o DB. `judge_faithfulness` é testado
apenas no nível do parser de resposta (`_parse_faithfulness_score`).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.rag_eval import (  # noqa: E402
    _matches,
    _parse_faithfulness_score,
    _percentile,
    aggregate_runs,
    evaluate_query,
    hit_at_k,
    reciprocal_rank,
)

# =============================================================================
# _matches — semântica de comparação chunk × esperado
# =============================================================================

def test_matches_section_exato():
    chunk = {"source_file": "Edital.pdf", "section": "1. OBJETIVO"}
    exp = {"source_file": "Edital.pdf", "section": "1. OBJETIVO"}
    assert _matches(chunk, exp)


def test_matches_falha_quando_source_difere():
    chunk = {"source_file": "Edital.pdf", "section": "1. OBJETIVO"}
    exp = {"source_file": "OutroEdital.pdf", "section": "1. OBJETIVO"}
    assert not _matches(chunk, exp)


def test_matches_falha_quando_section_difere():
    chunk = {"source_file": "Edital.pdf", "section": "1. OBJETIVO"}
    exp = {"source_file": "Edital.pdf", "section": "2. EIXO"}
    assert not _matches(chunk, exp)


def test_matches_loose_quando_expected_section_eh_none():
    """golden com section=None aceita qualquer chunk do mesmo PDF."""
    chunk = {"source_file": "FAQ.pdf", "section": "qualquer coisa"}
    exp = {"source_file": "FAQ.pdf", "section": None}
    assert _matches(chunk, exp)


def test_matches_falha_se_chunk_sem_section_e_expected_tem():
    chunk = {"source_file": "Edital.pdf", "section": None}
    exp = {"source_file": "Edital.pdf", "section": "1. OBJETIVO"}
    assert not _matches(chunk, exp)


def test_matches_chunk_sem_section_e_expected_sem():
    """Ambos None → ainda casa pelo source_file (matching loose)."""
    chunk = {"source_file": "X.pdf", "section": None}
    exp = {"source_file": "X.pdf", "section": None}
    assert _matches(chunk, exp)


# =============================================================================
# hit_at_k
# =============================================================================

def test_hit_at_k_progressao():
    retrieved = [
        {"source_file": "X.pdf", "section": "A"},
        {"source_file": "Y.pdf", "section": "B"},
        {"source_file": "Z.pdf", "section": "C"},
    ]
    expected = [{"source_file": "Y.pdf", "section": "B"}]
    assert not hit_at_k(retrieved, expected, k=1)  # Y está em rank 2
    assert hit_at_k(retrieved, expected, k=2)
    assert hit_at_k(retrieved, expected, k=3)


def test_hit_at_k_k_zero_ou_negativo_eh_falso():
    retrieved = [{"source_file": "X.pdf", "section": "A"}]
    expected = [{"source_file": "X.pdf", "section": "A"}]
    assert not hit_at_k(retrieved, expected, k=0)
    assert not hit_at_k(retrieved, expected, k=-1)


def test_hit_at_k_listas_vazias():
    assert not hit_at_k([], [{"source_file": "X.pdf", "section": "A"}], k=5)
    assert not hit_at_k([{"source_file": "X.pdf", "section": "A"}], [], k=5)


def test_hit_at_k_multiplos_expected_qualquer_um_basta():
    retrieved = [
        {"source_file": "X.pdf", "section": "A"},
        {"source_file": "Y.pdf", "section": "B"},
    ]
    expected = [
        {"source_file": "Z.pdf", "section": "Z"},   # não está
        {"source_file": "Y.pdf", "section": "B"},   # está em rank 2
    ]
    assert not hit_at_k(retrieved, expected, k=1)
    assert hit_at_k(retrieved, expected, k=2)


# =============================================================================
# reciprocal_rank
# =============================================================================

def test_reciprocal_rank_basico():
    retrieved = [
        {"source_file": "X.pdf", "section": "A"},
        {"source_file": "Y.pdf", "section": "B"},
        {"source_file": "Z.pdf", "section": "C"},
    ]
    assert reciprocal_rank(retrieved, [{"source_file": "X.pdf", "section": "A"}]) == 1.0
    assert reciprocal_rank(retrieved, [{"source_file": "Y.pdf", "section": "B"}]) == 0.5
    assert reciprocal_rank(retrieved, [{"source_file": "Z.pdf", "section": "C"}]) == 1/3


def test_reciprocal_rank_sem_hit():
    retrieved = [{"source_file": "X.pdf", "section": "A"}]
    expected = [{"source_file": "W.pdf", "section": None}]
    assert reciprocal_rank(retrieved, expected) == 0.0


def test_reciprocal_rank_listas_vazias():
    assert reciprocal_rank([], [{"source_file": "X.pdf", "section": "A"}]) == 0.0
    assert reciprocal_rank([{"source_file": "X.pdf", "section": "A"}], []) == 0.0


def test_reciprocal_rank_pega_primeiro_match_quando_multiplos():
    retrieved = [
        {"source_file": "X.pdf", "section": "A"},  # casa expected[1]
        {"source_file": "Y.pdf", "section": "B"},  # casa expected[0]
    ]
    expected = [
        {"source_file": "Y.pdf", "section": "B"},
        {"source_file": "X.pdf", "section": "A"},
    ]
    # Primeiro chunk recuperado (rank 1) casa com expected[1] → RR=1.0
    assert reciprocal_rank(retrieved, expected) == 1.0


# =============================================================================
# evaluate_query — junta tudo
# =============================================================================

def test_evaluate_query_completo():
    retrieved = [
        {"source_file": "X.pdf", "section": "A"},
        {"source_file": "Y.pdf", "section": "B"},
    ]
    expected = [{"source_file": "Y.pdf", "section": "B"}]
    out = evaluate_query(retrieved, expected, ks=[1, 3, 5])
    assert out["hit_at_k"] == {"1": False, "3": True, "5": True}
    assert out["reciprocal_rank"] == 0.5
    assert out["n_retrieved"] == 2
    assert out["null_result"] is False


def test_evaluate_query_null_result():
    out = evaluate_query([], [{"source_file": "X.pdf", "section": "A"}])
    assert out["null_result"] is True
    assert out["reciprocal_rank"] == 0.0
    assert out["hit_at_k"] == {"1": False, "3": False, "5": False}


def test_evaluate_query_ks_custom():
    retrieved = [{"source_file": "X.pdf", "section": "A"}] * 10
    expected = [{"source_file": "X.pdf", "section": "A"}]
    out = evaluate_query(retrieved, expected, ks=[2, 7])
    assert set(out["hit_at_k"].keys()) == {"2", "7"}


# =============================================================================
# _percentile (linear-interpolation)
# =============================================================================

def test_percentile_lista_vazia():
    assert _percentile([], 50) == 0.0


def test_percentile_um_elemento():
    assert _percentile([42.0], 50) == 42.0
    assert _percentile([42.0], 95) == 42.0


def test_percentile_p50_e_p95():
    vals = [100.0, 200.0, 300.0, 400.0]
    # P50: rank = 0.5 * 3 = 1.5 → entre 200 e 300 → 250
    assert _percentile(vals, 50) == 250.0
    # P95: rank = 0.95 * 3 = 2.85 → entre 300 e 400 → 385
    assert abs(_percentile(vals, 95) - 385.0) < 0.001


def test_percentile_p100_eh_max():
    assert _percentile([1.0, 2.0, 3.0], 100) == 3.0


# =============================================================================
# aggregate_runs
# =============================================================================

def _mk_q(*, hit_k1=False, hit_k3=False, hit_k5=False, rr=0.0,
         null_result=False, latency_ms=None, faithfulness=None) -> dict:
    """Helper pra montar uma query result no formato esperado."""
    q = {
        "hit_at_k": {"1": hit_k1, "3": hit_k3, "5": hit_k5},
        "reciprocal_rank": rr,
        "null_result": null_result,
    }
    if latency_ms is not None:
        q["latency_ms"] = latency_ms
    if faithfulness is not None:
        q["faithfulness"] = faithfulness
    return q


def test_aggregate_empty():
    assert aggregate_runs([]) == {}


def test_aggregate_recall_e_mrr():
    per_query = [
        _mk_q(hit_k1=True, hit_k3=True, hit_k5=True, rr=1.0),
        _mk_q(hit_k1=False, hit_k3=True, hit_k5=True, rr=0.5),
        _mk_q(hit_k1=False, hit_k3=False, hit_k5=True, rr=0.25),
        _mk_q(hit_k1=False, hit_k3=False, hit_k5=False, rr=0.0),
    ]
    agg = aggregate_runs(per_query)
    assert agg["n_queries"] == 4
    assert agg["recall_at_1"] == 0.25
    assert agg["recall_at_3"] == 0.5
    assert agg["recall_at_5"] == 0.75
    assert agg["mrr"] == (1.0 + 0.5 + 0.25 + 0.0) / 4


def test_aggregate_null_rate():
    per_query = [
        _mk_q(null_result=False),
        _mk_q(null_result=True),
        _mk_q(null_result=True),
    ]
    agg = aggregate_runs(per_query)
    assert agg["null_rate"] == 2 / 3


def test_aggregate_latency_percentile():
    per_query = [_mk_q(latency_ms=v) for v in (100, 200, 300, 400)]
    agg = aggregate_runs(per_query)
    assert agg["latency_p50_ms"] == 250.0
    assert abs(agg["latency_p95_ms"] - 385.0) < 0.001
    assert agg["latency_mean_ms"] == 250.0


def test_aggregate_omite_faithfulness_se_ninguem_tem():
    per_query = [_mk_q(rr=0.5), _mk_q(rr=0.3)]
    agg = aggregate_runs(per_query)
    assert "faithfulness_mean" not in agg
    assert "faithfulness_n" not in agg


def test_aggregate_faithfulness_quando_presente():
    per_query = [_mk_q(faithfulness=v) for v in (5, 4, 2)]
    agg = aggregate_runs(per_query)
    assert agg["faithfulness_mean"] == (5 + 4 + 2) / 3
    assert agg["faithfulness_n"] == 3


def test_aggregate_omite_latency_se_ninguem_tem():
    per_query = [_mk_q(rr=0.5), _mk_q(rr=0.3)]
    agg = aggregate_runs(per_query)
    assert "latency_p50_ms" not in agg
    assert "latency_p95_ms" not in agg


# =============================================================================
# _parse_faithfulness_score — único pedaço unit-testável da chain LLM
# =============================================================================

def test_parse_faithfulness_score_extrai_digito_simples():
    assert _parse_faithfulness_score("5") == 5.0
    assert _parse_faithfulness_score("0") == 0.0
    assert _parse_faithfulness_score("3") == 3.0


def test_parse_faithfulness_score_ignora_prefixo():
    assert _parse_faithfulness_score("Score: 4") == 4.0
    assert _parse_faithfulness_score("**5**") == 5.0
    assert _parse_faithfulness_score("Nota=2/5") == 2.0


def test_parse_faithfulness_score_ignora_digitos_fora_da_faixa():
    """Apenas 0-5 contam; outros dígitos não viram score."""
    assert _parse_faithfulness_score("9") == 0.0
    assert _parse_faithfulness_score("7") == 0.0
    assert _parse_faithfulness_score("abc 7 5") == 5.0  # ignora 7, pega 5


def test_parse_faithfulness_score_sem_digito():
    assert _parse_faithfulness_score("") == 0.0
    assert _parse_faithfulness_score("nao sei") == 0.0
    assert _parse_faithfulness_score("???") == 0.0
