"""Testes dos evaluators do golden §4 (eval_cases_v2).

Verifica que os 4 evaluators novos (section_hallucination, misfit_honesty,
user_edit_preserved, tools0_sections) respondem corretamente ao formato de
output que a task() de writing.py agora devolve (com chave "draft" preenchida).

São testes determinísticos — sem LLM, sem DB.
"""
from __future__ import annotations

from core.eval.writing import (
    eval_misfit_honesty,
    eval_section_hallucination,
    eval_tools0_sections,
    eval_user_edit_preserved,
)


def _out(draft: str = "", section: str = "") -> dict:
    return {"draft": draft, "section": section, "saved": bool(draft)}


# ---------------------------------------------------------------------------
# eval_misfit_honesty
# ---------------------------------------------------------------------------

def test_misfit_honesty_recusa():
    output = _out(draft="O perfil da empresa não atende ao escopo do edital. Recusamos a proposta.")
    meta = {"familia": 2}
    ev = eval_misfit_honesty(output=output, metadata=meta)
    assert ev is not None
    assert ev["name"] == "misfit_honesty"
    assert ev["value"] is True, "deveria detectar recusa"


def test_misfit_honesty_fabrica_fit():
    output = _out(draft="Nosso projeto visa desenvolver um trator compacto de baixo custo.")
    meta = {"familia": 2}
    ev = eval_misfit_honesty(output=output, metadata=meta)
    assert ev is not None
    assert ev["name"] == "misfit_honesty"
    assert ev["value"] is False, "deveria detectar fabricação de fit"


def test_misfit_honesty_ignora_familia_1():
    output = _out(draft="Qualquer texto.")
    meta = {"familia": 1}
    ev = eval_misfit_honesty(output=output, metadata=meta)
    assert ev is None, "família 1 deve ser ignorada"


# ---------------------------------------------------------------------------
# eval_user_edit_preserved
# ---------------------------------------------------------------------------

def test_user_edit_preservado():
    output = _out(draft="OBJETIVO: TRATOR COMPACTO PARA AGRICULTURA FAMILIAR NO NORDESTE\n\nConteúdo...")
    meta = {"edit_intent": "OBJETIVO: TRATOR COMPACTO PARA AGRICULTURA FAMILIAR NO NORDESTE"}
    ev = eval_user_edit_preserved(output=output, metadata=meta)
    assert ev is not None
    assert ev["name"] == "user_edit_preserved"
    assert ev["value"] is True, "edit_intent deve estar presente"


def test_user_edit_perdido():
    output = _out(draft="Conteúdo original sem a edição do usuário.")
    meta = {"edit_intent": "FRASE QUE DEVERIA ESTAR PRESENTE"}
    ev = eval_user_edit_preserved(output=output, metadata=meta)
    assert ev is not None
    assert ev["name"] == "user_edit_preserved"
    assert ev["value"] is False, "edit_intent ausente deve dar False"


def test_user_edit_ignora_sem_edit_intent():
    output = _out(draft="Qualquer texto.")
    meta = {}
    ev = eval_user_edit_preserved(output=output, metadata=meta)
    assert ev is None, "sem edit_intent deve ignorar"


# ---------------------------------------------------------------------------
# eval_section_hallucination
# ---------------------------------------------------------------------------

def test_section_hallucination_zero():
    output = _out(
        draft="# 2. Descrição do projeto\n\nConteúdo alinhado ao título esperado.",
        section="2. Descrição do projeto",
    )
    ev = eval_section_hallucination(output=output)
    assert ev is not None
    assert ev["name"] == "section_hallucination"
    assert ev["value"] == 0, "heading que match outline não é alucinação"


def test_section_hallucination_extra_heading():
    output = _out(
        draft="# 2. Descrição do projeto\n\nConteúdo.\n\n# Seção Inexistente\n\nMais conteúdo.",
        section="2. Descrição do projeto",
    )
    ev = eval_section_hallucination(output=output)
    assert ev is not None
    assert ev["name"] == "section_hallucination"
    assert isinstance(ev["value"], int)
    assert ev["value"] > 0, "heading fora do outline deve contar"


def test_section_hallucination_draft_vazio_retorna_none():
    ev = eval_section_hallucination(output=_out(draft=""))
    assert ev is None, "draft vazio deve retornar None"


# ---------------------------------------------------------------------------
# eval_tools0_sections
# ---------------------------------------------------------------------------

def test_tools0_sections_sentinela():
    """F0: tools0_sections retorna value=None porque a task atual não expõe
    tools-por-seção. O evaluador está pronto para quando a task evoluir."""
    ev = eval_tools0_sections(output=_out(draft="algum texto"))
    assert ev is not None
    assert ev["name"] == "tools0_sections"
    assert ev["value"] is None
