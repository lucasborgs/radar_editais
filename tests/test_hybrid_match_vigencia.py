"""Vigência em runtime no HybridMatchService (defesa-em-profundidade §7.1).

O índice é filtrado por prazo no build, mas o cron pode não rebuildar entre
prazos vencendo. `_get_editais_with_cards` re-filtra em runtime para nunca
expor edital com prazo vencido ao matching — mantendo fluxo contínuo
(deadline ausente) e prazos futuros.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from core.hybrid_match_service import HybridMatchService


def _svc_with_index(editais: list[dict]) -> HybridMatchService:
    svc = HybridMatchService()
    svc._index = {"reference_date": "2026-05-31", "editais": editais}
    return svc


def test_expired_edital_excluded_from_candidates():
    ontem = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    amanha = (date.today() + timedelta(days=30)).strftime("%d/%m/%Y")
    svc = _svc_with_index([
        {"id": "finep:EXPIRADO", "title": "vencido", "status": "ABERTA",
         "deadline": ontem, "themes": ["ia"], "source": "finep"},
        {"id": "finep:VALIDO", "title": "futuro", "status": "ABERTA",
         "deadline": amanha, "themes": ["ia"], "source": "finep"},
    ])
    with patch.object(svc, "_load_index", lambda: None), \
         patch.object(svc, "_load_wiki_page", lambda eid: None):
        ids = [c["id"] for c in svc._get_editais_with_cards()]
    assert "finep:EXPIRADO" not in ids
    assert "finep:VALIDO" in ids


def test_continuous_flow_no_deadline_kept():
    """Prazo ausente = fluxo contínuo = vigente (espelha _deadline_expired)."""
    svc = _svc_with_index([
        {"id": "finep:CONTINUO", "title": "sem prazo", "status": "ABERTA",
         "deadline": None, "themes": ["ia"], "source": "finep"},
        {"id": "finep:VAZIO", "title": "prazo vazio", "status": "ABERTA",
         "deadline": "", "themes": ["ia"], "source": "finep"},
    ])
    with patch.object(svc, "_load_index", lambda: None), \
         patch.object(svc, "_load_wiki_page", lambda eid: None):
        ids = [c["id"] for c in svc._get_editais_with_cards()]
    assert ids == ["finep:CONTINUO", "finep:VAZIO"]


def test_deadline_today_still_valid():
    """Prazo == hoje ainda vale (expira só quando d < hoje)."""
    hoje = date.today().strftime("%d/%m/%Y")
    svc = _svc_with_index([
        {"id": "finep:HOJE", "title": "vence hoje", "status": "ABERTA",
         "deadline": hoje, "themes": ["ia"], "source": "finep"},
    ])
    with patch.object(svc, "_load_index", lambda: None), \
         patch.object(svc, "_load_wiki_page", lambda eid: None):
        ids = [c["id"] for c in svc._get_editais_with_cards()]
    assert ids == ["finep:HOJE"]
