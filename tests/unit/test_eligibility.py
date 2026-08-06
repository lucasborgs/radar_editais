"""Testes do avaliador de elegibilidade dura (KG v2, PR5 / Estágio 0)."""
from __future__ import annotations

import pytest

from radar.core.services import eligibility as el

pytestmark = pytest.mark.unit

# ── constraint isolada ────────────────────────────────────────────────────────

def test_porte_in_sat_unsat_unknown():
    c = {"tipo": "porte", "op": "in", "valor": ["mei", "me", "epp", "media"]}
    assert el.evaluate_constraint(c, {"tamanho_empresa": "ME"})[0] == el.SAT
    assert el.evaluate_constraint(c, {"tamanho_empresa": "MEDIO"})[0] == el.SAT
    assert el.evaluate_constraint(c, {"tamanho_empresa": "GRANDE"})[0] == el.UNSAT
    # campo faltando → unknown (não elimina)
    assert el.evaluate_constraint(c, {})[0] == el.UNKNOWN


def test_faturamento_lte_gte():
    lte = {"tipo": "faturamento", "op": "lte", "valor": 16_000_000}
    assert el.evaluate_constraint(lte, {"faturamento_anual": 5_000_000})[0] == el.SAT
    assert el.evaluate_constraint(lte, {"faturamento_anual": 20_000_000})[0] == el.UNSAT
    gte = {"tipo": "faturamento", "op": "gte", "valor": 16_000_000}
    assert el.evaluate_constraint(gte, {"faturamento_anual": 20_000_000})[0] == el.SAT
    assert el.evaluate_constraint(gte, {"faturamento_anual": 5_000_000})[0] == el.UNSAT


def test_trl_gte():
    c = {"tipo": "trl", "op": "gte", "valor": 4}
    assert el.evaluate_constraint(c, {"trl": 6})[0] == el.SAT
    assert el.evaluate_constraint(c, {"trl": 2})[0] == el.UNSAT
    assert el.evaluate_constraint(c, {"trl": None})[0] == el.UNKNOWN


def test_sede_uf_region_expansion():
    c = {"tipo": "sede_uf", "op": "in", "valor": ["NE", "CO"]}
    assert el.evaluate_constraint(c, {"uf": "BA"})[0] == el.SAT   # Bahia ∈ Nordeste
    assert el.evaluate_constraint(c, {"uf": "GO"})[0] == el.SAT   # Goiás ∈ Centro-Oeste
    assert el.evaluate_constraint(c, {"uf": "SP"})[0] == el.UNSAT


def test_sede_uf_bare_se_is_sergipe_not_sudeste():
    c = {"tipo": "sede_uf", "op": "in", "valor": ["SE"]}
    assert el.evaluate_constraint(c, {"uf": "SE"})[0] == el.SAT   # Sergipe
    assert el.evaluate_constraint(c, {"uf": "SP"})[0] == el.UNSAT  # NÃO expande p/ Sudeste


def test_forma_juridica_startup_satisfies_empresa():
    # achado da auditoria externa do bake-off Fase 1.5: constraint exige
    # forma_juridica=[empresa], perfil tem tipo_entidade="startup" — toda
    # startup registrada JÁ é uma empresa, não deveriam ser tratadas como
    # categorias mutuamente exclusivas (matou biofarma_saude x finep:773/774
    # e semicondutores x finep:780, positivos verdadeiros de afinidade).
    c = {"tipo": "forma_juridica", "op": "in", "valor": ["empresa"]}
    assert el.evaluate_constraint(c, {"tipo_entidade": "startup"})[0] == el.SAT
    for variante in ("ltda", "sa", "eireli", "me", "epp", "empresa"):
        assert el.evaluate_constraint(c, {"tipo_entidade": variante})[0] == el.SAT


def test_forma_juridica_ict_nao_satisfaz_empresa():
    # negativo real: ICTs/universidades continuam UNSAT contra forma_juridica=[empresa]
    # (a hierarquia só aproxima "empresa" de seus subtipos, não de outras naturezas).
    c = {"tipo": "forma_juridica", "op": "in", "valor": ["empresa"]}
    assert el.evaluate_constraint(c, {"tipo_entidade": "ict"})[0] == el.UNSAT
    assert el.evaluate_constraint(c, {"tipo_entidade": "universidade"})[0] == el.UNSAT


def test_forma_juridica_not_in_nao_afetado_pela_hierarquia():
    # not_in continua exato — a hierarquia de satisfação é sobre "in" (o que
    # cumpre a exigência), não deve abrir brecha numa exclusão.
    c = {"tipo": "forma_juridica", "op": "not_in", "valor": ["ict"]}
    assert el.evaluate_constraint(c, {"tipo_entidade": "startup"})[0] == el.SAT
    assert el.evaluate_constraint(c, {"tipo_entidade": "ict"})[0] == el.UNSAT


def test_not_in():
    c = {"tipo": "porte", "op": "not_in", "valor": ["grande"]}
    assert el.evaluate_constraint(c, {"tamanho_empresa": "GRANDE"})[0] == el.UNSAT
    assert el.evaluate_constraint(c, {"tamanho_empresa": "ME"})[0] == el.SAT


def test_parceria_is_relational_unknown():
    # parceria não tem campo no perfil → sempre unknown (não elimina)
    c = {"tipo": "parceria", "op": "exige", "valor": "ict"}
    assert el.evaluate_constraint(c, {"tamanho_empresa": "ME"})[0] == el.UNKNOWN


# ── agregação por oportunidade ────────────────────────────────────────────────

def test_aggregate_inelegivel_on_any_unsat():
    cons = [
        {"tipo": "porte", "op": "in", "valor": ["mei", "me", "epp", "media"]},
        {"tipo": "sede_uf", "op": "in", "valor": ["SC"]},
    ]
    out = el.evaluate_opportunity(cons, {"tamanho_empresa": "GRANDE", "uf": "SP"})
    assert out["status"] == el.INELEGIVEL
    assert out["unsat"] and not out["unknown"]


def test_aggregate_nao_verificada_when_unknown_only():
    cons = [{"tipo": "porte", "op": "in", "valor": ["me"]}]
    out = el.evaluate_opportunity(cons, {})  # perfil vazio
    assert out["status"] == el.NAO_VERIFICADA
    assert out["unknown"] and not out["unsat"]


def test_aggregate_elegivel_when_all_sat_or_empty():
    cons = [{"tipo": "porte", "op": "in", "valor": ["me", "epp"]}]
    assert el.evaluate_opportunity(cons, {"tamanho_empresa": "ME"})["status"] == el.ELEGIVEL
    # sem constraints → elegível
    assert el.evaluate_opportunity([], {"tamanho_empresa": "GRANDE"})["status"] == el.ELEGIVEL
    assert el.evaluate_opportunity(None, {})["status"] == el.ELEGIVEL
