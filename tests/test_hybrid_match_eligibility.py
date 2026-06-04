"""Fallback "nenhum elegível" do HybridMatch (Front 2).

Antes, quando ninguém passava o Stage 1, o match devolvia o top sem filtro
como se fossem recomendações — podia empurrar inelegível. Agora ainda devolve
os mais próximos, mas marca `eligible=False` + aviso, sinal claro pro frontend.
"""
from __future__ import annotations

from unittest.mock import patch

from core.hybrid_match_service import HybridMatchService
from domain.user_profile import CompanyProfile


def _ineligible_profile() -> CompanyProfile:
    return CompanyProfile(
        nome="ACME Software",
        tipo_entidade="empresa",
        tamanho_empresa="ME",
        trl=3,
        one_liner="Plataforma SaaS de gestão financeira para PMEs.",
        solution_summary="Software de contabilidade automatizada.",
        descricao_atividades="Desenvolvimento de software de gestão.",
        tipos_financiamento_interesse=["subvencao_nao_reembolsavel"],
    )


def _svc(editais: list[dict]) -> HybridMatchService:
    svc = HybridMatchService()
    svc._index = {"reference_date": "2026-05-31", "editais": editais}
    return svc


# Card desenhado para o perfil pontuar < 25 no Stage 1 em TODAS as dimensões.
_HOSTILE_CARD = {
    "id": "finep:HOSTIL", "title": "Edital para ICTs de pesca", "status": "ABERTA",
    "deadline": None, "source": "finep",
    "eligible_entities": ["universidades"], "publico_alvo": ["icts"],
    "themes": ["pesca artesanal"], "eligible_sectors": ["pesca"],
    "trl_range": {"min": 8, "max": 9}, "mechanism": "reembolsavel",
    "counterpart_required": True,
}


def test_no_eligible_marks_results_ineligible():
    svc = _svc([_HOSTILE_CARD])
    with patch.object(svc, "_load_wiki_page", lambda eid: None), \
         patch.object(svc, "_load_index", lambda: None), \
         patch("core.hybrid_match_service._call_stage2_scores", lambda eligible, profile: {}):
        results = svc.match(_ineligible_profile(), top_k=5)

    assert results, "fallback deve devolver os aproximados (utilidade), não lista vazia"
    assert all(r["eligible"] is False for r in results)
    assert all("eligibility_warning" in r for r in results)


def test_eligible_results_marked_true():
    """Sanity: quando há elegível, eligible=True e sem warning."""
    friendly = {
        "id": "finep:OK", "title": "Subvenção para software", "status": "ABERTA",
        "deadline": None, "source": "finep",
        "eligible_entities": ["empresas"], "publico_alvo": ["empresas"],
        "themes": ["software", "gestão financeira"],
        "eligible_sectors": ["software"],
        "trl_range": {"min": 3, "max": 7}, "mechanism": "subvencao",
        "counterpart_required": False,
    }
    svc = _svc([friendly])
    with patch.object(svc, "_load_wiki_page", lambda eid: None), \
         patch.object(svc, "_load_index", lambda: None), \
         patch("core.hybrid_match_service._call_stage2_scores",
               lambda eligible, profile: {"finep:OK": 8.0}), \
         patch("core.hybrid_match_service._call_stage2_explain",
               lambda top_results, profile: {}):
        results = svc.match(_ineligible_profile(), top_k=5)

    assert results
    assert all(r["eligible"] is True for r in results)
    assert all("eligibility_warning" not in r for r in results)
