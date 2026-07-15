from __future__ import annotations

from pipeline.adapters import fapesc


def test_adapter_preserva_base_e_retificacao(monkeypatch):
    monkeypatch.setattr(fapesc, "_load_latest_bronze", lambda: [{
        "native_id": "31-2026",
        "documentos_normativos": [
            {"doc_name": "Edital.pdf", "url": "https://x/Edital.pdf",
             "family": "edital-base", "text": "4. Critérios"},
            {"doc_name": "Retificacao.pdf", "url": "https://x/Retificacao.pdf",
             "family": "emenda", "text": "Altera o item 4.2"},
        ],
        "texto_cru": "fallback",
    }])
    docs = fapesc.Adapter().to_documents("31-2026")
    assert [d["doc_name"] for d in docs] == ["Edital.pdf", "Retificacao.pdf"]
    assert docs[0]["metadata"]["composition_order"] == 0
    assert docs[1]["metadata"]["family"] == "emenda"
    assert all(d["metadata"]["authority_state"] == "vigente" for d in docs)
