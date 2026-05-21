"""
ChecklistService — extrai requisitos obrigatórios do edital e roda auto-review em 3 passes paralelas.

Fontes de requisitos (em ordem de prioridade):
  1. wiki_page.key_requirements  — gerados pelo etl_finep_cards.py
  2. Fatos Tier 1 que contêm verbos de obrigatoriedade

Auto-review (LLM):
  Pass 1 — Compliance:      requisitos obrigatórios do edital cobertos?
  Pass 2 — Qualidade:       clareza, coerência, persuasão, tom.
  Pass 3 — Completude:      seções presentes e com profundidade adequada.

Os 3 passes rodam em paralelo via asyncio.gather (ADR C4: latência cai ~3×).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from config import KG_WIKI_DIR

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


# ---------------------------------------------------------------------------
# Prompts — 3 passes
# ---------------------------------------------------------------------------

_COMPLIANCE_SYSTEM = """Você é um revisor especializado em propostas de P&D para editais de fomento no Brasil.
Dado o documento da proposta e a lista de requisitos obrigatórios do edital,
avalie quais requisitos estão cobertos, parcialmente cobertos ou ausentes.
Responda APENAS com JSON válido."""

_COMPLIANCE_USER = """DOCUMENTO DA PROPOSTA:
\"\"\"
{document}
\"\"\"

REQUISITOS OBRIGATÓRIOS DO EDITAL:
{requirements}

Para cada requisito, retorne se está coberto, com evidência do texto e sugestão de melhoria caso necessário.
Atribua também um score 0-100 representando a aderência global da proposta aos requisitos.

Formato esperado:
{{
  "issues": [
    {{
      "requirement": "texto do requisito",
      "status": "ok" | "missing" | "partial",
      "evidence": "trecho curto da proposta (vazio se ausente)",
      "suggestion": "1 frase de melhoria (vazio se status=ok)"
    }}
  ],
  "score": 0
}}"""

_QUALITY_SYSTEM = """Você é um editor sênior especializado em propostas técnicas para editais de fomento.
Analise o texto fornecido sob a ótica de qualidade narrativa: clareza, coerência, persuasão e tom adequado.
Aponte problemas específicos, com trechos curtos como evidência (excerpt) e sugestão acionável.
Responda APENAS com JSON válido."""

_QUALITY_USER = """DOCUMENTO DA PROPOSTA:
\"\"\"
{document}
\"\"\"

Avalie a qualidade narrativa do texto. Para cada problema encontrado, classifique a categoria
(clarity, coherence, persuasion ou tone) e a severidade (low, medium ou high).
Inclua um trecho curto (excerpt) que evidencie o problema e uma sugestão de reescrita ou ajuste.
Atribua um overall_score 0-100 para a qualidade global da redação.

Formato esperado:
{{
  "issues": [
    {{
      "category": "clarity" | "coherence" | "persuasion" | "tone",
      "severity": "low" | "medium" | "high",
      "excerpt": "trecho curto (até ~25 palavras) do documento",
      "suggestion": "1-2 frases de melhoria concreta"
    }}
  ],
  "overall_score": 0
}}"""

_COMPLETENESS_SYSTEM = """Você é um revisor estrutural de propostas para editais de fomento.
Sua função é avaliar se a proposta cobre todas as seções esperadas e se cada seção tem profundidade adequada.
Responda APENAS com JSON válido."""

_COMPLETENESS_USER = """DOCUMENTO DA PROPOSTA (cada seção começa com '## <título>'):
\"\"\"
{document}
\"\"\"

SEÇÕES ESPERADAS (outline da proposta):
{outline}

Para cada seção do outline, avalie:
  - status: "empty"     — seção ausente ou marcada como [A preencher]
            "shallow"   — presente mas raso (poucas linhas, sem detalhamento)
            "adequate"  — cobre os pontos esperados, suficiente
            "thorough"  — desenvolvido, com detalhes, dados ou referências
  - suggestion: 1 frase indicando o que falta (vazio se thorough).

Liste em missing_sections os títulos com status "empty".
Atribua um overall_score 0-100 para a completude global do documento.

Formato esperado:
{{
  "sections": [
    {{
      "title": "título da seção (igual ao outline)",
      "status": "empty" | "shallow" | "adequate" | "thorough",
      "suggestion": "1 frase (vazio se thorough)"
    }}
  ],
  "missing_sections": ["..."],
  "overall_score": 0
}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_section(requirement: str) -> str:
    req_lower = requirement.lower()
    for section, keywords in _SECTION_KEYWORDS.items():
        if any(kw in req_lower for kw in keywords):
            return section
    return "Geral"


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
    return raw


def _llm_config() -> tuple[str, str, str | None]:
    """Retorna (api_key, model, base_url|None) baseado em LLM_BACKEND."""
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        return (
            os.environ["GEMINI_API_KEY"],
            "gemini-2.5-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    return (
        os.environ["OPENAI_API_KEY"],
        os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        None,
    )


def build_checklist(edital_id: str) -> list[dict]:
    """
    Constrói checklist de requisitos para o edital.
    Prioridade: wiki_page.key_requirements > fatos Tier 1 com verbos de obrigação.
    """
    items: list[dict] = []
    seen: set[str] = set()

    wiki_file = KG_WIKI_DIR / f"{edital_id}.json"
    if wiki_file.exists():
        try:
            wiki_page = json.loads(wiki_file.read_text(encoding="utf-8"))
            for req in wiki_page.get("key_requirements", []):
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
            logger.warning("Erro ao ler wiki page %s: %s", edital_id, e)

    return items


# ---------------------------------------------------------------------------
# Passes (async, rodam em paralelo)
# ---------------------------------------------------------------------------

async def _call_llm(client, model: str, system: str, user: str, max_tokens: int = 2000) -> dict:
    """Chama o LLM e parseia o JSON da resposta."""
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
    )
    raw = _strip_code_fence(response.choices[0].message.content or "")
    return json.loads(raw)


async def _pass_compliance(
    proposal: str,
    edital_requirements: list[dict],
    client,
    model: str,
) -> dict:
    """Pass 1 — requisitos obrigatórios do edital cobertos?"""
    if not edital_requirements:
        return {"issues": [], "score": 100}

    reqs_text = "\n".join(f"- {item['requirement']}" for item in edital_requirements)
    data = await _call_llm(
        client,
        model,
        _COMPLIANCE_SYSTEM,
        _COMPLIANCE_USER.format(document=proposal[:6000], requirements=reqs_text),
        max_tokens=2000,
    )
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    return {"issues": issues, "score": max(0, min(100, score))}


async def _pass_quality(proposal: str, client, model: str) -> dict:
    """Pass 2 — qualidade narrativa: clareza, coerência, persuasão, tom."""
    if not proposal.strip():
        return {"issues": [], "overall_score": 0}

    data = await _call_llm(
        client,
        model,
        _QUALITY_SYSTEM,
        _QUALITY_USER.format(document=proposal[:6000]),
        max_tokens=2000,
    )
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    try:
        score = int(data.get("overall_score", 0))
    except (TypeError, ValueError):
        score = 0
    return {"issues": issues, "overall_score": max(0, min(100, score))}


async def _pass_completeness(
    proposal: str,
    outline: list[str],
    client,
    model: str,
) -> dict:
    """Pass 3 — completude das seções."""
    if not outline:
        return {"sections": [], "missing_sections": [], "overall_score": 0}

    outline_text = "\n".join(f"- {t}" for t in outline)
    data = await _call_llm(
        client,
        model,
        _COMPLETENESS_SYSTEM,
        _COMPLETENESS_USER.format(document=proposal[:6000], outline=outline_text),
        max_tokens=2000,
    )
    sections = data.get("sections") or []
    if not isinstance(sections, list):
        sections = []
    missing = data.get("missing_sections") or []
    if not isinstance(missing, list):
        missing = []
    try:
        score = int(data.get("overall_score", 0))
    except (TypeError, ValueError):
        score = 0
    return {
        "sections": sections,
        "missing_sections": missing,
        "overall_score": max(0, min(100, score)),
    }


# ---------------------------------------------------------------------------
# Fallbacks (quando um pass falha)
# ---------------------------------------------------------------------------

_FALLBACK_COMPLIANCE = {"issues": [], "score": 0}
_FALLBACK_QUALITY = {"issues": [], "overall_score": 0}
_FALLBACK_COMPLETENESS = {"sections": [], "missing_sections": [], "overall_score": 0}


# ---------------------------------------------------------------------------
# Entry point (async): roda os 3 passes em paralelo
# ---------------------------------------------------------------------------

async def auto_review_checklist(
    proposal: str,
    edital_requirements: list[dict] | None = None,
    outline: list[str] | None = None,
) -> dict:
    """
    Executa as 3 passes de revisão em paralelo via asyncio.gather.

    Retorna um dict com:
      - compliance:   {issues, score}
      - quality:      {issues, overall_score}
      - completeness: {sections, missing_sections, overall_score}
      - error:        None ou lista de {pass, message} para passes que falharam
    """
    edital_requirements = edital_requirements or []
    outline = outline or []

    # Sem proposta, nada a revisar — retorne fallback estruturado.
    if not proposal or not proposal.strip():
        return {
            "compliance":   dict(_FALLBACK_COMPLIANCE),
            "quality":      dict(_FALLBACK_QUALITY),
            "completeness": dict(_FALLBACK_COMPLETENESS),
            "error":        None,
        }

    try:
        from core.llm_client import make_async_client
    except ImportError as e:
        logger.error("AsyncOpenAI indisponível: %s", e)
        return {
            "compliance":   dict(_FALLBACK_COMPLIANCE),
            "quality":      dict(_FALLBACK_QUALITY),
            "completeness": dict(_FALLBACK_COMPLETENESS),
            "error":        [{"pass": "all", "message": str(e)}],
        }

    api_key, model, base_url = _llm_config()
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    async with make_async_client(**client_kwargs) as client:
        results = await asyncio.gather(
            _pass_compliance(proposal, edital_requirements, client, model),
            _pass_quality(proposal, client, model),
            _pass_completeness(proposal, outline, client, model),
            return_exceptions=True,
        )

    compliance_res, quality_res, completeness_res = results
    errors: list[dict] = []

    if isinstance(compliance_res, BaseException):
        logger.error("Pass compliance falhou: %s", compliance_res)
        errors.append({"pass": "compliance", "message": str(compliance_res)})
        compliance_res = dict(_FALLBACK_COMPLIANCE)

    if isinstance(quality_res, BaseException):
        logger.error("Pass quality falhou: %s", quality_res)
        errors.append({"pass": "quality", "message": str(quality_res)})
        quality_res = dict(_FALLBACK_QUALITY)

    if isinstance(completeness_res, BaseException):
        logger.error("Pass completeness falhou: %s", completeness_res)
        errors.append({"pass": "completeness", "message": str(completeness_res)})
        completeness_res = dict(_FALLBACK_COMPLETENESS)

    return {
        "compliance":   compliance_res,
        "quality":      quality_res,
        "completeness": completeness_res,
        "error":        errors or None,
    }
