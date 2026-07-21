from __future__ import annotations

import pytest

from core.services import factual_retrieval

pytestmark = pytest.mark.unit


def test_enumerative_desliga_hyde_expande_e_nao_usa_analogos(monkeypatch):
    captured = {}

    def fake_retrieve(db, edital_ids, query, **kwargs):
        captured.update(db=db, edital_ids=edital_ids, query=query, **kwargs)
        return []

    monkeypatch.setattr(factual_retrieval, "retrieve_chunks", fake_retrieve)
    factual_retrieval.retrieve_edital_evidence(
        "fapesc:31-2026", "quais os critérios?", profile="factual_enumerative",
    )
    assert captured["edital_ids"] == ["fapesc:31-2026"]
    assert captured["hyde"] is False
    assert captured["expand_sections"] is True
    assert captured["max_per_source"] == 0


def test_formatacao_preserva_proveniencia():
    out = factual_retrieval.format_factual_evidence([{
        "id": "chunk-1", "edital_id": "finep:745", "source_file": "Regulamento.pdf",
        "section": "4.3", "page_range": "p.8", "text": "Pagamento de pessoal",
        "metadata": {"revision": 3, "published_at": "2026-02-09",
                     "authority_state": "vigente", "source_url": "https://fonte"},
    }])
    assert "chunk-1" in out and "versão=3" in out
    assert "data=2026-02-09" in out and "seção=4.3" in out and "página=p.8" in out


def test_formatacao_antecipa_checklist_das_subfamilias_expandidas():
    chunks = [
        {"id": "a", "section": "4.2 Empresa", "text": "A", "metadata": {}},
        {"id": "b", "section": "4.3 Coordenador", "text": "B", "metadata": {},
         "structural_expansion": True},
        {"id": "c", "section": "4.4 Equipe", "text": "C", "metadata": {},
         "structural_expansion": True},
        {"id": "d", "section": "10.3 Mérito", "text": "D", "metadata": {}},
    ]
    out = factual_retrieval.format_factual_evidence(chunks)
    checklist = out.split("[EVIDÊNCIA", 1)[0]
    assert "4.2, 4.3, 4.4" in checklist
    assert "10.3" not in checklist
