"""
ChecklistService — extrai requisitos obrigatórios do edital e avalia cobertura.

Fontes de requisitos (em ordem de prioridade):
  1. card.key_requirements  — gerados pelo etl_finep_cards.py
  2. Fatos Tier 1 que contêm verbos de obrigatoriedade
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from config import FINEP_FACTS_DIR, KG_CARDS_DIR

logger = logging.getLogger(__name__)

_OBLIGATION_PATTERN = re.compile(
    r"\b(deve|deverá|é obrigatório|obrigatório|necessário|é necessário|exige|exigido|requerido)\b",
    re.IGNORECASE,
)

_SECTION_KEYWORDS: dict[str, list[str]] = {
    "Identificação da empresa":       ["cnpj", "identificação", "empresa", "razão social"],
    "Objeto do projeto":               ["objeto", "título", "denominação"],
    "Justificativa e relevância":      ["justificativa", "problema", "relevância"],
    "Objetivos":                       ["objetivo", "meta", "finalidade"],
    "Metodologia":                     ["metodologia", "plano de trabalho", "etapas", "cronograma"],
    "Equipe técnica":                  ["equipe", "pesquisador", "coordenador", "cv lattes"],
    "Orçamento":                       ["orçamento", "planilha", "custo", "contrapartida"],
    "Documentação":                    ["documento", "certidão", "declaração", "anexo"],
}

_AUTO_REVIEW_SYSTEM = """Você é um revisor especializado em propostas de P&D para editais de fomento no Brasil.
Dado o documento da proposta e a lista de requisitos obrigatórios do edital,
identifique quais requisitos estão cobertos (addressed) e quais estão pendentes (pending).
Responda APENAS com JSON válido."""

_AUTO_REVIEW_USER = """DOCUMENTO DA PROPOSTA:
\"\"\"
{document}
\"\"\"

REQUISITOS OBRIGATÓRIOS:
{requirements}

Para cada requisito, retorne se ele está coberto no documento.
{{
  "review": [
    {{
      "requirement": "texto do requisito",
      "status": "addressed" | "pending",
      "reason": "1 frase explicando"
    }}
  ]
}}"""


def _infer_section(requirement: str) -> str:
    req_lower = requirement.lower()
    for section, keywords in _SECTION_KEYWORDS.items():
        if any(kw in req_lower for kw in keywords):
            return section
    return "Geral"


def build_checklist(edital_id: str) -> list[dict]:
    """
    Constrói checklist de requisitos para o edital.
    Prioridade: card.key_requirements > fatos Tier 1 com verbos de obrigação.
    """
    items: list[dict] = []
    seen: set[str] = set()

    # 1. Card key_requirements
    card_file = KG_CARDS_DIR / f"{edital_id}.json"
    if card_file.exists():
        try:
            card = json.loads(card_file.read_text(encoding="utf-8"))
            for req in card.get("key_requirements", []):
                if req and req not in seen:
                    seen.add(req)
                    items.append({
                        "id": f"req_{len(items)}",
                        "requirement": req,
                        "section": _infer_section(req),
                        "status": "pending",
                        "source": "key_requirements",
                    })
        except Exception as e:
            logger.warning("Erro ao ler card %s: %s", edital_id, e)

    # 2. Fatos Tier 1 com verbos de obrigação
    facts_dir = FINEP_FACTS_DIR / edital_id
    if facts_dir.exists():
        for fact_file in sorted(facts_dir.glob("tier1_*.json")):
            try:
                facts = json.loads(fact_file.read_text(encoding="utf-8"))
                for fact in facts.get("facts", []):
                    text = fact if isinstance(fact, str) else fact.get("fact", "")
                    if _OBLIGATION_PATTERN.search(text) and text not in seen:
                        seen.add(text)
                        items.append({
                            "id": f"req_{len(items)}",
                            "requirement": text,
                            "section": _infer_section(text),
                            "status": "pending",
                            "source": "tier1_fact",
                        })
            except Exception as e:
                logger.warning("Erro ao ler fatos %s: %s", fact_file, e)

    return items


def auto_review_checklist(items: list[dict], document: str) -> list[dict]:
    """Usa LLM para marcar quais requisitos estão cobertos no documento."""
    if not items or not document.strip():
        return items

    try:
        from openai import OpenAI
        backend = os.getenv("LLM_BACKEND", "openai").lower()
        if backend == "gemini":
            client = OpenAI(
                api_key=os.environ["GEMINI_API_KEY"],
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            model = "gemini-2.5-flash"
        else:
            client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        reqs_text = "\n".join(f"- {item['requirement']}" for item in items)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _AUTO_REVIEW_SYSTEM},
                {"role": "user", "content": _AUTO_REVIEW_USER.format(
                    document=document[:6000],
                    requirements=reqs_text,
                )},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        data = json.loads(raw)

        review_map = {r["requirement"]: r for r in data.get("review", [])}
        updated = []
        for item in items:
            rev = review_map.get(item["requirement"], {})
            updated.append({
                **item,
                "status": rev.get("status", item["status"]),
                "reason": rev.get("reason"),
            })
        return updated

    except Exception as e:
        logger.error("Erro no auto-review: %s", e)
        return items
