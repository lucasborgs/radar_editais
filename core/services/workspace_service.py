"""Workspace multi-modo (Fase 1 da spec workspace-multi-mode).

Dispatcher que roteia mensagens ao agente correto com base no modo atual:
  /explorer → ExploreAgent (RAG + KG, dúvidas sobre o edital)
  /plan     → PlanningNode (gerar/ajustar plano da proposta)
  /escrita  → WritingSession (escrever/refinar seções)

RAG e KG são transversais — todos os modos acessam ambos.
"""
from __future__ import annotations

import json
import logging

from core.services.explore_agent import ExploreAgent

logger = logging.getLogger(__name__)

MODE_EXPLORER = "explorer"
MODE_PLAN = "plan"
MODE_ESCRITA = "escrita"

VALID_MODES = frozenset({MODE_EXPLORER, MODE_PLAN, MODE_ESCRITA})

# ── Blocos de redirecionamento ──────────────────────────────────────────────
# Cada modo tem: escopo, ações fora-de-escopo, e mensagem de redirecionamento.
# Injetados no system prompt do agente via {mode_redirect_block}.

_REDIRECT_BLOCK = """
MODO ATUAL: /{mode}
ESCOPO: {scope}
NÃO FAZ: {out_of_scope}

Se o usuário pedir algo FORA DO ESCOPO, responda APENAS:
"⚠ Você está no modo /{mode}. Para {redirect_action}, digite /{redirect_mode}
no chat. Quer que eu te ajude com {redirect_offer}?"

NÃO tente executar ações fora do escopo — recuse educadamente e redirecione.
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
                   "Quando quiser planejar, digite /plan.",
    },
    MODE_PLAN: {
        "scope": "Gerar, visualizar e ajustar o plano estruturado da "
                 "proposta (seções, alinhamento empresa↔edital, "
                 "compliance hints).",
        "out_of_scope": "Explorar o edital em profundidade, escrever "
                        "conteúdo das seções, refinar rascunhos.",
        "redirect_action": "explorar o edital em detalhes",
        "redirect_mode": "explorer",
        "redirect_offer": "planejar a proposta",
        "welcome": "📋 Modo Plan — planeje a estrutura da proposta. "
                   "Para escrever, digite /escrita.",
    },
    MODE_ESCRITA: {
        "scope": "Escrever, refinar e salvar seções da proposta com "
                 "validação do Critic automático.",
        "out_of_scope": "Explorar o edital em profundidade, gerar ou "
                        "modificar o plano da proposta.",
        "redirect_action": "modificar o plano da proposta",
        "redirect_mode": "plan",
        "redirect_offer": "escrever a proposta",
        "welcome": "✍️ Modo Escrita — escreva sua proposta. "
                   "Para dúvidas sobre o edital, digite /explorer.",
    },
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
        mode: Modo atual (explorer | plan | escrita).
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
                "error": f"Modo inválido: {mode}. Use /explorer, /plan ou /escrita."}

    history = _mode_history(db, session_id, mode, window=8)

    # Carrega edital_id da sessão (útil para explorer e plan)
    edital_id = _load_session_edital_id(db, session_id)

    try:
        if mode == MODE_EXPLORER:
            response = _dispatch_explorer(
                message, history, profile,
                edital_ids=[edital_id] if edital_id else None,
                library_items=library_items,
            )
        elif mode == MODE_PLAN:
            response = _dispatch_plan(
                db, session_id, message, profile,
                edital_id=edital_id,
                library_items=library_items,
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
) -> str:
    """Modo /explorer: ExploreAgent contextualizado ao edital e anexos."""
    agent = ExploreAgent()

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

    # Constrói o prompt com contexto do edital + anexos + redirect block
    context_parts = [_mode_history_str(history)]
    if edital_ids:
        context_parts.append(f"EDITAL-ID: {edital_ids[0]}")
    lib_summary = _library_summary(library_items)
    if lib_summary:
        context_parts.append(lib_summary)

    msg_with_hint = (
        f"{message}\n\n"
        f"[CONTEXTO]\n" + "\n".join(context_parts) + "\n"
        f"{mode_redirect_block(MODE_EXPLORER)}"
    )

    answer = agent.explore(
        message=msg_with_hint,
        history=history,
        edital_ids=edital_ids,
        has_profile=profile is not None,
        profile_text=profile_text,
        profile=profile,
    )
    return answer


def _dispatch_plan(
    db,
    session_id: str,
    message: str,
    profile,
    edital_id: str | None = None,
    library_items: list | None = None,
) -> str:
    """Modo /plan: gera ou ajusta o plano da proposta."""
    from core.kg.planning_node import generate_plan

    # Carrega o plano existente (se houver)
    existing_plan = None
    try:
        row = (
            db.table("writing_sessions")
            .select("section_drafts")
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        if row and row.data:
            drafts = row.data.get("section_drafts") or {}
            existing_plan = drafts.get("__plan__")
    except Exception as e:
        logger.debug("dispatch_plan: erro ao ler __plan__: %s", e)

    if existing_plan:
        # Ajuste do plano existente
        return _adjust_plan(existing_plan, message)
    else:
        # Geração de novo plano
        plan = generate_plan(
            question=message,
            analysis="",
            edital_id=edital_id,
            company_nodes=None,
        )
        if "error" in plan:
            return f"Não foi possível gerar o plano: {plan['error']}"

        # Salva o plano no JSONB
        try:
            row = (
                db.table("writing_sessions")
                .select("section_drafts")
                .eq("id", session_id)
                .maybe_single()
                .execute()
            )
            drafts = dict(row.data.get("section_drafts") or {}) if row and row.data else {}
            drafts["__plan__"] = plan
            db.table("writing_sessions").update({
                "section_drafts": drafts,
            }).eq("id", session_id).execute()
        except Exception as e:
            logger.debug("dispatch_plan: erro ao salvar __plan__: %s", e)

        return _format_plan_response(plan)


def _adjust_plan(existing_plan: dict, instruction: str) -> str:
    """Ajusta o plano existente baseado na instrução do usuário."""
    from core.llm.llm_client import make_client

    client = make_client()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Você ajusta um plano de proposta de fomento. "
                    "Recebe o plano atual em JSON e uma instrução de ajuste. "
                    "Retorne APENAS o JSON do plano ajustado, com a mesma estrutura."
                )},
                {"role": "user", "content": (
                    f"PLANO ATUAL:\n{json.dumps(existing_plan, ensure_ascii=False, indent=2)}\n\n"
                    f"INSTRUÇÃO: {instruction}\n\n"
                    f"{mode_redirect_block(MODE_PLAN)}"
                )},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        if raw:
            adjusted = json.loads(raw)
            return _format_plan_response(adjusted)
        return "Não consegui ajustar o plano."
    except Exception as e:
        logger.debug("adjust_plan erro: %s", e)
        return f"Erro ao ajustar plano: {e}"


def _format_plan_response(plan: dict) -> str:
    """Formata o plano como texto legível para o chat."""
    title = plan.get("title", "Proposta")
    sections = plan.get("sections", [])
    alignment = plan.get("alignment", {})
    hints = plan.get("compliance_hints", [])

    lines = [f"## {title}", ""]
    lines.append(f"**{len(sections)} seções**")
    for sec in sections:
        lines.append("")
        lines.append(f"### {sec.get('title', '')}")
        if sec.get("estimated_length"):
            lines.append(f"*~{sec['estimated_length']}*")
        if sec.get("description"):
            lines.append(f"{sec['description']}")
        if sec.get("key_points"):
            for kp in sec["key_points"]:
                lines.append(f"- {kp}")

    if alignment:
        lines.extend(["", "**Alinhamento:**"])
        score = alignment.get("match_score")
        if score is not None:
            lines.append(f"Match: {score * 100:.0f}%")
        if alignment.get("critical_gaps"):
            lines.append("Gaps:")
            for g in alignment["critical_gaps"]:
                lines.append(f"- ⚠ {g}")

    if hints:
        lines.extend(["", "**Compliance:**"])
        for h in hints:
            lines.append(f"- ⚠ {h}")

    lines.append("")
    lines.append("---")
    lines.append("Quer ajustar algo ou posso salvar e começar a escrever?")
    return "\n".join(lines)


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
