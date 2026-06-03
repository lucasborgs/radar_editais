"""Testes da ingestão de descoberta no KG (item 2.2 — Fase A, incremento 1).

Cobre o caminho determinístico bronze-de-descoberta → entry de edital provisório.
O engine de descoberta (web_search + triagem + extração) vem no incremento 2.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.build_knowledge_graph import _build_discovery_editais


def _rec(**kw):
    base = {
        "source": "FAPESC",
        "native_id": "2026-001",
        "titulo": "Edital de Inovação SC",
        "link": "http://fapesc.sc.gov.br/e/2026-001",
        "prazo_envio": "31/12/2026",
        "tema": "tecnologias digitais e conectividade",
        "publico_alvo": "empresas",
        "descricao": "Chamada de fomento à inovação.",
        "status": "ABERTA",
    }
    base.update(kw)
    return base


def test_discovery_enters_as_provisorio():
    out = _build_discovery_editais([_rec()])
    assert len(out) == 1
    e = out[0]
    assert e["verificacao"] == "provisorio"
    assert e["id"] == "fapesc:2026-001"        # source normalizado (lower)
    assert e["source"] == "fapesc"


def test_discovery_themes_restricted_to_canonical_vocab():
    """Tema fora do vocab §5.9 é descartado (blinda a ponte/invariante)."""
    out = _build_discovery_editais([_rec(
        tema="tecnologias digitais e conectividade; bagulho-nao-canonico"
    )])
    assert out[0]["themes"] == ["tecnologias digitais e conectividade"]


def test_discovery_detects_ict_requirement():
    out = _build_discovery_editais([_rec(
        descricao="A proposta deverá contar com uma ICT coexecutora."
    )])
    assert out[0]["requires_ict_partner"] is True


def test_discovery_fallback_source_web_and_slug_id():
    """Sem source → 'web'; sem native_id → slug do link."""
    out = _build_discovery_editais([{
        "titulo": "Desafio aberto", "link": "http://x.org/abc",
        "descricao": "...", "status": "ABERTA",
    }])
    assert out[0]["source"] == "web"
    assert out[0]["id"].startswith("web:")


def test_discovery_dedup_by_id():
    out = _build_discovery_editais([_rec(), _rec()])
    assert len(out) == 1
