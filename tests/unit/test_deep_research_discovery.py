"""Testes do canal de descoberta via Deep Research (spec discovery-deep-research.md).

Cobre o contrato do canal: pesquisa → pacote de evidências → registros prontos
pro staging (com citações/data/confiança/campos ausentes/conflitos), dedup,
fail-open determinístico e os loaders de config (schema).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.core import deep_research as dr
from radar.core.ingestion import deep_research_discovery as drd
from radar.core.ingestion import opportunity_discovery as od
from radar.core.kg import schema as ws

pytestmark = pytest.mark.unit


def _result(*sources, answer="Síntese com citação.", stop_reason="end_turn"):
    return dr.DeepResearchResult(
        answer=answer,
        sources=[dr.Source(title=t, url=u, snippet=s) for t, u, s in sources],
        stop_reason=stop_reason,
    )


_TARGETS = [
    {"key": "credit_lines", "brief": "linhas de crédito abertas no Brasil", "type_hint": "edital"},
]


def _run(monkeypatch, result, *, targets=None, max_findings=10, exclude=None, provider="anthropic"):
    monkeypatch.setattr(dr, "run_deep_research", lambda q, **kw: result)
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    return drd.run_deep_research_channel(
        targets if targets is not None else _TARGETS,
        max_findings=max_findings, provider=provider,
        exclude_urls=set(exclude or []),
    )


# ---------------------------------------------------------------------------
# Contrato: fonte citada → registro de staging + pacote de evidências
# ---------------------------------------------------------------------------

def test_channel_converte_fontes_em_candidatos_de_staging(monkeypatch):
    records = _run(monkeypatch, _result(
        ("BNDES Crédito Inovação", "https://bndes.gov.br/credito", "linhas para inovação"),
        ("Agência Fomento", "https://fomento.estado.gov.br/linha", "condições e acesso"),
    ))
    assert len(records) == 2
    r = records[0]
    assert r["url"] == od.normalize_web_url("https://bndes.gov.br/credito")
    assert r["url_hash"] == od.web_url_hash("https://bndes.gov.br/credito")
    assert r["title"].startswith("BNDES")
    assert r["fonte"] == "Deep Research"
    assert r["verificacao"] == "provisorio"
    assert r["opportunity_type"] == "edital"

    pkg = r["evidence_package"]
    assert pkg["identity"]["collector"] == "deep_research"
    assert pkg["identity"]["canonical_url"] == r["url"]

    block = pkg["deep_research"]
    assert block["target_key"] == "credit_lines"
    assert block["confidence"] == "high"  # 2 citações
    assert len(block["citations"]) == 2
    assert block["citations"][0]["url"] == "https://bndes.gov.br/credito"
    assert "Síntese com citação" in block["answer"]
    assert block["researched_at"]
    assert block["conflicts"] == []
    assert block["conflicts_resolution"] == "staged_for_review"
    assert block["relationship_to_source"] == "sintese_com_citacao"
    # campos ausentes preservados (nunca preenchidos silenciosamente)
    assert "prazo_envio" in block["missing_fields"]
    assert "publico_alvo" in block["missing_fields"]
    assert "descricao" not in block["missing_fields"]  # snippet alimenta descricao


def test_channel_evidencia_confianca_por_citacoes(monkeypatch):
    baixo = _run(monkeypatch, _result(("Só uma", "https://a.com/x", "trecho")))
    assert baixo[0]["evidence_package"]["deep_research"]["confidence"] == "medium"
    # sem snippet, descricao entra nos missing_fields (nunca inventada)
    sem_snippet = _run(monkeypatch, _result(("Só URL", "https://a.com/y", "")))
    block = sem_snippet[0]["evidence_package"]["deep_research"]
    assert "descricao" in block["missing_fields"]
    vazio = _run(monkeypatch, _result())
    assert vazio == []


# ---------------------------------------------------------------------------
# Dedup (ledger/KG, social, dedicados) + cap
# ---------------------------------------------------------------------------

def test_channel_dedup_known_social_e_dedicados(monkeypatch):
    rec = _run(monkeypatch, _result(
        ("Novo", "https://novo.gov.br/linha", "trecho"),
        ("Já visto", "https://visto.gov.br/x", "trecho"),
        ("Instagram", "https://instagram.com/p/1", "trecho"),
        ("FINEP dedicada", "https://finep.gov.br/edital", "trecho"),
    ), exclude=["https://visto.gov.br/x"])
    urls = {r["url"] for r in rec}
    assert urls == {od.normalize_web_url("https://novo.gov.br/linha")}


def test_channel_respeita_max_findings(monkeypatch):
    rec = _run(monkeypatch, _result(
        ("A", "https://a.com/1", "x"), ("B", "https://b.com/2", "x"),
        ("C", "https://c.com/3", "x"),
    ), max_findings=2)
    assert len(rec) == 2


# ---------------------------------------------------------------------------
# Fail-open (aceite 5): nunca levanta, degrada para []
# ---------------------------------------------------------------------------

def test_channel_agente_erro_devolve_vazio(monkeypatch):
    assert _run(monkeypatch, _result(stop_reason="error")) == []


def test_channel_engine_levantando_nao_propaga(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("credencial indisponível")

    monkeypatch.setattr(dr, "run_deep_research", boom)
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    assert drd.run_deep_research_channel(_TARGETS) == []


def test_channel_sem_alvos_ou_sem_engine(monkeypatch):
    assert drd.run_deep_research_channel([]) == []
    # engine import falha → fail-open
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "radar.core.deep_research":
            raise ImportError("sem dependência")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert drd.run_deep_research_channel(_TARGETS) == []


# ---------------------------------------------------------------------------
# Loaders de config (docs/domain/sources/_discovery.md é autoritativo)
# ---------------------------------------------------------------------------

def test_deep_research_targets_do_doc():
    targets = ws.deep_research_targets()
    assert {t["key"] for t in targets} == {
        "credit_lines", "corporate_challenges",
        "accelerators_incubators", "ict_labs", "new_sources",
    }
    for t in targets:
        assert t["brief"]
        assert t["type_hint"] in {"edital", "desafio", "programa", "ict"}
    assert ws.deep_research_config()["max_findings"] >= 1
