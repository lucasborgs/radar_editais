"""Workspace multi-modo — dispatcher de skills.

Dispatcher que roteia mensagens ao agente correto com base no modo atual:
  /explorer → ExploreAgent (RAG + KG, dúvidas sobre o edital)
  /escrita  → WritingSession (escrever/refinar seções)

RAG e KG são transversais — todos os modos acessam ambos.
"""
from __future__ import annotations

import json
import logging

from core.services.explore_agent import ExploreAgent

logger = logging.getLogger(__name__)

MODE_EXPLORER = "explorer"
MODE_ESCRITA = "escrita"

VALID_MODES = frozenset({MODE_EXPLORER, MODE_ESCRITA})

# ── Blocos de redirecionamento ──────────────────────────────────────────────
# Cada modo tem: escopo, ações fora-de-escopo, e mensagem de redirecionamento.
# Injetados no system prompt do agente via {mode_redirect_block}.

_REDIRECT_BLOCK = """
MODO ATUAL: /{mode}
ESCOPO: {scope}

Se o usuário pedir algo fora do escopo, o sistema troca de contexto
automaticamente. Mantenha o foco no escopo atual.
"""

MODE_CONFIG: dict[str, dict] = {
    MODE_EXPLORER: {
        "scope": "Responder perguntas sobre o edital, o hipergrado "
                 "(temas, ICTs, investidores), os anexos da biblioteca "
                 "e o cenário de fomento.",
        "out_of_scope": "Gerar planos de proposta, escrever seções do "
                        "rascunho, refinar conteúdo existente.",
        "redirect_action": "escrever ou refinar a proposta",
        "redirect_mode": "escrita",
        "redirect_offer": "explorar o edital",
        "welcome": "🧭 Modo Explorer — tire dúvidas sobre o edital. "
                   "Para escrever, digite /escrita.",
    },
    MODE_ESCRITA: {
        "scope": "Escrever, refinar e salvar seções da proposta com "
                 "validação do Critic automático.",
        "out_of_scope": "Explorar o edital em profundidade.",
        "redirect_action": "explorar o edital em detalhes",
        "redirect_mode": "explorer",
        "redirect_offer": "escrever a proposta",
        "welcome": "✍️ Modo Escrita — escreva sua proposta. "
                   "Para dúvidas sobre o edital, digite /explorer.",
    },
}


# ── Prefixos de transição fluida ──────────────────────────────────────────
# Injetados antes da resposta do produtor alvo quando o dispatch faz handoff.
TRANSITION_PREFIXES: dict[tuple[str, str], str] = {
    ("escrita", "explorer"): "↪ Entendi que você quer escrever — troquei para /escrita.\n\n",
    ("explorer", "escrita"): "↪ Entendi sua pergunta sobre o edital — troquei para /explorer.\n\n",
}


def mode_redirect_block(mode: str) -> str:
    """Retorna o bloco de redirecionamento para o system prompt do modo."""
    cfg = MODE_CONFIG.get(mode)
    if not cfg:
        return ""
    return _REDIRECT_BLOCK.format(
        mode=mode,
        scope=cfg["scope"],
        out_of_scope=cfg["out_of_scope"],
        redirect_action=cfg["redirect_action"],
        redirect_mode=cfg["redirect_mode"],
        redirect_offer=cfg["redirect_offer"],
    )


def mode_welcome(mode: str) -> str:
    """Mensagem de boas-vindas ao entrar num modo."""
    cfg = MODE_CONFIG.get(mode)
    return cfg["welcome"] if cfg else f"Modo /{mode} ativado."


def _mode_history(
    db,
    session_id: str,
    mode: str,
    window: int = 8,
) -> list[dict]:
    """Retorna últimas N entradas do modo específico, ordenadas."""
    try:
        rows = (
            db.table("session_turns")
            .select("role, content")
            .eq("session_id", session_id)
            .eq("mode", mode)
            .order("turn_index", ascending=False)
            .limit(window)
            .execute()
        )
        data = rows.data if rows else []
        return list(reversed(data))
    except Exception as e:
        logger.debug("mode_history erro: %s", e)
        return []


def _mode_history_all(
    db,
    session_id: str,
    window: int = 8,
) -> list[dict]:
    """Retorna últimas N entradas de TODOS os modos (fallback)."""
    try:
        rows = (
            db.table("session_turns")
            .select("role, content, mode")
            .eq("session_id", session_id)
            .order("turn_index", ascending=False)
            .limit(window)
            .execute()
        )
        data = rows.data if rows else []
        return list(reversed(data))
    except Exception as e:
        logger.debug("mode_history_all erro: %s", e)
        return []


def _save_turn(
    db,
    session_id: str,
    mode: str,
    role: str,
    content: str,
    turn_index: int | None = None,
) -> dict | None:
    """Persiste um turno com o modo atual."""
    try:
        payload = {"role": role, "content": content, "mode": mode}
        if turn_index is not None:
            payload["turn_index"] = turn_index
        result = (
            db.table("session_turns")
            .insert(payload)
            .execute()
        )
        return result.data[0] if result and result.data else None
    except Exception as e:
        logger.debug("save_turn erro: %s", e)
        return None


def dispatch(
    db,
    session_id: str,
    workspace_id: str,
    profile,
    mode: str,
    message: str,
    library_items: list | None = None,
) -> dict:
    """Roteia uma mensagem ao agente correto com base no modo.

    Args:
        db: Supabase client.
        session_id: ID da sessão de escrita.
        workspace_id: ID do workspace.
        profile: CompanyProfile do usuário.
        mode: Modo atual (explorer | escrita).
        message: Mensagem do usuário.
        library_items: Itens da biblioteca (anexos).

    Returns:
        Dict com:
          - mode: str (modo atual)
          - response: str (resposta do agente)
          - welcome: str | None (mensagem de boas-vindas se modo mudou)
          - error: str | None
    """
    if mode not in VALID_MODES:
        return {"mode": mode, "response": "", "welcome": None,
                "error": f"Modo inválido: {mode}. Use /explorer ou /escrita."}

    history = _mode_history(db, session_id, mode, window=8)

    # Carrega edital_id da sessão (útil para explorer)
    edital_id = _load_session_edital_id(db, session_id)

    # Classifica a intenção uma vez no dispatch (antes de chamar o produtor)
    from core.services.explore_routing import (
        RouteContext,
        classify_ambiguous_route,
        handoff_target,
        route_message,
    )

    decision = route_message(RouteContext(
        mode=mode,
        target_type="edital" if edital_id else None,
        target_id=edital_id if edital_id else None,
        message=message,
        has_profile=profile is not None,
        has_documents=bool(library_items),
    ), ambiguous_classifier=classify_ambiguous_route)

    target = handoff_target(decision, mode)

    if target is not None and target != mode:
        # Handoff fluido: o código decide a troca de produtor
        try:
            if target == MODE_ESCRITA:
                producer_response = _dispatch_escrita(
                    db, session_id, workspace_id, profile, message, library_items,
                )
            elif target == MODE_EXPLORER:
                producer_response = _dispatch_explorer(
                    message, history, profile,
                    edital_ids=[edital_id] if edital_id else None,
                    library_items=library_items,
                    decision=decision,
                )
            else:
                producer_response = "Modo não reconhecido."
        except Exception as e:
            logger.error("dispatch handoff erro %s→%s: %s", mode, target, e)
            return {"mode": mode, "response": "", "welcome": None,
                    "error": f"Erro no modo /{target}: {e}"}

        prefix = TRANSITION_PREFIXES.get((target, mode),
                                         f"↪ Entendi, troquei para /{target}.\n\n")
        response = prefix + producer_response

        # Persiste turno no modo-alvo (a conversa continua lá)
        _save_turn(db, session_id, target, "user", message)
        _save_turn(db, session_id, target, "assistant", response)
        return {"mode": target, "response": response, "welcome": None, "error": None}

    try:
        if mode == MODE_EXPLORER:
            response = _dispatch_explorer(
                message, history, profile,
                edital_ids=[edital_id] if edital_id else None,
                library_items=library_items,
                decision=decision,
            )
        elif mode == MODE_ESCRITA:
            response = _dispatch_escrita(
                db, session_id, workspace_id, profile, message, library_items,
            )
        else:
            response = "Modo não reconhecido."
    except Exception as e:
        logger.error("dispatch erro no modo %s: %s", mode, e)
        return {"mode": mode, "response": "", "welcome": None,
                "error": f"Erro no modo /{mode}: {e}"}

    # Persiste o turno (user + assistant)
    _save_turn(db, session_id, mode, "user", message)
    _save_turn(db, session_id, mode, "assistant", response)

    return {"mode": mode, "response": response, "welcome": None, "error": None}


def _load_session_edital_id(db, session_id: str) -> str | None:
    """Carrega o edital_id da sessão de escrita."""
    try:
        row = (
            db.table("writing_sessions")
            .select("edital_id")
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        if row and row.data:
            return row.data.get("edital_id")
    except Exception as e:
        logger.debug("load_session_edital_id erro: %s", e)
    return None


def _library_summary(library_items: list | None) -> str:
    """Gera resumo textual dos itens da biblioteca para contexto do explorador."""
    if not library_items:
        return ""
    lines = ["\n--- ANEXOS DISPONÍVEIS ---"]
    for item in library_items:
        name = item.get("title") or item.get("filename", "sem nome")
        kind = item.get("kind", "documento")
        lines.append(f"- {name} ({kind})")
    return "\n".join(lines)


def _dispatch_explorer(
    message: str,
    history: list[dict],
    profile,
    edital_ids: list[str] | None = None,
    library_items: list | None = None,
    decision=None,
) -> str:
    """Modo /explorer: ExploreAgent contextualizado ao edital e anexos.

    Quando ``decision`` (RouteDecision) é fornecido pelo dispatch(),
    a classificação de rota já foi feita uma vez — não repete.
    """
    from core.services.explore_routing import (
        RouteContext,
        classify_ambiguous_route,
        redirect_for,
        route_message,
    )

    agent = ExploreAgent()

    if decision is None:
        decision = route_message(RouteContext(
            mode=MODE_EXPLORER,
            target_type="edital" if edital_ids else None,
            target_id=edital_ids[0] if edital_ids else None,
            message=message,
            has_profile=profile is not None,
            has_documents=bool(library_items),
        ), ambiguous_classifier=classify_ambiguous_route)
        redirect = redirect_for(decision, MODE_EXPLORER)
        if redirect:
            return redirect

    profile_text = None
    if profile:
        try:
            profile_text = (
                json.dumps(profile, ensure_ascii=False, default=str)
                if isinstance(profile, dict)
                else str(profile)
            )
        except Exception:
            profile_text = None

    # Constrói o prompt somente com contexto factual. Redirects são decididos
    # pela política pura acima e nunca anexados à mensagem do usuário.
    context_parts = [_mode_history_str(history)]
    if edital_ids:
        context_parts.append(f"EDITAL-ID: {edital_ids[0]}")
    lib_summary = _library_summary(library_items)
    if lib_summary:
        context_parts.append(lib_summary)

    msg_with_hint = (
        f"{message}\n\n"
        f"[CONTEXTO]\n" + "\n".join(context_parts)
    )

    answer = agent.explore(
        message=msg_with_hint,
        history=history,
        edital_ids=edital_ids,
        has_profile=profile is not None,
        profile_text=profile_text,
        profile=profile,
        route_decision=decision,
    )
    return answer


def _dispatch_escrita(
    db,
    session_id: str,
    workspace_id: str,
    profile,
    message: str,
    library_items: list | None = None,
) -> str:
    """Modo /escrita: WritingSession."""
    from core.services import writing_session as _ws_mod
    ws_cls = _ws_mod.WritingSession

    session = ws_cls(
        db=db,
        workspace_id=workspace_id,
        profile=profile,
        session_id=session_id,
        library_items=library_items or [],
    )

    # Injeta redirect block na mensagem para o agente saber o modo
    enriched = f"{message}\n\n{mode_redirect_block(MODE_ESCRITA)}"

    result = session.turn(user_message=enriched)
    return result.get("assistant_message", result.get("error", "Erro no modo escrita."))


def _mode_history_str(history: list[dict]) -> str:
    if not history:
        return "(sem histórico)"
    return "\n".join(
        f"{'Você' if h.get('role') == 'user' else 'Assistente'}: {h.get('content', '')[:200]}"
        for h in history[-4:]
    )
