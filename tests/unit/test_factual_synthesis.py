from __future__ import annotations

import pytest

from radar.core.services import factual_synthesis

pytestmark = pytest.mark.unit


def test_group_evidence_preserva_subfamilias_e_proveniencia():
    groups = factual_synthesis._group_evidence([
        {
            "section": "REGULAMENTO – 3ª RERRATIFICAÇÃO > 4.3.2",
            "source_file": "Regulamento.pdf", "page_range": "p.4",
            "text": "Pagamento de pessoal",
            "metadata": {"revision": 3, "authority_state": "vigente"},
        },
        {
            "section": "REGULAMENTO – 3ª RERRATIFICAÇÃO > 4.5.6",
            "source_file": "Regulamento.pdf", "page_range": "p.6",
            "text": "Contrapartida",
            "metadata": {"revision": 3, "authority_state": "vigente"},
            "structural_expansion": True,
        },
    ])
    assert list(groups) == ["4.3", "4.5"]
    assert "versão=3" in groups["4.3"][0]
    assert "autoridade=vigente" in groups["4.5"][0]


def test_sintese_exige_todos_os_grupos_no_prompt(monkeypatch):
    monkeypatch.setattr(factual_synthesis, "retrieve_edital_evidence", lambda *a, **k: [
        {"section": "4.2 Empresa", "text": "Empresa", "metadata": {}},
        {"section": "4.3 Coordenador", "text": "Coordenador", "metadata": {},
         "structural_expansion": True},
        {"section": "4.4 Equipe", "text": "Equipe", "metadata": {},
         "structural_expansion": True},
        {"section": "4.5 Proposta", "text": "Proposta", "metadata": {},
         "structural_expansion": True},
    ])
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = type("Message", (), {"content": "resposta"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice]})()

    client = type(
        "Client", (),
        {"chat": type("Chat", (), {"completions": _Completions()})()},
    )()
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setattr(factual_synthesis, "make_client", lambda **kwargs: client)

    answer = factual_synthesis.synthesize_enumerative_answer("fapesc:31", "critérios?")
    assert answer.endswith("resposta")
    assert answer.startswith("**Fonte normativa vigente:**")
    prompt = captured["messages"][1]["content"]
    assert "4.2, 4.3, 4.4, 4.5" in prompt
