"""Testes do subconjunto público de proveniência (RT01-T10).

Cobre:
  - public_provenance: stated+exact, unresolved, inferred, vazio, None,
    malformado, campos operacionais ausentes do output;
  - entity_catalog: _row_to_card, _curated_card, get_investidor,
    get_programa com provenance no row;
  - explore_tools: get_edital e get_investidor incluem dict público
    no payload textual.
"""
from __future__ import annotations

import pytest

from radar.core.kg.provenance_read import public_provenance

pytestmark = pytest.mark.unit


# ===========================================================================
# public_provenance — função pura
# ===========================================================================

_STATED_EXACT_REF = {
    "schema_version": 1,
    "source": "finep",
    "native_id": "745",
    "document": "Edital.pdf",
    "page": 17,
    "quote": "Poderão participar empresas brasileiras...",
    "canonical_content_hash": "sha256:abc",
    "locator_quality": "exact",
    "source_url": "https://exemplo.com/edital.pdf",
    "collected_at": "2026-07-21T12:00:00Z",
}

_STATED_EXACT_STORED = {
    "deadline": {
        "state": "stated",
        "evidence_refs": [_STATED_EXACT_REF],
        "producer": {"kind": "adapter", "name": "finep_adapter", "version": "1"},
        "derivation": None,
        "validations": [],
        "review": None,
        "updated_at": "2026-07-21T12:00:00Z",
    },
}


def test_public_provenance_stated_exact_includes_state_and_citation():
    result = public_provenance(_STATED_EXACT_STORED)
    deadline = result.get("deadline")
    assert deadline is not None
    assert deadline["state"] == "stated"
    assert len(deadline["citations"]) == 1
    c = deadline["citations"][0]
    assert c["document"] == "Edital.pdf"
    assert c["page"] == 17
    assert "Poderão participar" in c["quote"]
    assert c["source_url"] == "https://exemplo.com/edital.pdf"
    assert c["collected_at"] == "2026-07-21T12:00:00Z"


def test_public_provenance_unresolved_ref_no_citation():
    """unresolved → state preservado, citations vazio."""
    stored = {
        "deadline": {
            "state": "stated",
            "evidence_refs": [{
                "schema_version": 1,
                "source": "finep",
                "quote": "trecho sem posição",
                "canonical_content_hash": "sha256:abc",
                "locator_quality": "unresolved",
            }],
            "producer": {"kind": "adapter", "name": "finep_adapter", "version": "1"},
        },
    }
    result = public_provenance(stored)
    assert result["deadline"]["state"] == "stated"
    assert result["deadline"]["citations"] == []


def test_public_provenance_document_only_ref_included():
    """document_only tem citação (document identificado, sem coordenada)."""
    stored = {
        "deadline": {
            "state": "stated",
            "evidence_refs": [{
                "schema_version": 1,
                "source": "finep",
                "document": "Edital.pdf",
                "quote": "prazo: 31/10/2026",
                "canonical_content_hash": "sha256:def",
                "locator_quality": "document_only",
            }],
        },
    }
    result = public_provenance(stored)
    assert len(result["deadline"]["citations"]) == 1
    assert result["deadline"]["citations"][0]["document"] == "Edital.pdf"


def test_public_provenance_inferred_sem_refs_citations_empty():
    stored = {
        "setores": {
            "state": "inferred",
            "evidence_refs": [],
            "producer": {"kind": "llm", "name": "gold_tagger"},
        },
    }
    result = public_provenance(stored)
    assert result["setores"]["state"] == "inferred"
    assert result["setores"]["citations"] == []


def test_public_provenance_empty_dict_returns_empty():
    assert public_provenance({}) == {}


def test_public_provenance_none_returns_empty():
    assert public_provenance(None) == {}


def test_public_provenance_malformed_not_dict_returns_empty():
    assert public_provenance("não é dict") == {}
    assert public_provenance(42) == {}


def test_public_provenance_malformed_entry_skipped():
    """Path com valor não-dict é ignorado (não quebra)."""
    stored = {"deadline": "string em vez de dict"}
    assert public_provenance(stored) == {}


def test_public_provenance_operational_fields_absent():
    """producer, derivation, validations, review NUNCA aparecem."""
    result = public_provenance(_STATED_EXACT_STORED)
    deadline = result["deadline"]
    assert "producer" not in deadline
    assert "derivation" not in deadline
    assert "validations" not in deadline
    assert "review" not in deadline
    assert "updated_at" not in deadline


def test_public_provenance_adversarial_producer_never_leaks():
    """Garantia adversarial: mesmo que stored tenha producer, ele não vaza."""
    stored = {
        "deadline": {
            "state": "stated",
            "evidence_refs": [_STATED_EXACT_REF],
            "producer": {"kind": "adapter", "name": "finep_adapter",
                         "version": "1", "api_key": "sk-secret"},
        },
    }
    result = public_provenance(stored)
    assert "producer" not in result["deadline"]
    assert "api_key" not in str(result)


# ===========================================================================
# entity_catalog — _row_to_card com provenance
# ===========================================================================

def _row_to_card_direct(row: dict) -> dict:
    """Chama _row_to_card com stubs mínimos (função pura)."""
    from radar.core.kg.entity_catalog import _row_to_card
    return _row_to_card(row, programs={}, icts={}, agencies={})


def test_row_to_card_com_provenance_inclui_chave() -> None:
    row = {
        "id": "uuid-1",
        "native_id": "finep:589",
        "source": "finep",
        "kind": "edital",
        "name": "Edital Teste",
        "deadline": "2026-12-31",
        "status": "aberta",
        "setores": ["saúde"],
        "tecnologias_tags": ["IA"],
        "description": "Descrição",
        "formato": "fluxo contínuo",
        "mecanismo": "subvenção",
        "requisitos_texto": [],
        "constraints": [],
        "updated_at": "2026-07-21T00:00:00",
        "metadata": {},
        "provenance": {
            "deadline": {
                "state": "stated",
                "evidence_refs": [{
                    "schema_version": 1,
                    "source": "finep",
                    "document": "Edital.pdf",
                    "page": 5,
                    "quote": "prazo: 31/12/2026",
                    "canonical_content_hash": "sha256:abc",
                    "locator_quality": "exact",
                }],
                "producer": {"kind": "adapter", "name": "finep_adapter", "version": "1"},
            },
        },
    }
    card = _row_to_card_direct(row)
    assert "provenance" in card
    assert card["provenance"]["deadline"]["state"] == "stated"
    assert len(card["provenance"]["deadline"]["citations"]) == 1


def test_row_to_card_legado_sem_provenance_retorna_vazio() -> None:
    row = {
        "id": "uuid-2",
        "native_id": "finep:590",
        "source": "finep",
        "kind": "edital",
        "name": "Edital Legado",
        "deadline": None,
        "status": "aberta",
        "setores": [],
        "tecnologias_tags": [],
        "description": "",
        "formato": "",
        "mecanismo": None,
        "requisitos_texto": [],
        "constraints": [],
        "updated_at": "",
        "metadata": {},
    }
    card = _row_to_card_direct(row)
    assert "provenance" in card
    assert card["provenance"] == {}


def test_row_to_card_chaves_preexistentes_inalteradas() -> None:
    row = {
        "id": "uuid-3",
        "native_id": "finep:591",
        "source": "finep",
        "kind": "edital",
        "name": "Edital Stable",
        "deadline": "2026-12-31",
        "status": "aberta",
        "setores": ["energia"],
        "tecnologias_tags": [],
        "description": "Descrição estável",
        "formato": "fluxo contínuo",
        "mecanismo": "subvenção",
        "requisitos_texto": ["requisito A"],
        "constraints": [],
        "updated_at": "2026-07-21T00:00:00",
        "metadata": {"publico_alvo": "empresas"},
        "provenance": {"deadline": {"state": "stated", "evidence_refs": []}},
    }
    card = _row_to_card_direct(row)
    # snapshot de chaves pré-existentes
    assert card["id"] == "finep:591"
    assert card["title"] == "Edital Stable"
    assert card["source"] == "finep"
    assert card["status"] == "ABERTA"
    assert card["deadline"] == "31/12/2026"
    assert card["themes"] == ["energia"]
    assert card["objective"] == "Descrição estável"
    assert card["key_requirements"] == ["requisito A"]
    assert card["provenance"] == {"deadline": {"state": "stated", "citations": []}}


# ===========================================================================
# entity_catalog — _curated_card com provenance
# ===========================================================================

def _curated_card_direct(row: dict) -> dict:
    from radar.core.kg.entity_catalog import _curated_card
    return _curated_card(row, agencies={})


def test_curated_card_com_provenance_inclui_chave() -> None:
    row = {
        "id": "uuid-p1",
        "native_id": "programa:centelha",
        "source": "finep",
        "kind": "programa",
        "name": "Centelha",
        "status": "ativa",
        "deadline": None,
        "setores": ["inovação"],
        "tecnologias_tags": [],
        "description": "Programa Centelha",
        "formato": "",
        "mecanismo": None,
        "requisitos_texto": [],
        "constraints": [],
        "updated_at": "",
        "metadata": {},
        "provenance": {
            "name": {
                "state": "stated",
                "evidence_refs": [{
                    "schema_version": 1,
                    "source": "finep",
                    "document": "Edital.pdf",
                    "quote": "Programa Centelha",
                    "canonical_content_hash": "sha256:abc",
                    "locator_quality": "document_only",
                }],
            },
        },
    }
    card = _curated_card_direct(row)
    assert "provenance" in card
    assert card["provenance"]["name"]["state"] == "stated"
    assert len(card["provenance"]["name"]["citations"]) == 1
    assert card["provenance"]["name"]["citations"][0]["document"] == "Edital.pdf"


def test_curated_card_legado_sem_provenance() -> None:
    row = {
        "id": "uuid-p2",
        "native_id": "programa:legado",
        "source": "finep",
        "kind": "programa",
        "name": "Legado",
        "status": "inativa",
        "setores": [], "tecnologias_tags": [], "description": "",
        "formato": "", "mecanismo": None,
        "requisitos_texto": [], "constraints": [],
        "updated_at": "", "metadata": {},
    }
    card = _curated_card_direct(row)
    assert "provenance" in card
    assert card["provenance"] == {}


# ===========================================================================
# entity_catalog — get_investidor com provenance
# ===========================================================================

def test_get_investidor_com_provenance() -> None:
    import radar.core.kg.entity_catalog as ec

    def _fake_fetch(client, kind: str, native_id: str) -> dict | None:
        return {
            "id": "uuid-i1",
            "native_id": "investidor:barn-invest",
            "name": "Barn Invest",
            "description": "Greentech",
            "setores": ["agro"],
            "tecnologias_tags": [],
            "ticket_min": None,
            "ticket_max": None,
            "updated_at": "2026-07-21T00:00:00",
            "metadata": {
                "tese_themes": [],
                "verticais": ["agro", "energia"],
                "estagio_alvo": ["growth"],
                "portfolio": [],
                "co_investidores": [],
                "site": "https://barn.com",
                "source_urls": [],
                "verificado_em": None,
            },
            "provenance": {
                "setores": {
                    "state": "stated",
                    "evidence_refs": [{
                        "schema_version": 1,
                        "source": "finep",
                        "document": "site.pdf",
                        "page": 2,
                        "quote": "agro e energia",
                        "canonical_content_hash": "sha256:abc",
                        "locator_quality": "exact",
                    }],
                },
            },
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ec, "_fetch_one", _fake_fetch)
    monkeypatch.setattr(ec, "_client", lambda: None)
    try:
        card = ec.get_investidor("investidor:barn-invest")
        assert card is not None
        assert "provenance" in card
        assert card["provenance"]["setores"]["state"] == "stated"
        assert len(card["provenance"]["setores"]["citations"]) == 1
    finally:
        monkeypatch.undo()


def test_get_investidor_legado_sem_provenance() -> None:
    import radar.core.kg.entity_catalog as ec

    def _fake_fetch(client, kind: str, native_id: str) -> dict | None:
        return {
            "id": "uuid-i2",
            "native_id": "investidor:legado",
            "name": "Legado Fund",
            "description": "",
            "setores": [],
            "tecnologias_tags": [],
            "ticket_min": None,
            "ticket_max": None,
            "updated_at": "",
            "metadata": {},
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ec, "_fetch_one", _fake_fetch)
    monkeypatch.setattr(ec, "_client", lambda: None)
    try:
        card = ec.get_investidor("investidor:legado")
        assert card is not None
        assert "provenance" in card
        assert card["provenance"] == {}
    finally:
        monkeypatch.undo()


# ===========================================================================
# entity_catalog — get_programa com provenance
# ===========================================================================

def test_get_programa_com_provenance() -> None:
    import radar.core.kg.entity_catalog as ec

    def _fake_fetch(client, kind: str, native_id: str) -> dict | None:
        return {
            "id": "uuid-pr1",
            "native_id": "programa:centelha",
            "name": "Centelha",
            "description": "Programa Centelha",
            "setores": [],
            "tecnologias_tags": [],
            "ticket_min": None,
            "ticket_max": None,
            "formato": "fluxo contínuo",
            "updated_at": "",
            "metadata": {
                "operador": "FINEP",
                "tipo": "subvenção",
                "cadencia": "anual",
                "beneficio": "R$ 60k",
                "estagio_alvo": [],
                "elegibilidade": "",
                "site": "",
                "faq_url": "",
            },
            "provenance": {
                "name": {
                    "state": "stated",
                    "evidence_refs": [{
                        "schema_version": 1,
                        "source": "finep",
                        "document": "regulamento.pdf",
                        "quote": "Centelha",
                        "canonical_content_hash": "sha256:def",
                        "locator_quality": "document_only",
                    }],
                },
            },
        }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ec, "_fetch_one", _fake_fetch)
    monkeypatch.setattr(ec, "_client", lambda: None)
    try:
        card = ec.get_programa("programa:centelha")
        assert card is not None
        assert "provenance" in card
        assert card["provenance"]["name"]["state"] == "stated"
    finally:
        monkeypatch.undo()


# ===========================================================================
# explore_tools — get_edital factual inclui provenance no payload
# ===========================================================================

def test_get_edital_tool_inclui_provenance() -> None:
    from radar.core.kg import entity_catalog
    from radar.core.llm.agent_tools.explore_tools import build_explore_tools

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(entity_catalog, "get_edital", lambda eid: {
        "id": "finep:589",
        "title": "Edital Teste",
        "status": "ABERTA",
        "deadline": "31/12/2026",
        "objective": "Apoio a projetos de IA",
        "mechanism": "Subvenção",
        "eligible_entities": ["empresas"],
        "value": "",
        "themes": ["IA"],
        "key_requirements": [],
        "provenance": {
            "deadline": {
                "state": "stated",
                "citations": [{
                    "document": "Edital.pdf",
                    "page": 5,
                    "quote": "prazo: 31/12/2026",
                    "source_url": "https://exemplo.com/edital.pdf",
                    "collected_at": "2026-07-21T12:00:00Z",
                }],
            },
        },
    })
    try:
        tools = {t.name: t for t in build_explore_tools()}
        out = tools["get_edital"].invoke({"edital_id": "finep:589"})
        assert isinstance(out, str)
        assert "[PROVENANCE:deadline]" in out
        assert "state=stated" in out
        assert "Edital.pdf" in out
        assert "prazo: 31/12/2026" in out
    finally:
        monkeypatch.undo()


def test_get_investidor_tool_inclui_provenance() -> None:
    from radar.core.kg import entity_catalog
    from radar.core.llm.agent_tools.explore_tools import build_explore_tools

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(entity_catalog, "get_investidor", lambda _id: {
        "id": "investidor:barn-invest",
        "name": "Barn Invest",
        "tese": "Greentech",
        "setores": ["agro", "energia"],
        "tese_themes": [],
        "estagio_alvo": ["growth"],
        "portfolio": [],
        "ticket_range": {},
        "site": "https://barn.com",
        "verificado_em": None,
        "provenance": {
            "setores": {
                "state": "stated",
                "citations": [{
                    "document": "site.pdf",
                    "page": 2,
                    "quote": "agro e energia",
                    "source_url": "https://barn.com",
                    "collected_at": "2026-07-21T12:00:00Z",
                }],
            },
        },
    })
    try:
        tools = {t.name: t for t in build_explore_tools()}
        out = tools["get_investidor"].invoke({"investidor_id": "investidor:barn-invest"})
        assert isinstance(out, str)
        assert "[PROVENANCE:setores]" in out
        assert "state=stated" in out
    finally:
        monkeypatch.undo()
