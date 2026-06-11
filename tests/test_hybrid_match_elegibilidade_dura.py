"""Dimensão soft `elegibilidade_dura` do Stage 1 (região/idade/faturamento).

Garante: (a) o scoring por par perfil↔constraint, (b) a DORMÊNCIA — card sem
`eligibility_constraints` pontua idêntico ao legado (a dimensão nem entra no
breakdown), o que prova regressão zero no catálogo atual. (c) que ela é SOFT —
nunca seta eligible=False sozinha.
"""
from __future__ import annotations

from core.services.hybrid_match_service import (
    _WEIGHTS,
    _score_elegibilidade_dura,
    score_stage1,
)
from domain.user_profile import CompanyProfile

_W = {k: float(v) for k, v in _WEIGHTS.items()}
_WD = _W["elegibilidade_dura"]  # 10


def _profile(**kw) -> CompanyProfile:
    base = dict(nome="ACME", tipo_entidade="empresa", tamanho_empresa="ME")
    base.update(kw)
    return CompanyProfile(**base)


def _card(constraints) -> dict:
    return {"id": "finep:X", "title": "T", "eligibility_constraints": constraints}


# --- região --------------------------------------------------------------

def test_region_match_by_state_name():
    c = _card([{"type": "region", "description": "Empresas sediadas em São Paulo"}])
    assert _score_elegibilidade_dura(c, _profile(uf="SP"), _W) == _WD


def test_region_match_by_macro_region():
    c = _card([{"type": "region", "description": "Restrito à região Sudeste"}])
    assert _score_elegibilidade_dura(c, _profile(uf="MG"), _W) == _WD


def test_region_mismatch_scores_zero():
    c = _card([{"type": "region", "description": "Empresas do Nordeste"}])
    assert _score_elegibilidade_dura(c, _profile(uf="SP"), _W) == 0.0


def test_region_no_profile_pair_is_neutral():
    """Constraint presente mas perfil sem UF → neutro (w/2), sinal HITL."""
    c = _card([{"type": "region", "description": "Empresas do Nordeste"}])
    assert _score_elegibilidade_dura(c, _profile(uf=""), _W) == _WD / 2


# --- idade da empresa ----------------------------------------------------

def test_company_age_within_limit():
    c = _card([{"type": "company_age", "description": "até 4 anos de constituição"}])
    assert _score_elegibilidade_dura(c, _profile(ano_fundacao=2024), _W) == _WD


def test_company_age_exceeds_limit():
    c = _card([{"type": "company_age", "description": "no máximo 5 anos"}])
    assert _score_elegibilidade_dura(c, _profile(ano_fundacao=2005), _W) == 0.0


# --- faturamento ---------------------------------------------------------

def test_revenue_within_ceiling_millions():
    c = _card([{"type": "revenue", "description": "faturamento de até R$ 4,8 milhões"}])
    assert _score_elegibilidade_dura(c, _profile(faturamento_anual=2_000_000), _W) == _WD


def test_revenue_exceeds_ceiling():
    c = _card([{"type": "revenue", "description": "receita anual até R$ 4,8 milhões"}])
    assert _score_elegibilidade_dura(c, _profile(faturamento_anual=10_000_000), _W) == 0.0


# --- aplicabilidade / dormência ------------------------------------------

def test_no_constraints_returns_none():
    assert _score_elegibilidade_dura(_card([]), _profile(uf="SP"), _W) is None


def test_only_unsupported_types_returns_none():
    c = _card([{"type": "cnae", "description": "CNAE 62.01"},
               {"type": "consortium", "description": "consórcio obrigatório"}])
    assert _score_elegibilidade_dura(c, _profile(uf="SP"), _W) is None


def test_dormancy_card_without_constraints_scores_identically():
    """Card sem o campo → breakdown SEM 'elegibilidade_dura' (idêntico ao legado)."""
    legacy_card = {
        "id": "finep:OK", "title": "Subvenção software",
        "eligible_entities": ["empresas"], "themes": ["software"],
        "trl_range": {"min": 3, "max": 7}, "mechanism": "subvencao",
        "counterpart_required": False,
    }
    res = score_stage1(legacy_card, _profile(uf="SP", trl=5), _W)
    assert "elegibilidade_dura" not in res.breakdown


def test_soft_never_sets_ineligible_alone():
    """Mismatch duro NÃO zera elegibilidade: outras dimensões sustentam o item."""
    card = {
        "id": "finep:OK", "title": "Subvenção software",
        "eligible_entities": ["empresas"], "themes": ["software"],
        "trl_range": {"min": 3, "max": 7}, "mechanism": "subvencao",
        "counterpart_required": False,
        "eligibility_constraints": [{"type": "region", "description": "Nordeste"}],
    }
    res = score_stage1(
        card,
        _profile(uf="SP", trl=5, one_liner="software de gestão",
                 tipos_financiamento_interesse=["subvencao_nao_reembolsavel"]),
        _W,
    )
    assert res.breakdown["elegibilidade_dura"] == 0  # dimensão zerada
    assert res.eligible is True  # mas o item segue elegível pelas demais
