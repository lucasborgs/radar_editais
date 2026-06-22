"""Tier 0 — guarda do contrato 'o index card carrega os campos de match'.

Em produção (cloud) a wiki page é ARQUIVO efêmero e não existe no container web →
o HybridMatch Stage 1 usa só o index card. Se os campos estruturados
(mechanism/trl_range/counterpart_required/eligible_entities/value_range) não
estiverem no card, as dimensões trl/mecanismo/contrapartida FLATLINAM (mesmo valor
para todo edital) e o ranking colapsa SILENCIOSAMENTE — sem erro.

O eval local (`core.eval matching`) NÃO pega isso: roda com as wiki files presentes.
Estes testes puros pegam — falham se o contrato quebrar (síntese parar de promover
os campos, ou Stage 1 passar a depender de campo fora do card).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.kg.wiki_schema import MATCH_FIELDS as _MATCH_FIELDS  # noqa: E402
from core.kg.wiki_schema import promote_match_fields as _promote_match_fields  # noqa: E402
from core.services.hybrid_match_service import (  # noqa: E402
    _score_contrapartida,
    _score_mecanismo,
    _score_trl,
)
from domain.user_profile import CompanyProfile  # noqa: E402

W = {"trl": 20, "mecanismo": 15, "contrapartida": 10}


def test_promote_match_fields_copies_only_match_fields():
    """A promoção leva os campos de match da wiki page pro index entry — Stage 1
    (mechanism/trl_range/counterpart_required/eligible_entities/value_range) + contexto
    do Stage 2 (objective/key_requirements). E SÓ esses: o resto da página
    (key_facts/proposal_sections/…) fica de fora do card (separação de responsabilidades)."""
    entry = {"id": "finep:1", "title": "x", "themes": ["agro"]}  # card cru do scrape
    wiki = {
        "mechanism": "subvencao",
        "trl_range": {"min": 3, "max": 6},
        "counterpart_required": True,
        "eligible_entities": ["empresas"],
        "value_range": {"min_brl": 0, "max_brl": 1000},
        "objective": "foco em bioeconomia",
        "key_requirements": ["req A", "req B"],
        "eligibility_constraints": [{"type": "region", "description": "sede no NE", "evidence": "..."}],
        "key_facts": ["NÃO deve ir pro card"],
        "proposal_sections": ["NÃO deve ir pro card"],
    }
    _promote_match_fields(entry, wiki)
    for k in _MATCH_FIELDS:  # inclui objective + key_requirements
        assert entry[k] == wiki[k]
    assert "key_facts" not in entry
    assert "proposal_sections" not in entry


def test_stage1_trl_discriminates_with_card_field_flatlines_without():
    p = CompanyProfile(trl=4)
    in_range = {"trl_range": {"min": 3, "max": 6}}
    out_range = {"trl_range": {"min": 8, "max": 9}}
    # COM o campo no card → a dimensão discrimina (edital compatível ≠ incompatível)
    assert _score_trl(in_range, p, W) != _score_trl(out_range, p, W)
    # SEM o campo (regressão cloud) → flatline: mesmo valor para qualquer card
    assert _score_trl({}, p, W) == _score_trl({"foo": 1}, p, W) == W["trl"] / 2


def test_stage1_mecanismo_discriminates_with_card_field_flatlines_without():
    p = CompanyProfile(tipos_financiamento_interesse=["subvencao_nao_reembolsavel"])
    assert _score_mecanismo({"mechanism": "subvencao"}, p, W) != \
        _score_mecanismo({"mechanism": "credito"}, p, W)
    assert _score_mecanismo({}, p, W) == W["mecanismo"] / 2  # ausente → flatline


def test_build_carry_forward_promotes_from_wiki_store(monkeypatch):
    """build_knowledge_graph carrega adiante os campos de match da wiki store ao
    rebuildar → índice nunca degrada se a síntese ainda não rodou (desacopla o
    índice da síntese flaky/rate-limited). Edital sem wiki fica cru (esperado)."""
    from pipeline import build_knowledge_graph as bkg
    store = {"finep:1": {"mechanism": "subvencao", "objective": "x",
                         "trl_range": {"min": 1, "max": 9}, "key_requirements": ["r"]}}
    monkeypatch.setattr("core.kg.kg_store.load_wiki_page", lambda eid: store.get(eid))
    index = {"editais": [{"id": "finep:1", "title": "a"}, {"id": "finep:2", "title": "b"}]}
    bkg._carry_forward_match_fields(index)
    assert index["editais"][0]["mechanism"] == "subvencao"
    assert index["editais"][0]["objective"] == "x"
    assert index["editais"][0]["key_requirements"] == ["r"]
    assert "mechanism" not in index["editais"][1]  # sem wiki → cru, sem inventar


def test_stage1_contrapartida_absence_gives_full_points_regression():
    """Documenta o comportamento de risco: counterpart_required AUSENTE → pontos
    cheios (não w/2). Em cloud isso premiava indevidamente editais que EXIGEM
    contrapartida mas tiveram o campo perdido — reforço de por que promover o campo."""
    mei = CompanyProfile(tamanho_empresa="MEI")
    assert _score_contrapartida({"counterpart_required": True}, mei, W) == 0   # exige + MEI → 0
    assert _score_contrapartida({}, mei, W) == W["contrapartida"]              # ausente → cheio
