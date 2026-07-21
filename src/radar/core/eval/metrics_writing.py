"""
core/eval/metrics_writing.py — Métricas da rúbrica de SEÇÃO (Front 1.5).

Habilitador da aposentadoria do legacy (Front 1): sem métrica, trocar o path
de escrita é fé, não engenharia. Este módulo mede a qualidade de uma seção
redigida pelo agente sobre a rúbrica única da spec:

  • nº de afirmações sobre o edital + % com respaldo em chunk (grounding);
  • nº de erros factuais (juiz offline, mesma lente do critic);
  • coerência interna (0 contradições entre seções do doc final).

A "conclusão do save" (o agente persistiu em ≤ N turnos?) é confiabilidade do
tool-loop — medida no orquestrador `scripts/eval_agent_writing.py`, não aqui,
porque depende de rodar o agente.

Os juízes usam um modelo FIXO e capaz (gpt-4o por default) — eval só é
comparável entre runs se o juiz não muda. Override via `WRITING_EVAL_MODEL`.
As funções de PARSE são puras e testáveis sem rede; só `*_judge`/`extract_*`
tocam a OpenAI.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _eval_model() -> str:
    return os.getenv("WRITING_EVAL_MODEL") or os.getenv("OPENAI_MODEL_PRO") or "gpt-4o-mini"


def _client():
    from radar.core.llm.llm_client import make_client
    return make_client(api_key=os.environ["OPENAI_API_KEY"])


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    return raw


# =============================================================================
# 1. Extração de afirmações sobre o edital
# =============================================================================

_CLAIMS_SYSTEM = (
    "Você extrai AFIRMAÇÕES VERIFICÁVEIS sobre o edital feitas num trecho de "
    "proposta. Uma afirmação verificável é uma alegação sobre o edital que pode "
    "ser conferida no texto do edital: prazo, valor, TRL exigido, elegibilidade, "
    "mecanismo de financiamento, contrapartida, requisitos formais. NÃO extraia "
    "frases sobre a empresa, opiniões, objetivos do projeto ou linguagem de "
    "marketing. Responda APENAS com JSON {\"claims\": [\"...\", ...]}."
)


def _parse_claims(raw: str) -> list[str]:
    """Parser puro da resposta de extração de claims."""
    try:
        data = json.loads(_strip_code_fence(raw))
    except Exception:
        return []
    claims = data.get("claims", []) if isinstance(data, dict) else []
    return [str(c).strip() for c in claims if str(c).strip()]


def extract_edital_claims(section_text: str) -> list[str]:
    """Extrai afirmações verificáveis sobre o edital feitas na seção.

    Falha graciosa: [] se LLM/parse falham (a seção não pontua grounding, mas o
    eval não quebra).
    """
    if not section_text or not section_text.strip():
        return []
    try:
        resp = _client().chat.completions.create(
            model=_eval_model(),
            messages=[
                {"role": "system", "content": _CLAIMS_SYSTEM},
                {"role": "user", "content": f"TRECHO DA PROPOSTA:\n{section_text[:4000]}"},
            ],
            temperature=0.0,
            max_tokens=800,
        )
        return _parse_claims(resp.choices[0].message.content or "")
    except Exception as e:
        logger.warning("extract_edital_claims falhou: %s", e)
        return []


# =============================================================================
# 2. Grounding — % de afirmações com respaldo em chunk
# =============================================================================

_GROUNDING_SYSTEM = (
    "Você verifica se cada AFIRMAÇÃO sobre um edital tem respaldo nos TRECHOS "
    "recuperados do edital. Uma afirmação é 'grounded' (true) se os trechos a "
    "sustentam de forma direta; 'false' se a contradizem OU se simplesmente não "
    "há trecho que a sustente (alegação não-verificável a partir do contexto). "
    "Responda APENAS com JSON {\"results\": [{\"i\": <índice>, \"grounded\": "
    "true|false}, ...]}."
)


@dataclass
class GroundingResult:
    n_claims: int
    n_grounded: int
    per_claim: list[dict] = field(default_factory=list)

    @property
    def pct_grounded(self) -> float:
        return (self.n_grounded / self.n_claims) if self.n_claims else 0.0


def _parse_grounding(raw: str, n_claims: int) -> list[bool]:
    """Parser puro → lista de bool por claim (índice). Default False se ausente."""
    grounded = [False] * n_claims
    try:
        data = json.loads(_strip_code_fence(raw))
    except Exception:
        return grounded
    for item in data.get("results", []) if isinstance(data, dict) else []:
        try:
            idx = int(item.get("i", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n_claims:
            grounded[idx] = bool(item.get("grounded", False))
    return grounded


_GROUNDING_ONE_SYSTEM = (
    "Você verifica se UMA afirmação sobre um edital tem respaldo nos TRECHOS "
    "recuperados do edital. Responda 'true' apenas se os trechos sustentam a "
    "afirmação de forma direta; 'false' se a contradizem OU se não há trecho que "
    'a sustente. Responda APENAS com JSON {"grounded": true|false}.'
)


def _parse_grounded_one(raw: str) -> bool:
    try:
        data = json.loads(_strip_code_fence(raw))
    except Exception:
        return False
    return bool(data.get("grounded", False)) if isinstance(data, dict) else False


def judge_claim_grounded(claim: str, chunks: list[dict]) -> bool:
    """Juiz de UMA afirmação contra os chunks recuperados PARA ELA. False em falha."""
    if not chunks:
        return False
    chunk_text = "\n\n---\n\n".join((c.get("text") or "").strip()[:1500] for c in chunks)
    user = f"TRECHOS DO EDITAL:\n{chunk_text}\n\nAFIRMAÇÃO:\n{claim}"
    try:
        resp = _client().chat.completions.create(
            model=_eval_model(),
            messages=[
                {"role": "system", "content": _GROUNDING_ONE_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=20,
        )
        return _parse_grounded_one(resp.choices[0].message.content or "")
    except Exception as e:
        logger.warning("judge_claim_grounded falhou: %s", e)
        return False


def score_grounding(
    claims: list[str],
    chunks: list[dict] | None = None,
    *,
    retrieve_fn=None,
    k: int = 5,
) -> GroundingResult:
    """% de afirmações com respaldo em chunk.

    Dois modos:
      • PER-CLAIM (preferido): passe `retrieve_fn(query, k) -> list[chunk]`. Para
        CADA afirmação, recupera evidência específica daquela afirmação (query =
        a própria claim) e julga 1-a-1. Evita o viés de medição de checar todas
        as claims contra um único conjunto de chunks recuperado por uma query
        genérica (ex.: draft[:500]) que não cobre claims do meio/fim do texto.
      • BATCH (legado): passe `chunks` fixos; julga todas as claims de uma vez.

    Sem claims → 0/0. Falha de LLM/retrieval → claim conta como não-grounded
    (conservador: nunca infla a métrica).
    """
    if not claims:
        return GroundingResult(n_claims=0, n_grounded=0)

    if retrieve_fn is not None:
        per_claim: list[dict] = []
        for c in claims:
            try:
                evidence = retrieve_fn(c, k)
            except Exception as e:
                logger.warning("score_grounding: retrieve_fn falhou p/ claim: %s", e)
                evidence = []
            grounded = judge_claim_grounded(c, evidence)
            per_claim.append({"claim": c, "grounded": grounded, "n_evidence": len(evidence)})
        return GroundingResult(
            n_claims=len(claims),
            n_grounded=sum(1 for p in per_claim if p["grounded"]),
            per_claim=per_claim,
        )

    # Caminho batch (legado).
    chunk_text = "\n\n---\n\n".join((c.get("text") or "").strip()[:1500] for c in (chunks or []))
    numbered = "\n".join(f"[{i}] {c}" for i, c in enumerate(claims))
    user = f"TRECHOS DO EDITAL:\n{chunk_text or '(nenhum)'}\n\nAFIRMAÇÕES:\n{numbered}"
    try:
        resp = _client().chat.completions.create(
            model=_eval_model(),
            messages=[
                {"role": "system", "content": _GROUNDING_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=600,
        )
        grounded = _parse_grounding(resp.choices[0].message.content or "", len(claims))
    except Exception as e:
        logger.warning("score_grounding falhou: %s", e)
        grounded = [False] * len(claims)
    per_claim = [{"claim": c, "grounded": g} for c, g in zip(claims, grounded, strict=True)]
    return GroundingResult(
        n_claims=len(claims),
        n_grounded=sum(grounded),
        per_claim=per_claim,
    )


# =============================================================================
# 3. Erros factuais — juiz offline (mesma lente do critic)
# =============================================================================

_FACTUAL_SYSTEM = (
    "Você é um fact-checker de propostas para editais. Liste APENAS afirmações "
    "do rascunho que CONTRADIZEM o edital, contradizem outra seção da proposta, "
    "ou são internamente inconsistentes. NÃO reporte omissões nem questões de "
    "estilo. Responda APENAS com JSON {\"errors\": [\"...\", ...]} (lista vazia "
    "se nada está errado)."
)


def _parse_errors(raw: str) -> list[str]:
    try:
        data = json.loads(_strip_code_fence(raw))
    except Exception:
        return []
    errs = data.get("errors", []) if isinstance(data, dict) else []
    return [str(e).strip() for e in errs if str(e).strip()]


def judge_factual_errors(
    section_title: str,
    draft: str,
    edital_context: str,
    proposal_context: str = "",
    temporal_block: str = "",
) -> list[str]:
    """Conta erros factuais do rascunho como juiz offline. [] = sem erros."""
    if not draft or not draft.strip():
        return []
    tb = f"{temporal_block}\n\n" if temporal_block else ""
    user = (
        f"{tb}TRECHOS DO EDITAL:\n{edital_context or '(nenhum)'}\n\n"
        f"OUTRAS SEÇÕES DA PROPOSTA:\n{proposal_context or '(nenhuma)'}\n\n"
        f"SEÇÃO: {section_title}\nRASCUNHO:\n{draft[:3000]}"
    )
    try:
        resp = _client().chat.completions.create(
            model=_eval_model(),
            messages=[
                {"role": "system", "content": _FACTUAL_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=600,
        )
        return _parse_errors(resp.choices[0].message.content or "")
    except Exception as e:
        logger.warning("judge_factual_errors falhou: %s", e)
        return []


# =============================================================================
# 4. Coerência interna — contradições entre seções do doc final
# =============================================================================

_COHERENCE_SYSTEM = (
    "Você verifica COERÊNCIA INTERNA de uma proposta: contradições factuais "
    "entre suas seções (números, prazos, escopo, equipe, orçamento, objetivos "
    "divergentes). NÃO reporte repetição, estilo ou omissão. Responda APENAS "
    "com JSON {\"coherent\": true|false, \"contradictions\": [\"...\", ...]}."
)


@dataclass
class CoherenceResult:
    coherent: bool
    contradictions: list[str] = field(default_factory=list)


def _parse_coherence(raw: str) -> CoherenceResult:
    try:
        data = json.loads(_strip_code_fence(raw))
    except Exception:
        # Sem veredicto legível → assume coerente (não inventa contradição).
        return CoherenceResult(coherent=True, contradictions=[])
    contradictions = [
        str(c).strip()
        for c in (data.get("contradictions", []) if isinstance(data, dict) else [])
        if str(c).strip()
    ]
    coherent = bool(data.get("coherent", not contradictions))
    # Consistência: se há contradições listadas, não é coerente.
    if contradictions:
        coherent = False
    return CoherenceResult(coherent=coherent, contradictions=contradictions)


def judge_internal_coherence(sections: dict[str, str]) -> CoherenceResult:
    """Checa contradições entre as seções redigidas. <2 seções → trivialmente coerente."""
    filled = {t: c for t, c in sections.items() if (c or "").strip()}
    if len(filled) < 2:
        return CoherenceResult(coherent=True, contradictions=[])
    body = "\n\n".join(f"## {t}\n{c[:2000]}" for t, c in filled.items())
    try:
        resp = _client().chat.completions.create(
            model=_eval_model(),
            messages=[
                {"role": "system", "content": _COHERENCE_SYSTEM},
                {"role": "user", "content": f"PROPOSTA:\n{body}"},
            ],
            temperature=0.0,
            max_tokens=600,
        )
        return _parse_coherence(resp.choices[0].message.content or "")
    except Exception as e:
        logger.warning("judge_internal_coherence falhou: %s", e)
        return CoherenceResult(coherent=True, contradictions=[])


# =============================================================================
# Agregação
# =============================================================================

def aggregate_writing_runs(per_case: list[dict]) -> dict:
    """Agrega métricas da rúbrica de seção entre casos.

    Cada item de `per_case` pode conter:
      n_claims, n_grounded, n_factual_errors, saved (bool), turns_to_save (int|None),
      coherent (bool). Campos ausentes são ignorados na sua média.
    """
    if not per_case:
        return {}
    n = len(per_case)
    total_claims = sum(c.get("n_claims", 0) for c in per_case)
    total_grounded = sum(c.get("n_grounded", 0) for c in per_case)
    saved = [c for c in per_case if "saved" in c]
    coherent = [c for c in per_case if "coherent" in c]
    turns = [c["turns_to_save"] for c in per_case if c.get("turns_to_save") is not None]

    out: dict = {
        "n_cases": n,
        "total_claims": total_claims,
        "total_grounded": total_grounded,
        "pct_grounded": (total_grounded / total_claims) if total_claims else 0.0,
        "total_factual_errors": sum(c.get("n_factual_errors", 0) for c in per_case),
        "mean_factual_errors": sum(c.get("n_factual_errors", 0) for c in per_case) / n,
    }
    if saved:
        out["save_rate"] = sum(1 for c in saved if c.get("saved")) / len(saved)
    if turns:
        out["mean_turns_to_save"] = sum(turns) / len(turns)
    if coherent:
        out["coherence_rate"] = sum(1 for c in coherent if c.get("coherent")) / len(coherent)
    return out


__all__ = [
    "CoherenceResult",
    "GroundingResult",
    "aggregate_writing_runs",
    "extract_edital_claims",
    "judge_factual_errors",
    "judge_internal_coherence",
    "score_grounding",
]
