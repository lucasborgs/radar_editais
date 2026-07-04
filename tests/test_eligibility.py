"""Testes do avaliador de elegibilidade dura (KG v2, PR5 / Estágio 0)."""
from __future__ import annotations

from core.services import eligibility as el

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
    assert el.is_eliminated(cons, {"tamanho_empresa": "GRANDE", "uf": "SP"}) is True


def test_aggregate_nao_verificada_when_unknown_only():
    cons = [{"tipo": "porte", "op": "in", "valor": ["me"]}]
    out = el.evaluate_opportunity(cons, {})  # perfil vazio
    assert out["status"] == el.NAO_VERIFICADA
    assert out["unknown"] and not out["unsat"]
    # unknown NUNCA elimina (PR5)
    assert el.is_eliminated(cons, {}) is False


def test_aggregate_elegivel_when_all_sat_or_empty():
    cons = [{"tipo": "porte", "op": "in", "valor": ["me", "epp"]}]
    assert el.evaluate_opportunity(cons, {"tamanho_empresa": "ME"})["status"] == el.ELEGIVEL
    # sem constraints → elegível
    assert el.evaluate_opportunity([], {"tamanho_empresa": "GRANDE"})["status"] == el.ELEGIVEL
    assert el.evaluate_opportunity(None, {})["status"] == el.ELEGIVEL


def test_accepts_object_profile_not_just_dict():
    class P:
        tamanho_empresa = "GRANDE"
    cons = [{"tipo": "porte", "op": "in", "valor": ["me"]}]
    assert el.is_eliminated(cons, P()) is True
