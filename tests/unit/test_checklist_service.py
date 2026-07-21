"""
Testes da triage determinística de `auto_review_checklist`.

`auto_review_checklist` roda passes LLM (compliance + qualidade + completude)
em paralelo. A triage (gates ~zero-custo antes do `asyncio.gather`) decide
quais passes têm de fato algo a avaliar:

  - compliance é PULADO se não há `edital_requirements` (sem regra a checar);
  - completude é PULADO se todas as seções do outline estão preenchidas
    (checagem estrutural, sem LLM);
  - qualidade roda SEMPRE.

Passe pulado devolve um resultado sintético "ok / não aplicável" com o mesmo
shape do passe real (campo extra `skipped`).

As chamadas LLM são mockadas — nenhum teste faz rede. Mockamos:
  - `_call_llm`     → registra qual passe foi efetivamente disparado e devolve
                      JSON canônico por passe (detectado pelo system prompt);
  - `make_async_client` → fake async context manager (sem API key / sem rede).

Os testes são síncronos e dirigem a coroutine via `asyncio.run` — sem depender
de configuração de `pytest-asyncio` na suíte.

SHADOW (gate manual do usuário, NÃO roda aqui): para validar que a triage não
perde achados, rode o all-3 forçando a flag e compare contra o triaged sobre
as mesmas propostas reais:

    # baseline all-3:  auto_review_checklist(..., force_all_passes=True)
    #                  ou export CHECKLIST_FORCE_ALL_PASSES=1 antes do caller real
    # triaged:         auto_review_checklist(...)        (sem a flag)

Colete o set de issues de cada run e promova a triage só se o triaged não
perder nenhum achado que o all-3 produzia.
"""
from __future__ import annotations

import asyncio

import pytest

from core.services import checklist_service as cs

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures de mock — nenhuma rede
# ---------------------------------------------------------------------------

class _FakeAsyncClient:
    """Async context manager mínimo no lugar de make_async_client."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Evita exigir OPENAI_API_KEY e qualquer cliente real."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.delenv("CHECKLIST_FORCE_ALL_PASSES", raising=False)
    monkeypatch.setattr(cs, "make_async_client", lambda **kw: _FakeAsyncClient())


@pytest.fixture
def llm_calls(monkeypatch):
    """
    Intercepta `_call_llm`, registra qual passe disparou (inferido do system
    prompt) e devolve JSON canônico daquele passe. Retorna a lista de passes
    efetivamente chamados.
    """
    calls: list[str] = []

    async def fake_call_llm(client, model, system, user, max_tokens=2000,
                            span_name="checklist.pass", span_meta=None):
        if system is cs._COMPLIANCE_SYSTEM:
            calls.append("compliance")
            return {
                "issues": [{"requirement": "CNPJ ativo", "status": "missing",
                            "evidence": "", "suggestion": "Inclua o CNPJ."}],
                "score": 40,
            }
        if system is cs._QUALITY_SYSTEM:
            calls.append("quality")
            return {
                "issues": [{"category": "clarity", "severity": "medium",
                            "excerpt": "trecho", "suggestion": "Reescreva."}],
                "overall_score": 70,
            }
        if system is cs._COMPLETENESS_SYSTEM:
            calls.append("completeness")
            return {
                "sections": [{"title": "Objetivos", "status": "empty",
                              "suggestion": "Preencher."}],
                "missing_sections": ["Objetivos"],
                "overall_score": 50,
            }
        raise AssertionError(f"prompt de passe desconhecido: {system!r}")

    monkeypatch.setattr(cs, "_call_llm", fake_call_llm)
    return calls


# ---------------------------------------------------------------------------
# Helpers de documento
# ---------------------------------------------------------------------------

def _doc(sections: dict[str, str]) -> str:
    """Monta o documento como o caller real (## título + corpo ou placeholder)."""
    parts = []
    for title, content in sections.items():
        if content:
            parts.append(f"## {title}\n\n{content}")
        else:
            parts.append(f"## {title}\n\n*[A preencher]*")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Gate 1 — compliance pulado sem requisitos
# ---------------------------------------------------------------------------

def test_compliance_skipped_when_no_requirements(llm_calls):
    outline = ["Objetivos", "Metodologia"]
    # seções parciais → completude NÃO é pulada
    proposal = _doc({"Objetivos": "Detalhado.", "Metodologia": ""})

    review = _run(cs.auto_review_checklist(
        proposal=proposal, edital_requirements=[], outline=outline,
    ))

    assert "compliance" not in llm_calls          # passe LLM não disparou
    assert "quality" in llm_calls
    # F5: completude é determinística (zero-LLM) — não chama _call_llm
    assert "completeness" not in llm_calls
    # resultado sintético, shape preservado
    assert review["compliance"]["score"] == 100
    assert review["compliance"]["issues"] == []
    assert review["compliance"]["skipped"] == "no_requirements"
    assert review["error"] is None
    # completude determinística: Metodologia vazia → empty
    meta_section = next(
        s for s in review["completeness"]["sections"] if s["title"] == "Metodologia"
    )
    assert meta_section["status"] == "empty"


# ---------------------------------------------------------------------------
# Gate 2 — completude pulada com seções completas
# ---------------------------------------------------------------------------

def test_completeness_skipped_when_all_sections_filled(llm_calls):
    outline = ["Objetivos", "Metodologia"]
    proposal = _doc({
        "Objetivos": "Texto substancial dos objetivos.",
        "Metodologia": "Plano de trabalho com etapas.",
    })
    requirements = [{"id": "req_0", "requirement": "CNPJ ativo"}]

    review = _run(cs.auto_review_checklist(
        proposal=proposal, edital_requirements=requirements, outline=outline,
    ))

    assert "completeness" not in llm_calls         # passe LLM não disparou
    assert "compliance" in llm_calls               # tem requisito → roda
    assert "quality" in llm_calls
    assert review["completeness"]["overall_score"] == 100
    assert review["completeness"]["sections"] == []
    assert review["completeness"]["missing_sections"] == []
    assert review["completeness"]["skipped"] == "all_sections_filled"


# ---------------------------------------------------------------------------
# Caso base — proposta parcial com requisitos → 3 passes rodam
# ---------------------------------------------------------------------------

def test_all_three_passes_run_for_partial_proposal(llm_calls):
    outline = ["Objetivos", "Metodologia"]
    proposal = _doc({"Objetivos": "Detalhado.", "Metodologia": ""})  # uma vazia
    requirements = [{"id": "req_0", "requirement": "CNPJ ativo"}]

    review = _run(cs.auto_review_checklist(
        proposal=proposal, edital_requirements=requirements, outline=outline,
    ))

    # F5: completude é determinística (zero-LLM) — não chama _call_llm
    assert set(llm_calls) == {"compliance", "quality"}
    # achados reais dos passes propagam
    assert review["compliance"]["score"] == 40
    assert review["quality"]["overall_score"] == 70
    # completude determinística: Metodologia vazia → empty
    assert "Metodologia" in review["completeness"]["missing_sections"]
    assert review["error"] is None


# ---------------------------------------------------------------------------
# Ambos os gates juntos — só qualidade roda
# ---------------------------------------------------------------------------

def test_both_gates_skip_leaves_only_quality(llm_calls):
    outline = ["Objetivos"]
    proposal = _doc({"Objetivos": "Conteúdo completo dos objetivos."})

    review = _run(cs.auto_review_checklist(
        proposal=proposal, edital_requirements=[], outline=outline,
    ))

    assert llm_calls == ["quality"]
    assert review["compliance"]["skipped"] == "no_requirements"
    assert review["completeness"]["skipped"] == "all_sections_filled"
    assert review["quality"]["overall_score"] == 70


# ---------------------------------------------------------------------------
# Shadow / force-all — flag desliga a triage
# ---------------------------------------------------------------------------

def test_force_all_passes_flag_disables_triage(llm_calls):
    # seções preenchidas → o gate de completude pularia esse passe.
    # requisitos não-vazios → compliance realmente chama o LLM (sem eles o passe
    # faz no-op interno e nunca chamaria, mascarando o efeito do force).
    outline = ["Objetivos"]
    proposal = _doc({"Objetivos": "Completo."})

    review = _run(cs.auto_review_checklist(
        proposal=proposal,
        edital_requirements=[{"id": "r", "requirement": "x"}],
        outline=outline,
        force_all_passes=True,
    ))

    # force_all reativa a completude que o gate teria pulado → o passe roda,
    # mas é determinístico (zero-LLM, F5) — não aparece em llm_calls.
    assert set(llm_calls) == {"compliance", "quality"}
    assert "skipped" not in review["compliance"]
    # completude determinística: seção com "Completo." (curta) → shallow
    assert review["completeness"]["sections"][0]["status"] == "shallow"
    assert "skipped" not in review["completeness"]


def test_force_all_passes_env_disables_triage(llm_calls, monkeypatch):
    monkeypatch.setenv("CHECKLIST_FORCE_ALL_PASSES", "1")
    outline = ["Objetivos"]
    proposal = _doc({"Objetivos": "Completo."})

    _run(cs.auto_review_checklist(
        proposal=proposal,
        edital_requirements=[{"id": "r", "requirement": "x"}],
        outline=outline,
    ))

    # F5: completude determinística — não chama LLM
    assert set(llm_calls) == {"compliance", "quality"}


# ---------------------------------------------------------------------------
# Conservadorismo do gate de completude
# ---------------------------------------------------------------------------

def test_completeness_runs_when_a_section_is_placeholder(llm_calls):
    outline = ["Objetivos", "Metodologia"]
    proposal = _doc({"Objetivos": "Completo.", "Metodologia": ""})  # placeholder

    review = _run(cs.auto_review_checklist(
        proposal=proposal,
        edital_requirements=[{"id": "r", "requirement": "x"}],
        outline=outline,
    ))
    # F5: completude determinística — não chama LLM, mas roda e detecta empty
    assert "completeness" not in llm_calls
    assert review["completeness"]["sections"][1]["status"] == "empty"


def test_completeness_runs_when_outline_section_missing_from_doc(llm_calls):
    # outline pede uma seção que o documento não tem → não pular
    outline = ["Objetivos", "Orçamento"]
    proposal = _doc({"Objetivos": "Completo."})

    review = _run(cs.auto_review_checklist(
        proposal=proposal,
        edital_requirements=[{"id": "r", "requirement": "x"}],
        outline=outline,
    ))
    # F5: completude determinística — detecta "Orçamento" como ausente
    assert "completeness" not in llm_calls
    assert "Orçamento" in review["completeness"]["missing_sections"]


def test_completeness_gate_stays_conservative_when_outline_empty(llm_calls):
    # sem outline não há sinal estrutural → o gate NÃO pode concluir "tudo
    # preenchido". A decisão do gate é não pular (o passe em si faz no-op
    # interno sem outline; o que importa aqui é que o gate não marcou skipped).
    proposal = _doc({"Objetivos": "Completo."})

    review = _run(cs.auto_review_checklist(
        proposal=proposal,
        edital_requirements=[{"id": "r", "requirement": "x"}],
        outline=[],
    ))
    assert review["completeness"].get("skipped") != "all_sections_filled"


# ---------------------------------------------------------------------------
# Unit do helper estrutural
# ---------------------------------------------------------------------------

def test_all_sections_filled_true():
    outline = ["Objetivos", "Metodologia"]
    proposal = _doc({"Objetivos": "Texto.", "Metodologia": "Mais texto."})
    assert cs._all_sections_filled(proposal, outline) is True


def test_all_sections_filled_false_on_placeholder():
    outline = ["Objetivos", "Metodologia"]
    proposal = _doc({"Objetivos": "Texto.", "Metodologia": ""})
    assert cs._all_sections_filled(proposal, outline) is False


def test_all_sections_filled_false_on_empty_outline():
    assert cs._all_sections_filled(_doc({"Objetivos": "x"}), []) is False


# ---------------------------------------------------------------------------
# Proposta vazia — short-circuit (comportamento atual preservado)
# ---------------------------------------------------------------------------

def test_empty_proposal_short_circuits_without_llm(llm_calls):
    review = _run(cs.auto_review_checklist(
        proposal="   ", edital_requirements=[{"id": "r", "requirement": "x"}],
        outline=["Objetivos"],
    ))
    assert llm_calls == []                          # nenhum passe disparou
    assert review["compliance"] == cs._FALLBACK_COMPLIANCE
    assert review["quality"] == cs._FALLBACK_QUALITY
    assert review["completeness"] == cs._FALLBACK_COMPLETENESS
    assert review["error"] is None


# ---------------------------------------------------------------------------
# Falha de passe vira erro estruturado (return_exceptions preservado)
# ---------------------------------------------------------------------------

def test_pass_failure_is_captured_as_error(monkeypatch):
    async def boom_call_llm(client, model, system, user, max_tokens=2000,
                            span_name="checklist.pass", span_meta=None):
        if system is cs._QUALITY_SYSTEM:
            raise RuntimeError("llm down")
        if system is cs._COMPLETENESS_SYSTEM:
            return {"sections": [], "missing_sections": [], "overall_score": 80}
        raise AssertionError("unexpected pass")

    monkeypatch.setattr(cs, "_call_llm", boom_call_llm)

    outline = ["Objetivos"]
    proposal = _doc({"Objetivos": ""})  # placeholder → completude roda

    review = _run(cs.auto_review_checklist(
        proposal=proposal, edital_requirements=[], outline=outline,
    ))

    assert review["error"] is not None
    assert any(e["pass"] == "quality" for e in review["error"])
    assert review["quality"] == cs._FALLBACK_QUALITY
    # F5: completude determinística — seção vazia → empty
    assert review["completeness"]["sections"][0]["status"] == "empty"
    assert review["completeness"]["overall_score"] == 0
