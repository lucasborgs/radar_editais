"""Regressão de conteúdo dos system prompts do Redator (revisão prompts/skills).

Guards baratos contra reintrodução de convenções mortas e contra a assimetria
WRITER↔PITCH corrigida nesta revisão.
"""
from __future__ import annotations

from core.services.writing_session import (
    PITCH_WRITER_AGENT_SYSTEM,
    WRITER_AGENT_SYSTEM,
)


def test_no_completar_placeholder_convention():
    """[COMPLETAR: ...] foi substituído por request_user_info — não deve voltar."""
    assert "[COMPLETAR" not in WRITER_AGENT_SYSTEM
    assert "[COMPLETAR" not in PITCH_WRITER_AGENT_SYSTEM
    assert "request_user_info" in WRITER_AGENT_SYSTEM
    assert "request_user_info" in PITCH_WRITER_AGENT_SYSTEM


def test_both_writers_instruct_write_todos():
    """write_todos é o único mecanismo de planejamento (spec 04); ambos os
    gêneros (proposta e pitch) devem orientá-lo — paridade restaurada."""
    assert "write_todos" in WRITER_AGENT_SYSTEM
    assert "write_todos" in PITCH_WRITER_AGENT_SYSTEM
