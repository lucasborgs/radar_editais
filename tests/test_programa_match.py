"""Testes do motor de match-por-aderência de programas (core/entity_matcher).

LLM mockado — sem custo de rede. Cobre: formatação do catálogo (estágio +
multissetorial), parse + enriquecimento do match, e degradação sem LLM. Espelha
tests/test_investor_match.py.
"""
from __future__ import annotations

import json

from core.services import entity_matcher
from domain.user_profile import CompanyProfile


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


def test_format_programa_props_includes_estagio_and_multissetorial():
    p = {"id": "programa:a", "name": "A", "tipo": "subvencao", "operador": "X",
         "estagio_alvo": ["pre-seed"], "setores": [], "tese_themes": [],
         "elegibilidade": "MEI não pode"}
    out = entity_matcher._format_programa_props(p)
    assert "tipo:subvencao" in out
    assert "estagio:pre-seed" in out
    assert "multissetorial" in out


def test_match_programas_parses_and_enriches(monkeypatch):
    canned = json.dumps({"matches": [
        {"id": "programa:centelha", "name": "Programa Centelha", "score": 9.0,
         "match_dimensions": {"estagio": "pre-seed bate", "elegibilidade": "ok", "tema": "—"},
         "justificativa": "forte aderência por estágio"}
    ]})
    monkeypatch.setattr(entity_matcher, "_make_client",
                        lambda: (_FakeClient(canned), "fake"))
    profile = CompanyProfile(nome="Acme", estagio="pre-seed", solution_summary="deep-tech")
    out = entity_matcher.EntityMatcher(
        entity_matcher.catalog_programas
    ).match(profile, top_k=3)
    assert len(out) == 1
    m = out[0]
    assert m["id"] == "programa:centelha"
    assert m["tipo"] == "subvencao"
    assert m["site"] == "https://programacentelha.com.br"
    assert "pre-seed" in m["estagio_alvo"]


def test_match_programas_degrades_without_llm(monkeypatch):
    def _raise():
        raise ValueError("sem credencial")
    monkeypatch.setattr(entity_matcher, "_make_client", _raise)
    out = entity_matcher.EntityMatcher(
        entity_matcher.catalog_programas
    ).match(CompanyProfile(nome="X"))
    assert out == []
