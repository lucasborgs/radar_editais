"""Contrato puro de roteamento do Explorar.

Regras de alta precisão resolvem comandos, ações e perguntas factuais. O agente
continua responsável por linguagem ambígua, mas não pode alterar escopo,
autoridade documental ou emitir redirects por conta própria.
"""
from __future__ import annotations

import json
import logging
import os
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    EDITAL_SUMMARY = "EDITAL_SUMMARY"
    EDITAL_FACT = "EDITAL_FACT"
    EDITAL_FACT_ENUMERATIVE = "EDITAL_FACT_ENUMERATIVE"
    ENTITY_FACT = "ENTITY_FACT"
    DISCOVERY = "DISCOVERY"
    MATCH_PROFILE = "MATCH_PROFILE"
    CONCEPTUAL = "CONCEPTUAL"
    PLAN_ACTION = "PLAN_ACTION"
    WRITING_ACTION = "WRITING_ACTION"


@dataclass(frozen=True)
class RouteContext:
    mode: str = "explorer"
    target_type: str | None = None
    target_id: str | None = None
    message: str = ""
    has_profile: bool = False
    has_documents: bool = False
    workspace_id: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    intent: Intent
    target_type: str | None
    target_id: str | None
    facets: tuple[str, ...] = ()
    confidence: float = 1.0
    reason_code: str = "deterministic"
    retrieval_profile: str = "gold_summary"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["intent"] = self.intent.value
        data["facets"] = list(self.facets)
        return data


_ENUMERATIVE = (
    "quais", "liste", "listar", "todos", "todas", "itens", "criterios",
    "requisitos", "documentos", "despesas", "rubricas", "vedacoes",
)
_FACTUAL_FACETS: dict[str, tuple[str, ...]] = {
    "financiaveis": ("financiavel", "financiaveis", "despesa", "rubrica"),
    "admissibilidade": ("admissibilidade", "admissivel"),
    "elegibilidade": ("elegibilidade", "elegivel", "requisito"),
    "contrapartida": ("contrapartida",),
    "prazo": ("prazo", "data limite", "deadline"),
    "valor": ("valor", "quanto", "orcamento"),
    "avaliacao": ("criterio de avaliacao", "criterios de avaliacao", "merito"),
}
_ENTITY_TERMS = (
    "investidor", "investimento", "fundo", "tese", "vertical", "verticais",
    "portfolio", "ticket", "estagio", "ict", "programa", "instituto",
)
_WRITE_ACTIONS = (
    "escreva", "redija", "reescreva", "refine", "melhore o texto",
    "crie a secao", "salve o rascunho", "gerar texto",
)
_PLAN_ACTIONS = (
    "crie um plano", "gere um plano", "planeje a proposta", "ajuste o plano",
    "modifique o plano", "estruture a proposta",
)


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _profile_for(intent: Intent) -> str:
    return {
        Intent.EDITAL_SUMMARY: "gold_summary",
        Intent.EDITAL_FACT: "factual_point",
        Intent.EDITAL_FACT_ENUMERATIVE: "factual_enumerative",
        Intent.ENTITY_FACT: "entity_detail",
        Intent.DISCOVERY: "discovery_semantic",
        Intent.MATCH_PROFILE: "match",
    }.get(intent, "none")


def route_message(
    context: RouteContext,
    ambiguous_classifier: Callable[[RouteContext], Intent | str | None] | None = None,
) -> RouteDecision:
    """Classifica sem executar tools ou responder ao usuário.

    ``ambiguous_classifier`` é o seam do classificador LLM fechado. Ele só é
    consultado quando nenhuma regra de alta precisão decide a intenção.
    """
    text = _fold(context.message).strip()
    target_type = _fold(context.target_type or "") or None
    resolved_target_type = context.target_type
    resolved_target_id = context.target_id

    if text.startswith("/plan") or _contains_any(text, _PLAN_ACTIONS):
        intent = Intent.PLAN_ACTION
        reason = "explicit_plan_action"
    elif text.startswith("/escrita") or _contains_any(text, _WRITE_ACTIONS):
        intent = Intent.WRITING_ACTION
        reason = "explicit_writing_action"
    elif target_type in {"investidor", "programa", "ict"} or _contains_any(text, _ENTITY_TERMS):
        intent = Intent.ENTITY_FACT
        reason = "named_or_focused_entity"
        if target_type not in {"investidor", "programa", "ict"}:
            if _contains_any(text, ("investidor", "investimento", "fundo", "tese", "vertical", "portfolio", "ticket")):
                target_type = "investidor"
            elif "ict" in text or "instituto" in text:
                target_type = "ict"
            elif "programa" in text:
                target_type = "programa"
        resolved_target_type = target_type
        resolved_target_id = (
            context.target_id if _fold(context.target_type or "") == target_type else None
        )
    else:
        facets = tuple(
            facet for facet, terms in _FACTUAL_FACETS.items()
            if _contains_any(text, terms)
        )
        if (target_type == "edital" or context.target_id or "edital" in text) and facets:
            is_list = _contains_any(text, _ENUMERATIVE)
            intent = Intent.EDITAL_FACT_ENUMERATIVE if is_list else Intent.EDITAL_FACT
            reason = "edital_factual_enumerative" if is_list else "edital_factual_point"
            return RouteDecision(
                intent, context.target_type or "edital", context.target_id, facets,
                1.0, reason, _profile_for(intent),
            )
        if _contains_any(text, ("para minha empresa", "para a gente", "meu perfil", "afinidade")):
            intent = Intent.MATCH_PROFILE
            reason = "explicit_profile_match"
        elif _contains_any(text, ("quais oportunidades", "o que existe", "encontre", "buscar")):
            intent = Intent.DISCOVERY
            reason = "explicit_discovery"
        elif target_type == "edital" or context.target_id:
            intent = Intent.EDITAL_SUMMARY
            reason = "focused_edital_default"
        else:
            classified = ambiguous_classifier(context) if ambiguous_classifier else None
            try:
                intent = classified if isinstance(classified, Intent) else Intent(str(classified))
                reason = "bounded_classifier"
            except (TypeError, ValueError):
                intent = Intent.CONCEPTUAL
                reason = "safe_conceptual_fallback"

    return RouteDecision(
        intent=intent,
        target_type=resolved_target_type,
        target_id=resolved_target_id,
        confidence=1.0 if reason != "safe_conceptual_fallback" else 0.5,
        reason_code=reason,
        retrieval_profile=_profile_for(intent),
    )


def redirect_for(decision: RouteDecision, current_mode: str = "explorer") -> str | None:
    """Redirect determinístico apenas para ações explícitas fora do modo.

    Deprecated: use handoff_target() para transição fluida.
    Mantido para compatibilidade de chamadas diretas a _dispatch_explorer.
    """
    if current_mode != "explorer":
        return None
    if decision.intent == Intent.WRITING_ACTION:
        return "⚠ Você está no modo /explorer. Para escrever ou refinar a proposta, digite /escrita no chat."
    return None


def handoff_target(decision: RouteDecision, current_mode: str) -> str | None:
    """Retorna o modo-alvo de handoff ou None se o modo atual já é o correto.

    Função pura, sem I/O — testável isoladamente. Substitui redirect_for
    no fluxo do dispatch(): o código decide a troca de produtor, não o
    modelo.

    Mapeamento pós-Task 4:
      WRITING_ACTION / PLAN_ACTION → escrita (escrita absorve o plano)
      intents factuais/de exploração → explorer
      None se já está no modo correto

    Segurança: nunca faz handoff quando a classificação veio do fallback
    de baixa confiança (reason_code == safe_conceptual_fallback).
    Mensagens ambíguas não expulsam o usuário do modo atual.
    """
    # Nunca força handoff em classificação de baixa confiança — a mensagem
    # pode ser ambígua e o usuário não pediu para trocar de skill.
    if decision.reason_code == "safe_conceptual_fallback":
        return None

    _factual_ints = frozenset({
        Intent.EDITAL_SUMMARY,
        Intent.EDITAL_FACT,
        Intent.EDITAL_FACT_ENUMERATIVE,
        Intent.ENTITY_FACT,
        Intent.DISCOVERY,
        Intent.MATCH_PROFILE,
        Intent.CONCEPTUAL,
    })
    if decision.intent in (Intent.WRITING_ACTION, Intent.PLAN_ACTION):
        if current_mode != "escrita":
            return "escrita"
        return None
    if decision.intent in _factual_ints:
        if current_mode != "explorer":
            return "explorer"
        return None
    return None


def classify_ambiguous_route(context: RouteContext) -> Intent | None:
    """Classificador LLM fechado; sem tools e sem autoridade para responder."""
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    else:
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = None
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        return None
    try:
        from radar.core.llm.llm_client import make_client

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        response = make_client(**kwargs).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": (
                    "Classifique a intenção em exatamente um valor: EDITAL_SUMMARY, "
                    "EDITAL_FACT, EDITAL_FACT_ENUMERATIVE, ENTITY_FACT, DISCOVERY, "
                    "MATCH_PROFILE, CONCEPTUAL, PLAN_ACTION ou WRITING_ACTION. "
                    "Retorne JSON {\"intent\": \"...\"}. Não responda à pergunta."
                )},
                {"role": "user", "content": json.dumps({
                    "mode": context.mode, "target_type": context.target_type,
                    "message": context.message,
                }, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        return Intent(payload.get("intent"))
    except Exception as exc:  # classificação é best-effort; fallback é seguro
        logger.warning("classificador de rota falhou: %s", exc)
        return None
