"""Scope classifier — classifica correção do usuário como cosmética ou conceitual.

Chamado internamente pela tool `save_draft` (writing_tools.py) no modo
conversacional, após o critic aprovar. Compara o conteúdo ANTES e DEPOIS da
correção para determinar se a mudança é:

  COSMETIC: tom, redação, formatação, ordem — fatos inalterados.
  CONCEPTUAL: escopo, tecnologia, metodologia, objetivos, equipe, orçamento,
              cronograma — ripple necessário em seções dependentes.

Regras (D9):
  • Só dispara em save_draft acionado por ação humana direta (não em ripple).
  • Depth limit = 1: ripple nunca gera novo ripple.
  • Falha graciosa: erro do LLM → retorna None (save nunca bloqueia).
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.services.writing_session import WritingSession

logger = logging.getLogger(__name__)

_CORRECTION_SCOPE_SYSTEM = """You analyze a user correction to a proposal section
and classify its scope.

The user sent a correction, and the agent rewrote section "{section_title}" accordingly.

Compare the OLD content (before correction) with the NEW content (after correction).
Then classify:

COSMETIC: tone, phrasing, formatting, word choice, grammar, reordering of
information. The factual content, scope, technology, approach, numbers, and
structure are unchanged.

CONCEPTUAL: scope, technology/methodology, objectives, team composition, budget
values, timeline, approach/methodology, or any factual claim changed.

Use the dependency matrix below as a hint for which sections might be affected,
but rely primarily on what actually changed in the content.

MATRIX DE DEPENDÊNCIA:
{matrix}

Output JSON:
{
    "type": "cosmetic" | "conceptual",
    "reasoning": "1-sentence justification in PT-BR",
    "changed_elements": ["list", "of", "changed", "aspects"]
}
"""


def classify_correction_scope(
    old_content: str,
    new_content: str,
    section_title: str,
    session: WritingSession,
) -> dict | None:
    """Classifica o escopo de uma correção em save_draft.

    Returns:
        dict com {"type", "reasoning", "changed_elements"} se conceitual.
        None se cosmético ou em caso de erro.
    """
    if not old_content.strip() or not new_content.strip():
        return None

    # Seleciona a matriz de dependência conforme o modo
    from core.services.writing_session import (
        PITCH_DEPENDENCY_MATRIX,
        PROPOSAL_DEPENDENCY_MATRIX,
    )
    matrix = (
        PITCH_DEPENDENCY_MATRIX if session.mode == "pitch"
        else PROPOSAL_DEPENDENCY_MATRIX
    )
    matrix_str = _format_matrix(matrix)

    # Monta o prompt
    system = _CORRECTION_SCOPE_SYSTEM.format(
        section_title=section_title,
        matrix=matrix_str,
    )

    user = (
        f"SEÇÃO: {section_title}\n\n"
        f"CONTEÚDO ANTES DA CORREÇÃO:\n{old_content[:2000]}\n\n"
        f"CONTEÚDO DEPOIS DA CORREÇÃO:\n{new_content[:2000]}"
    )

    try:
        from core import telemetry
        from core.llm.llm_client import make_client
        client = make_client()
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        with telemetry.llm_span(
            "writing.scope_classifier",
            model=model,
            metadata={"workspace_id": session.workspace_id,
                      "session_id": session.session_id,
                      "section": section_title},
        ) as span:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            telemetry.record_usage(span, response)
        text = response.choices[0].message.content
        if not text:
            return None

        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        if data.get("type") != "conceptual":
            return None

        return {
            "type": "conceptual",
            "reasoning": str(data.get("reasoning", "")),
            "changed_elements": data.get("changed_elements", []),
        }
    except Exception as e:
        logger.debug(
            "classify_correction_scope falhou para '%s': %s",
            section_title, e,
        )
        return None


def _format_matrix(matrix: dict[str, list[str]]) -> str:
    """Formata a matriz de dependência para exibição no prompt."""
    lines: list[str] = []
    for section, deps in matrix.items():
        if deps:
            lines.append(f"  • {section} → {', '.join(deps)}")
        else:
            lines.append(f"  • {section} → (nenhuma dependente)")
    return "\n".join(lines)
