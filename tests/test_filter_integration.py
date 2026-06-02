"""
Integração do filtro PME no build_knowledge_graph (Épico D).

Testa que entries normalizados pelo `_build_editais` são corretamente
divididos em accepted/rejections por `_apply_pme_filter`, e que o log
estruturado tem o shape esperado para o CLI de inspeção.

Não toca FS — apenas funções puras.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from pipeline.build_knowledge_graph import (  # noqa: E402
    _apply_pme_filter,
    _editais_to_filter_metadata,
)

# =============================================================================
# FIXTURES — entries pós-_build_editais (shape estável: id, source, title,
# publico_alvo canonicalizado §5.5, subprogramas §5.6, fonte_recurso §5.4, …)
# =============================================================================

FINEP_SUBVENCAO_EMPRESA = {
    "id": "finep:782", "source": "finep",
    "title": "Chamada Pública FINEP — Subvenção Econômica IA Indústria",
    "status": "ABERTA", "deadline": "31/12/2026",
    "publico_alvo": ["Empresas"],
    "fonte_recurso": ["FINEP", "FNDCT"],
    "subprogramas": [],
}

FINEP_CENTELHA = {
    "id": "finep:790", "source": "finep",
    "title": "Programa Centelha — Apoio a Negócios Inovadores",
    "status": "ABERTA", "deadline": "30/06/2026",
    "publico_alvo": ["Empresas"],
    "fonte_recurso": ["FINEP"],
    "subprogramas": ["Centelha"],
}

# Edital de infraestrutura científica — só ICTs, deve ser rejeitado
FINEP_CT_INFRA = {
    "id": "finep:701", "source": "finep",
    "title": "Chamada CT-Infra — Equipamentos multiusuário em ICTs",
    "status": "ABERTA", "deadline": "15/08/2026",
    "publico_alvo": ["ICTs", "Universidades"],
    "fonte_recurso": ["FINEP", "FNDCT"],
    "subprogramas": ["CT-Infra"],
}

FAPESP_PIPE = {
    "id": "fapesp:18064", "source": "fapesp",
    "title": "Chamada PIPE Jornada Tecnológica Agro — Fase 1",
    "modalidade": "PIPE - Fase 1",
    "status": "ABERTA", "deadline": "17/06/2026",
    "publico_alvo": ["Empresas"],
    "subprogramas": [],
}

FAPESP_BOLSA_DR = {
    "id": "fapesp:9001", "source": "fapesp",
    "title": "Bolsa de Doutorado - Edital genérico",
    "modalidade": "Bolsa",
    "status": "ABERTA", "deadline": "20/07/2026",
    "publico_alvo": ["Pesquisadores"],
    "subprogramas": [],
}

# Edital sem qualquer sinal — vai pra unclear
EDITAL_AMBIGUO = {
    "id": "finep:999", "source": "finep",
    "title": "Apoio a redes de formação em ambiente corporativo",
    "status": "ABERTA", "deadline": "30/06/2026",
    "publico_alvo": [],
    "fonte_recurso": [],
    "subprogramas": [],
}


# =============================================================================
# _editais_to_filter_metadata
# =============================================================================

def test_metadata_mapping_uses_subprogramas_as_programa():
    """FINEP bronze não emite `programa`; subprogramas vira proxy."""
    md = _editais_to_filter_metadata(FINEP_CENTELHA)
    assert md["programa"] == "Centelha"
    assert md["titulo"] == FINEP_CENTELHA["title"]
    assert md["publico_alvo"] == ["Empresas"]


def test_metadata_mapping_modalidade_passes_through():
    """FAPESP bronze (futuro) emite `modalidade` explícito."""
    md = _editais_to_filter_metadata(FAPESP_PIPE)
    assert md["modalidade"] == "PIPE - Fase 1"


def test_metadata_mapping_handles_missing_fields():
    md = _editais_to_filter_metadata({"id": "x:1", "title": "Foo"})
    assert md["titulo"] == "Foo"
    assert md["programa"] is None
    assert md["publico_alvo"] == []


# =============================================================================
# _apply_pme_filter — accepted/rejected partition
# =============================================================================

def test_filter_accepts_finep_subvencao_by_publico():
    accepted, rejections = _apply_pme_filter([FINEP_SUBVENCAO_EMPRESA])
    assert len(accepted) == 1
    assert accepted[0]["id"] == "finep:782"
    assert rejections == []


def test_filter_accepts_finep_centelha_by_subprograma():
    accepted, rejections = _apply_pme_filter([FINEP_CENTELHA])
    assert len(accepted) == 1
    assert rejections == []


def test_filter_accepts_fapesp_pipe_by_modalidade():
    accepted, rejections = _apply_pme_filter([FAPESP_PIPE])
    assert len(accepted) == 1
    assert accepted[0]["source"] == "fapesp"
    assert rejections == []


def test_filter_rejects_bolsa_doutorado():
    accepted, rejections = _apply_pme_filter([FAPESP_BOLSA_DR])
    assert accepted == []
    assert len(rejections) == 1
    assert rejections[0]["decision"] == "reject"
    assert rejections[0]["reason"].startswith("exclusor:")


def test_filter_rejects_ct_infra_as_unclear_or_excluded():
    """CT-Infra tem publico=[ICTs, Universidades] (fora da whitelist PME) e
    nenhum exclusor — cai em unclear."""
    accepted, rejections = _apply_pme_filter([FINEP_CT_INFRA])
    assert accepted == []
    assert len(rejections) == 1
    assert rejections[0]["decision"] == "unclear"


def test_filter_marks_unclear_for_ambiguous():
    accepted, rejections = _apply_pme_filter([EDITAL_AMBIGUO])
    assert accepted == []
    assert rejections[0]["decision"] == "unclear"
    assert rejections[0]["reason"] == "sem-sinal"


# =============================================================================
# Log shape — campos consumidos pelo scripts/list_filter_rejections.py
# =============================================================================

def test_rejection_log_has_expected_shape():
    _, rejections = _apply_pme_filter([FAPESP_BOLSA_DR])
    r = rejections[0]
    expected_keys = {"logged_at", "source", "edital_id", "title", "decision", "reason", "deadline"}
    assert expected_keys <= set(r.keys())
    assert r["source"] == "fapesp"
    assert r["edital_id"] == "fapesp:9001"
    assert r["deadline"] == "20/07/2026"


# =============================================================================
# Mixed batch (multifonte)
# =============================================================================

def test_filter_partitions_multifonte_batch():
    batch = [
        FINEP_SUBVENCAO_EMPRESA, FINEP_CENTELHA, FINEP_CT_INFRA,
        FAPESP_PIPE, FAPESP_BOLSA_DR, EDITAL_AMBIGUO,
    ]
    accepted, rejections = _apply_pme_filter(batch)
    assert len(accepted) == 3
    assert len(rejections) == 3
    accepted_ids = {e["id"] for e in accepted}
    assert accepted_ids == {"finep:782", "finep:790", "fapesp:18064"}

    decisions = {r["edital_id"]: r["decision"] for r in rejections}
    assert decisions == {
        "finep:701": "unclear",   # CT-Infra
        "fapesp:9001": "reject",  # bolsa doutorado (exclusor)
        "finep:999": "unclear",   # ambíguo
    }


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
