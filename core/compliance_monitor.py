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
from pathlib import Path

from config import KG_WIKI_DIR

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
    """Lê key_requirements + source da wiki page. Retorna ([], "") se ausente.

    Source é usado para carregar skills/<source>_compliance.md (Fase 4 #26).
    """
    wiki_file = KG_WIKI_DIR / f"{edital_id}.json"
    if not wiki_file.exists():
        return [], ""
    try:
        data = json.loads(Path(wiki_file).read_text(encoding="utf-8"))
        reqs = data.get("key_requirements", []) or []
        source = str(data.get("source", "") or "")
        return [str(r) for r in reqs], source
    except Exception as e:
        logger.warning("ComplianceMonitor: falha ao ler wiki page %s: %s", edital_id, e)
        return [], ""


def _make_client():
    from core.llm_client import make_client
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

    requirements, source = _load_edital_requirements(edital_id)
    if not requirements:
        return []

    requirements_block = "\n".join(f"- {r}" for r in requirements)

    # Skill por fonte (Fase 4 #26): se houver, anexa ao system prompt.
    from core.skills import load_skill
    source_skill = load_skill(source) if source else ""
    system_prompt = _MONITOR_SYSTEM
    if source_skill:
        system_prompt = (
            _MONITOR_SYSTEM
            + f"\n\nREGRAS ESPECÍFICAS DA FONTE ({source}):\n"
            + source_skill
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
