"""
ChecklistService — extrai requisitos obrigatórios do edital e roda auto-review em 3 passes paralelas.

Fontes de requisitos (em ordem de prioridade):
  1. ficha gold da oportunidade (`entity_catalog.get_opportunity`)
  2. requisitos textuais que contêm verbos de obrigatoriedade

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

from radar.core.kg import entity_catalog

# Importado no nível do módulo (em vez de dentro de auto_review_checklist) para
# que os testes possam monkeypatchar `make_async_client`. O SDK OpenAI é uma
# dependência declarada (pyproject), então o import não deve falhar; mantemos o
# fallback resiliente no entry point caso o ambiente esteja degradado.
try:
    from radar.core.llm.llm_client import make_async_client
except ImportError:  # pragma: no cover - ambiente sem o SDK OpenAI
    make_async_client = None  # type: ignore[assignment]

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
{playbook_block}
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

    try:
        card = entity_catalog.get_edital(edital_id)
        if card:
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
        logger.warning("Erro ao ler ficha gold %s: %s", edital_id, e)

    return items


# ---------------------------------------------------------------------------
# Passes (async, rodam em paralelo)
# ---------------------------------------------------------------------------

async def _call_llm(
    client,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2000,
    span_name: str = "checklist.pass",
    span_meta: dict | None = None,
) -> dict:
    """Chama o LLM e parseia o JSON da resposta. 1 span Langfuse por pass."""
    from radar.core.infra import telemetry

    with telemetry.llm_span(span_name, model=model, metadata=span_meta) as span:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=max_tokens,
        )
        telemetry.record_usage(span, response)
    raw = _strip_code_fence(response.choices[0].message.content or "")
    return json.loads(raw)


async def _pass_compliance(
    proposal: str,
    edital_requirements: list[dict],
    client,
    model: str,
    playbook_context: str = "",
    span_meta: dict | None = None,
) -> dict:
    """Pass 1 — requisitos obrigatórios do edital cobertos?"""
    if not edital_requirements:
        return {"issues": [], "score": 100}

    reqs_text = "\n".join(f"- {item['requirement']}" for item in edital_requirements)
    playbook_block = (
        f"\nREGRAS DO INSTRUMENTO (heurísticas e anti-padrões do mecanismo):\n{playbook_context}\n"
        if playbook_context and playbook_context.strip()
        else ""
    )
    data = await _call_llm(
        client,
        model,
        _COMPLIANCE_SYSTEM,
        _COMPLIANCE_USER.format(
            document=proposal[:6000],
            requirements=reqs_text,
            playbook_block=playbook_block,
        ),
        max_tokens=2000,
        span_name="checklist.compliance",
        span_meta=span_meta,
    )
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    return {"issues": issues, "score": max(0, min(100, score))}


async def _pass_quality(
    proposal: str, client, model: str, span_meta: dict | None = None,
) -> dict:
    """Pass 2 — qualidade narrativa: clareza, coerência, persuasão, tom."""
    if not proposal.strip():
        return {"issues": [], "overall_score": 0}

    data = await _call_llm(
        client,
        model,
        _QUALITY_SYSTEM,
        _QUALITY_USER.format(document=proposal[:6000]),
        max_tokens=2000,
        span_name="checklist.quality",
        span_meta=span_meta,
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
    client=None,  # noqa: ARG001 — mantido para compatibilidade de interface
    model: str = "",  # noqa: ARG001 — mantido para compatibilidade de interface
    span_meta: dict | None = None,  # noqa: ARG001
) -> dict:
    """Pass 3 — completude determinística das seções (F5: zero LLM).

    Para cada seção do outline, verifica estruturalmente:
      - "empty"   → seção ausente ou só placeholder [A preencher]
      - "shallow" → presente mas muito curta (< 200 chars)
      - "adequate" → presente com conteúdo substancial
    missing_sections é derivado dos status "empty".
    overall_score = proporção de seções adequate/thorough.

    A assinatura mantém `client` e `model` por compatibilidade com a chamada
    em `auto_review_checklist` que passa esses args posicionalmente.
    """
    if not outline:
        return {"sections": [], "missing_sections": [], "overall_score": 0}

    _shallow_threshold = 200

    blocks: dict[str, str] = {}
    current_title: str | None = None
    current_body: list[str] = []
    for line in proposal.splitlines():
        header = re.match(r"^\s*##\s+(.*\S)\s*$", line)
        if header:
            if current_title is not None:
                blocks[current_title] = "\n".join(current_body).strip()
            current_title = header.group(1).strip().lower()
            current_body = []
        elif current_title is not None:
            current_body.append(line)
    if current_title is not None:
        blocks[current_title] = "\n".join(current_body).strip()

    sections_out: list[dict] = []
    missing: list[str] = []
    adequate_count = 0

    for title in outline:
        body = blocks.get(title.strip().lower(), "")
        cleaned = _PLACEHOLDER_PATTERN.sub("", body).strip()
        cleaned = cleaned.strip("*").strip()

        if not cleaned:
            status = "empty"
            suggestion = "Seção ausente ou vazia — preencha com o conteúdo solicitado."
            missing.append(title)
        elif len(cleaned) < _shallow_threshold:
            status = "shallow"
            suggestion = "Seção muito curta — desenvolva com mais detalhes e evidências."
        else:
            status = "adequate"
            suggestion = ""
            adequate_count += 1

        sections_out.append({
            "title": title,
            "status": status,
            "suggestion": suggestion,
        })

    overall_score = int((adequate_count / len(outline)) * 100) if outline else 0

    return {
        "sections": sections_out,
        "missing_sections": missing,
        "overall_score": max(0, min(100, overall_score)),
    }


# ---------------------------------------------------------------------------
# Fallbacks (quando um pass falha)
# ---------------------------------------------------------------------------

_FALLBACK_COMPLIANCE = {"issues": [], "score": 0}
_FALLBACK_QUALITY = {"issues": [], "overall_score": 0}
_FALLBACK_COMPLETENESS = {"sections": [], "missing_sections": [], "overall_score": 0}


# ---------------------------------------------------------------------------
# Triage determinística (gates ~zero-custo antes do gather)
# ---------------------------------------------------------------------------
#
# Antes de disparar os passes LLM, decidimos quais têm de fato algo a avaliar.
# Os gates são CONSERVADORES: só pulam um passe quando ele é comprovadamente
# vazio. Passe pulado devolve um resultado sintético "ok / não aplicável" com o
# MESMO shape do passe real, para não quebrar o consumidor.
#
# SHADOW (gate manual do usuário): para validar que a triage não perde achados,
# rode o all-3 em paralelo forçando `CHECKLIST_FORCE_ALL_PASSES=1` no ambiente
# (ou `force_all_passes=True`) e compare o set de issues contra o run triaged
# (sem a flag) sobre as mesmas propostas. Promova a triage só se o triaged não
# perder nenhum achado que o all-3 produzia. A flag existe exatamente para
# permitir esse A/B sem mudar o caller.

_PLACEHOLDER_PATTERN = re.compile(r"\[\s*a\s+preencher\s*\]", re.IGNORECASE)

# Resultados sintéticos para passes pulados pela triage. Score "ok" (100):
# o gate só dispara quando não há nada a apontar (sem requisitos / tudo
# preenchido), então a aderência/completude é trivialmente total.
_SKIPPED_COMPLIANCE = {"issues": [], "score": 100, "skipped": "no_requirements"}
_SKIPPED_COMPLETENESS = {
    "sections": [],
    "missing_sections": [],
    "overall_score": 100,
    "skipped": "all_sections_filled",
}


def _force_all_passes(force_all_passes: bool) -> bool:
    """True se a triage deve ser desligada (shadow/all-3)."""
    if force_all_passes:
        return True
    return os.getenv("CHECKLIST_FORCE_ALL_PASSES", "").strip().lower() in {"1", "true", "yes"}


def _all_sections_filled(proposal: str, outline: list[str]) -> bool:
    """
    True se TODAS as seções do outline têm conteúdo real no documento.

    Estrutural, sem LLM. O documento é montado como blocos '## <título>' (ver
    backend/routers/writing.py). Para cada título do outline, localiza o bloco
    e verifica que o corpo é não-vazio e não é um placeholder '[A preencher]'.

    Conservador: se não houver outline, ou se algum título não puder ser
    localizado/confirmado, retorna False (não pula o passe).
    """
    if not outline:
        return False

    # Particiona o documento em blocos por cabeçalho '## '. Cada item:
    # (título_normalizado, corpo).
    blocks: dict[str, str] = {}
    current_title: str | None = None
    current_body: list[str] = []
    for line in proposal.splitlines():
        header = re.match(r"^\s*##\s+(.*\S)\s*$", line)
        if header:
            if current_title is not None:
                blocks[current_title] = "\n".join(current_body)
            current_title = header.group(1).strip().lower()
            current_body = []
        elif current_title is not None:
            current_body.append(line)
    if current_title is not None:
        blocks[current_title] = "\n".join(current_body)

    for title in outline:
        body = blocks.get(title.strip().lower())
        if body is None:
            return False  # seção não encontrada no documento → não pular
        cleaned = _PLACEHOLDER_PATTERN.sub("", body).strip()
        # Remove marcação de ênfase residual (ex.: '*[A preencher]*' → '*…*').
        cleaned = cleaned.strip("*").strip()
        if not cleaned:
            return False  # seção vazia / só placeholder → não pular

    return True


# ---------------------------------------------------------------------------
# Entry point (async): roda os 3 passes em paralelo
# ---------------------------------------------------------------------------

async def auto_review_checklist(
    proposal: str,
    edital_requirements: list[dict] | None = None,
    outline: list[str] | None = None,
    force_all_passes: bool = False,
    playbook_context: str = "",
    workspace_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """
    Executa as passes de revisão em paralelo via asyncio.gather.

    Triage determinística (custo ~zero) decide quais passes têm o que avaliar
    ANTES do gather — pula compliance sem requisitos e completude com todas as
    seções preenchidas; qualidade roda sempre. Passe pulado devolve resultado
    sintético "ok / não aplicável" (mesmo shape do real).

    `force_all_passes=True` (ou env CHECKLIST_FORCE_ALL_PASSES=1) desliga a
    triage e roda os 3 passes — usado no shadow A/B para comparar achados.

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

    if make_async_client is None:
        logger.error("AsyncOpenAI indisponível: make_async_client não importado")
        return {
            "compliance":   dict(_FALLBACK_COMPLIANCE),
            "quality":      dict(_FALLBACK_QUALITY),
            "completeness": dict(_FALLBACK_COMPLETENESS),
            "error":        [{"pass": "all", "message": "make_async_client indisponível"}],
        }

    # --- Triage determinística (gates antes do gather) ---------------------
    force_all = _force_all_passes(force_all_passes)

    # compliance: pular se não há requisito contra o qual checar.
    run_compliance = force_all or bool(edital_requirements)
    # completude: pular se todas as seções já estão preenchidas (estrutural).
    run_completeness = force_all or not _all_sections_filled(proposal, outline)
    # qualidade: sempre roda (subjetivo, sem gate barato confiável).

    # Resultados sintéticos para passes pulados (mesmo shape do real).
    compliance_res: dict | BaseException = dict(_SKIPPED_COMPLIANCE)
    completeness_res: dict | BaseException = dict(_SKIPPED_COMPLETENESS)
    quality_res: dict | BaseException = dict(_FALLBACK_QUALITY)

    api_key, model, base_url = _llm_config()
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    # Metadata dos spans de custo (PR5): quem pagou esta revisão.
    span_meta = {
        k: v for k, v in
        (("workspace_id", workspace_id), ("session_id", session_id))
        if v
    } or None

    # Monta só os passes elegíveis; mantém o mapeamento posição→nome para
    # reatribuir os resultados na ordem certa após o gather.
    async with make_async_client(**client_kwargs) as client:
        coros = []
        labels: list[str] = []
        if run_compliance:
            coros.append(_pass_compliance(
                proposal, edital_requirements, client, model, playbook_context,
                span_meta=span_meta,
            ))
            labels.append("compliance")
        coros.append(_pass_quality(proposal, client, model, span_meta=span_meta))
        labels.append("quality")
        if run_completeness:
            coros.append(_pass_completeness(
                proposal, outline, client, model, span_meta=span_meta,
            ))
            labels.append("completeness")

        results = await asyncio.gather(*coros, return_exceptions=True)

    by_label = dict(zip(labels, results, strict=True))
    errors: list[dict] = []

    if "compliance" in by_label:
        compliance_res = by_label["compliance"]
        if isinstance(compliance_res, BaseException):
            logger.error("Pass compliance falhou: %s", compliance_res)
            errors.append({"pass": "compliance", "message": str(compliance_res)})
            compliance_res = dict(_FALLBACK_COMPLIANCE)

    quality_res = by_label["quality"]
    if isinstance(quality_res, BaseException):
        logger.error("Pass quality falhou: %s", quality_res)
        errors.append({"pass": "quality", "message": str(quality_res)})
        quality_res = dict(_FALLBACK_QUALITY)

    if "completeness" in by_label:
        completeness_res = by_label["completeness"]
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
