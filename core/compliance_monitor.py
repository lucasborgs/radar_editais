"""
ComplianceMonitor — verificação inline de aderência ao edital por turno (Fase 2 #20).

ADR A4: roda em paralelo ao LLM principal via asyncio.gather no /writing/turn.
Como ele verifica a MENSAGEM DO USUÁRIO (não a resposta do LLM), pode rodar
em paralelo — latência total ≈ max(LLM, monitor) ≈ LLM (monitor tem prompt
enxuto).

Prompt enxuto, sem histórico — vê apenas:
  • Mensagem atual do usuário
  • Requirements mandatórios do edital (carregados do KG wiki page)

Retorna flags estruturadas para o frontend renderizar:
  [{requirement: str, status: 'ok' | 'at_risk' | 'violation', suggestion: str}]

Status:
  ok        — mensagem alinhada com o requisito (ou não toca nele)
  at_risk   — mensagem vai numa direção que pode comprometer o requisito
  violation — mensagem contradiz o requisito explicitamente

Falha graciosa: se LLM ou DB falhar, retorna [] e loga. Não bloqueia o turno.
"""
from __future__ import annotations

import json
import logging
import os
import re

from core.kg import hypergraph_catalog

logger = logging.getLogger(__name__)

_MONITOR_SYSTEM = """Você é um revisor de compliance para propostas em editais de fomento.
Sua função é avaliar se a mensagem do usuário em uma sessão de escrita está alinhada com
os requisitos mandatórios do edital. Seja conservador: marque 'violation' apenas com
evidência clara; use 'at_risk' para sinais; 'ok' caso a mensagem não toque no requisito
ou esteja alinhada. Responda APENAS com JSON válido."""


_MONITOR_USER = """REQUISITOS DO EDITAL:
{requirements}

MENSAGEM DO USUÁRIO:
\"\"\"
{user_message}
\"\"\"

Para cada requisito acima, avalie se a mensagem do usuário:
  - "ok"        — não toca neste requisito OU está alinhada
  - "at_risk"   — vai numa direção que pode comprometer
  - "violation" — contradiz explicitamente

Retorne apenas os requisitos com status != "ok" (não polua o output com okays).
Inclua uma sugestão acionável de 1 frase.

JSON:
{{
  "flags": [
    {{"requirement": "<texto do requisito>", "status": "at_risk|violation", "suggestion": "..."}}
  ]
}}"""


def _load_edital_requirements(edital_id: str) -> tuple[list[str], str]:
    """Lê key_requirements + mechanism do hypergraph card. ([], "") se ausente.

    `mechanism` keya o playbook. A agência (overlay de fonte) NÃO vem do campo
    `source` do card — esse guarda a proveniência da ingestão — e sim do
    prefixo do edital_id (`edital_id.source_of`).
    """
    try:
        card = hypergraph_catalog.get_edital(edital_id)
        if not card:
            return [], ""
        reqs = card.get("key_requirements", []) or []
        mechanism = str(card.get("mechanism", "") or "")
        return [str(r) for r in reqs], mechanism
    except Exception as e:
        logger.warning("ComplianceMonitor: falha ao ler hypergraph %s: %s", edital_id, e)
        return [], ""


def _make_client():
    from core.llm.llm_client import make_client
    return make_client(api_key=os.environ["OPENAI_API_KEY"]), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def check_compliance(user_message: str, edital_id: str) -> list[dict]:
    """Verifica a mensagem do usuário contra os requisitos do edital.

    Sync por design — o caller (FastAPI route) wrappia em asyncio.to_thread
    para rodar em paralelo com o LLM principal via asyncio.gather (ADR A4).

    Returns: lista de flags. Lista vazia significa "nada problemático detectado"
    (incluindo casos de falha — o ComplianceMonitor nunca interrompe o turno).
    """
    if not user_message.strip():
        return []

    requirements, mechanism = _load_edital_requirements(edital_id)
    if not requirements:
        return []

    requirements_block = "\n".join(f"- {r}" for r in requirements)

    # Playbook do mecanismo (+ overlay de fonte): heurísticas de aprovação e
    # anti-padrões tácitos. Substitui a injeção do skill por-fonte inteiro — a
    # regra dura do edital já vem em `requirements` (RAG), não no playbook.
    # Agência (overlay) = prefixo do edital_id, não o campo `source` da wiki.
    from core.kg.edital_id import source_of
    from core.skills import load_playbook
    try:
        agency = source_of(edital_id)
    except ValueError:
        agency = ""
    playbook = load_playbook(mechanism, agency)
    monitor_skill = playbook.for_monitor()
    system_prompt = _MONITOR_SYSTEM
    if monitor_skill:
        label = playbook.mechanism or "genérico"
        if playbook.source:
            label += f" · {playbook.source}"
        system_prompt = (
            _MONITOR_SYSTEM
            + f"\n\nHEURÍSTICAS DE AVALIAÇÃO ({label}):\n"
            + monitor_skill
        )

    user_msg = _MONITOR_USER.format(
        requirements=requirements_block,
        user_message=user_message[:3000],  # trunca para manter prompt enxuto
    )

    try:
        client, model = _make_client()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        data = json.loads(raw)
        flags = data.get("flags", []) or []
        # Sanitiza shape — protege o frontend de output inesperado
        return [
            {
                "requirement": str(f.get("requirement", "")),
                "status": str(f.get("status", "at_risk")),
                "suggestion": str(f.get("suggestion", "")),
            }
            for f in flags
            if f.get("status") in ("at_risk", "violation")
        ]
    except Exception as e:
        logger.warning("check_compliance falhou para edital=%s: %s", edital_id, e)
        return []
