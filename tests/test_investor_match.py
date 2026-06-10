"""Testes do motor de match-por-tese do investidor (core/investor_match).

LLM mockado — sem custo de rede. Cobre: formatação do catálogo (distinção de
modo tese vs generalista), parse + enriquecimento do match, e degradação sem LLM.
"""
from __future__ import annotations

import json

from core.services import investor_match
from domain.user_profile import CompanyProfile


# --- fake do client OpenAI (interface chat.completions.create) ---------------
class _Msg:
    def __init__(self, content): self.content = content
class _Choice:
    def __init__(self, content): self.message = _Msg(content)
class _Resp:
    def __init__(self, content): self.choices = [_Choice(content)]
class _Completions:
    def __init__(self, content): self._content = content
    def create(self, **kw): return _Resp(self._content)
class _Chat:
    def __init__(self, content): self.completions = _Completions(content)
class _FakeClient:
    def __init__(self, content): self.chat = _Chat(content)


def test_format_catalog_marks_generalista():
    invs = [
        {"id": "investidor:a", "name": "A", "generalista": False, "tese_themes": ["x"],
         "estagio_alvo": ["seed"], "setores": ["s"], "tese": "tese específica"},
        {"id": "investidor:b", "name": "B", "generalista": True, "tese_themes": [],
         "estagio_alvo": ["seed"], "setores": ["multissetorial"], "tese": "generalista"},
    ]
    out = investor_match._format_catalog(invs)
    assert "modo:tese" in out
    assert "modo:GENERALISTA" in out


def test_match_parses_and_enriches(monkeypatch):
    canned = json.dumps({"matches": [
        {"id": "investidor:indicator-capital", "name": "Indicator Capital", "score": 9.0,
         "generalista": False,
         "match_dimensions": {"tese": "deep-tech bate", "setor": "—", "estagio": "seed ok"},
         "justificativa": "forte aderência deep-tech"}
    ]})
    monkeypatch.setattr(investor_match, "_make_client", lambda: (_FakeClient(canned), "fake"))
    profile = CompanyProfile(nome="Acme", estagio="seed", solution_summary="hardware deep-tech")
    out = investor_match.match_investidores(profile, top_k=3)
    assert len(out) == 1
    m = out[0]
    assert m["id"] == "investidor:indicator-capital"
    # enriquecido com dados do diretório real (não veio do LLM)
    assert m["site"] == "https://indicator.capital"
    assert m["lead_follow"] == "lead"
    assert "seed" in m["estagio_alvo"]


def test_match_degrades_without_llm(monkeypatch):
    def _raise():
        raise ValueError("sem credencial")
    monkeypatch.setattr(investor_match, "_make_client", _raise)
    out = investor_match.match_investidores(CompanyProfile(nome="X"))
    assert out == []
