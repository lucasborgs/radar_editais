"""Testes das regras curadas de elegibilidade (KG v2 resíduos PR-E.2, R4).

Cobrem as funções novas em core/services/eligibility.py: load_curated_rules,
contrapartida_minima e formatação do bloco de regras.
"""
from __future__ import annotations

import pytest

from radar.core.services.eligibility import (
    _REGIOES,
    contrapartida_minima,
    evaluate_constraint,
    evaluate_opportunity,
    format_curated_rules_block,
    load_curated_rules,
)

pytestmark = pytest.mark.unit


def test_load_rules_returns_dict():
    rules = load_curated_rules()
    assert rules["version"] == 1
    assert "portes" in rules
    assert "contrapartida" in rules
    assert "interpretacoes" in rules


def test_contrapartida_minima_finds_by_porte_and_region():
    # ME no Sudeste = 5%
    result = contrapartida_minima("me", "SP")
    assert result["pct"] == 5
    assert result["regiao"] == "SUDESTE"

    # ME no Norte = 1%
    result = contrapartida_minima("me", "AM")
    assert result["pct"] == 1
    assert result["regiao"] == "N"


def test_contrapartida_minima_fallback_generic():
    # Media sem região específica
    result = contrapartida_minima("media", "SP")
    assert result["pct"] == 10


def test_contrapartida_minima_unknown_porte():
    assert contrapartida_minima("nonexistent", "SP") == {}


def test_evaluate_constraint_porte_me():
    """Constraint porte in [me] vs perfil com tamanho_empresa=me → sat."""
    constraint = {"tipo": "porte", "op": "in", "valor": ["me"]}
    profile = {"tamanho_empresa": "me"}
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "sat"


def test_evaluate_constraint_porte_mismatch():
    constraint = {"tipo": "porte", "op": "in", "valor": ["me", "epp"]}
    profile = {"tamanho_empresa": "grande"}
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "unsat"


def test_evaluate_constraint_uf_in():
    constraint = {"tipo": "sede_uf", "op": "in", "valor": ["SP", "RJ"]}
    profile = {"uf": "SP"}
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "sat"


def test_evaluate_constraint_uf_region_expansion():
    """NE expande para os 9 estados do Nordeste."""
    constraint = {"tipo": "sede_uf", "op": "in", "valor": ["NE"]}
    profile = {"uf": "BA"}
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "sat"


def test_evaluate_constraint_faturamento_lte():
    constraint = {"tipo": "faturamento", "op": "lte", "valor": 16000000}
    profile = {"faturamento_anual": 5000000}
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "sat"


def test_evaluate_constraint_faturamento_gte():
    constraint = {"tipo": "faturamento", "op": "gte", "valor": 16000000}
    profile = {"faturamento_anual": 5000000}
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "unsat"


def test_evaluate_opportunity_aggregates():
    constraints = [
        {"tipo": "porte", "op": "in", "valor": ["me"]},
        {"tipo": "sede_uf", "op": "in", "valor": ["SP"]},
    ]
    profile = {"tamanho_empresa": "me", "uf": "RJ"}
    result = evaluate_opportunity(constraints, profile)
    assert result["status"] == "inelegivel"
    assert any("UF da sede" in r for r in result["unsat"])


def test_unknown_constraint_type_is_safe():
    constraint = {"tipo": "parceria", "op": "exige", "valor": "ICT"}
    profile = {}
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "unknown"


def test_missing_profile_field_is_unknown():
    constraint = {"tipo": "porte", "op": "in", "valor": ["me"]}
    profile = {}  # sem tamanho_empresa
    verdict, reason = evaluate_constraint(constraint, profile)
    assert verdict == "unknown"


def test_regioes_expansion():
    assert _REGIOES["NE"] == {"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"}
    assert _REGIOES["SUL"] == {"PR", "RS", "SC"}
    assert _REGIOES["NORTE"] == {"AC", "AP", "AM", "PA", "RO", "RR", "TO"}


def test_format_curated_rules_block_includes_portes():
    block = format_curated_rules_block(None)
    assert "R$ 4,800,000" in block or "R$" in block
    assert "Portes" in block


def test_format_curated_rules_block_with_constraints_filters():
    """Só inclui portes quando constraint tem tipo=porte."""
    constraints = [{"tipo": "porte", "op": "in", "valor": ["me"]}]
    block = format_curated_rules_block(None, constraints)
    assert "Portes" in block
    assert "SUDAM" not in block  # filtrado


def test_format_curated_rules_block_with_profile_includes_contrapartida():
    class FakeProfile:
        tamanho_empresa = "me"
        uf = "SP"

    block = format_curated_rules_block(FakeProfile())
    assert "Contrapartida" in block


def test_format_curated_rules_block_empty_without_rules():
    """Fallback seguro quando arquivo de regras não existe."""
    import radar.core.services.eligibility as el

    # Força cache vazio
    old_load = el.load_curated_rules
    el.load_curated_rules = lambda: {"version": 1, "portes": {},
                                      "contrapartida": {"tabela": []},
                                      "sudam_sudene": {}, "interpretacoes": {}}
    try:
        block = format_curated_rules_block(None)
        assert block == ""
    finally:
        el.load_curated_rules = old_load
