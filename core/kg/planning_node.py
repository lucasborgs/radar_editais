"""Nó de planejamento de proposta — gera plano estruturado a partir do contexto
do Intake (FASE 1 da spec four-phase-workflow).

FLuxo: depois do Intake, se `is_complex_proposal()` → oferece "Estruturar proposta"
ao usuário. Se aceito, `generate_plan()` constrói o plano com seções, alinhamento
e dicas de compliance.

Uso:
    from core.kg.planning_node import is_complex_proposal, generate_plan

    if is_complex_proposal(message, analysis, len(history)):
        plan = generate_plan(intake_context)
        # plan → salva como `plan` no WritingSession.start()
"""
from __future__ import annotations

import json
import os
from typing import Any

from core.kg import entity_catalog
from core.llm.llm_client import make_client

_PLANNING_MODEL = os.getenv("PLANNING_MODEL", "gpt-4o-mini")
_PLANNING_API_KEY = os.getenv("PLANNING_API_KEY") or os.getenv("OPENAI_API_KEY", "")

_PLANNING_SYSTEM = """Você é um estrategista de propostas de fomento à inovação.
Dado o CONTEXTO de um edital e a CONVERSA de exploração com o usuário, produza
um plano estruturado de proposta.

O plano deve:
1. Ter 4-6 seções principais (título + descrição + key_points)
2. Incluir estimated_length (ex: "300-400 palavras") para cada seção
3. Mapear afinidades empresa ↔ edital (themes, match_score, critical_gaps)
4. Gerar compliance_hints — alertas objetivos sobre requisitos do edital
5. Se houver nós da empresa (company_nodes), gerar pre_fill relevante para cada seção

Responda APENAS JSON válido com esta estrutura exata:
{
  "title": "str — título completo da proposta incluindo edital",
  "sections_total": 5,
  "sections": [
    {
      "id": "str — slug curto (ex: impacto)",
      "title": "str — título da seção",
      "description": "str — o que a seção deve demonstrar",
      "key_points": ["str", "str"],
      "estimated_length": "str — ex: 300-400 palavras",
      "pre_fill": "str | null — texto sugestivo se houver afinidade"
    }
  ],
  "alignment": {
    "company_themes": ["str"],
    "edital_themes": ["str"],
    "match_score": 0.0-1.0,
    "critical_gaps": ["str — gaps observados"]
  },
  "compliance_hints": ["str — alertas objetivos sobre requisitos"]
}"""


def is_complex_proposal(
    message: str,
    analysis: str,
    history_length: int,
) -> bool:
    """Heurística: planning é oferecido se a mensagem menciona proposta/escrita,
    análise tem >300 palavras e há histórico (não é a primeira pergunta)."""
    keywords = ["proposta", "escrever", "redigir", "rascunho", "draft", "estruturar"]
    has_keyword = any(k in message.lower() for k in keywords)
    is_long = len(analysis.split()) > 300
    has_history = history_length > 1
    return has_keyword and is_long and has_history


def _format_edital_context(edital_id: str | None) -> str:
    """Busca ficha do edital no hipergrado e monta um bloco de contexto."""
    if not edital_id:
        return "Nenhum edital específico selecionado."
    card = entity_catalog.get_edital(edital_id)
    if not card:
        return f"Edital {edital_id} não encontrado no catálogo."
    lines: list[str] = [
        f"Título: {card.get('title', '')}",
        f"Objetivo: {card.get('objective', '')}",
        f"Mecanismo: {card.get('mechanism', '')}",
        f"Status: {card.get('status', '')}  |  Prazo: {card.get('deadline', '')}",
    ]
    if card.get("themes"):
        lines.append("Temas: " + ", ".join(card["themes"]))
    if card.get("technologies"):
        lines.append("Tecnologias: " + ", ".join(card["technologies"]))
    if card.get("key_requirements"):
        lines.append("Requisitos-chave:")
        for r in card["key_requirements"]:
            lines.append(f"  • {r}")
    if card.get("constraints"):
        lines.append("Restrições de elegibilidade:")
        for c in card["constraints"]:
            lines.append(f"  • {c}")
    if card.get("exclusoes"):
        lines.append("Exclusões:")
        for e in card["exclusoes"]:
            lines.append(f"  • {e}")
    return "\n".join(lines)


def _format_company_context(company_nodes: list[dict] | None) -> str:
    if not company_nodes:
        return "Nenhum perfil de empresa disponível."
    lines: list[str] = []
    for node in company_nodes:
        t = node.get("type", "desconhecido")
        names = node.get("names", [])
        if names:
            lines.append(f"  {t}: {', '.join(names)}")
    return "\n".join(lines) if lines else "Nenhum nó de empresa disponível."


def generate_plan(
    question: str,
    analysis: str,
    edital_id: str | None = None,
    company_nodes: list[dict] | None = None,
) -> dict[str, Any]:
    """Gera um plano estruturado de proposta via LLM.

    Args:
        question: Pergunta original do usuário no Intake.
        analysis: Resposta completa do ExploreAgent.
        edital_id: ID do edital (opcional, para contexto enriquecido).
        company_nodes: Nós extraídos da empresa (tema, tecnologia, aplicação).

    Returns:
        Dict com o plano (sections, alignment, compliance_hints) ou dict
        com chave 'error' se algo falhar.
    """
    edital_block = _format_edital_context(edital_id)
    company_block = _format_company_context(company_nodes)

    user_parts: list[str] = [
        "[CONTEXTO DO EDITAL]",
        edital_block,
        "",
        "[PERGUNTA DO USUÁRIO]",
        question,
        "",
        "[ANÁLISE DO INTAKE]",
        analysis,
    ]
    if company_block != "Nenhum perfil de empresa disponível.":
        user_parts += ["", "[NÓS DA EMPRESA]", company_block]

    user_text = "\n".join(user_parts)

    api_key = _PLANNING_API_KEY
    if not api_key:
        return {"error": "PLANNING_API_KEY / OPENAI_API_KEY não definida"}

    try:
        client = make_client(api_key=api_key)
        resp = client.chat.completions.create(
            model=_PLANNING_MODEL,
            messages=[
                {"role": "system", "content": _PLANNING_SYSTEM},
                {"role": "user", "content": user_text},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        if not raw:
            return {"error": "LLM retornou resposta vazia"}
        plan: dict[str, Any] = json.loads(raw)

        # Garante campos obrigatórios
        plan.setdefault("sections_total", len(plan.get("sections", [])))
        plan.setdefault("sections", [])
        plan.setdefault("alignment", {})
        plan.setdefault("compliance_hints", [])
        plan.setdefault("title", "Proposta")

        return plan

    except json.JSONDecodeError as e:
        return {"error": f"Erro ao parsear JSON do plano: {e}"}
    except Exception as e:
        return {"error": f"Erro ao gerar plano: {e}"}
