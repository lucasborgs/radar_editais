"""Workspace — dispatcher de ações one-shot.

A Workspace é um ambiente de execução de uma proposta/pitch: redação/refinamento
(WritingSession) e dúvidas factuais sobre o edital via o fluxo de escrita/RAG.
Mensagens normais de chat vão direto ao fluxo de escrita (`/writing/turn/stream`);
o endpoint `/workspace/{session}/mode` deste dispatcher serve apenas as ações
one-shot (`/profile`, `/review`).

Não existe mais o modo `/explorer` (ExploreAgent) nem handoff de produtores —
a navegação estratégica do KG vive exclusivamente no Explorar global.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VALID_ACTIONS = frozenset({"profile", "review"})


def dispatch(
    db,
    session_id: str,
    workspace_id: str,
    profile,
    mode: str,
    message: str,
    library_items: list | None = None,
) -> dict:
    """Roteia uma ação one-shot da Workspace.

    Args:
        db: Supabase client.
        session_id: ID da sessão de escrita.
        workspace_id: ID do workspace.
        profile: CompanyProfile do usuário.
        mode: Ação (/profile | /review).
        message: Conteúdo da ação.
        library_items: Itens da biblioteca (anexos).

    Returns:
        Dict com:
          - mode: str (ação executada)
          - response: str (resposta da ação)
          - welcome: str | None (sempre None — não há modos)
          - error: str | None
    """
    if mode not in VALID_ACTIONS:
        return {"mode": mode, "response": "", "welcome": None,
                "error": f"Ação inválida: {mode}. Use /profile ou /review."}

    if mode == "profile":
        response = _dispatch_profile(
            db, session_id, workspace_id, profile, message, library_items,
        )
    elif mode == "review":
        response = _dispatch_review(
            db, session_id, workspace_id, profile, message, library_items,
        )
    else:
        response = f"Ação desconhecida: /{mode}."
    return {"mode": mode, "response": response, "welcome": None, "error": None}


def _dispatch_profile(
    db,
    session_id: str,
    workspace_id: str,
    profile,
    message: str,
    library_items: list | None = None,
) -> str:
    """Ação /profile: extrai sugestão de perfil de uma URL via ProfileExtractor.

    Nunca persiste — só retorna a sugestão + confiança.
    Sem URL na mensagem → pede a URL sem chamar LLM.
    """
    import re

    from radar.core.ingestion.profile_extractor import ProfileExtractor

    url_match = re.search(r'https?://[^\s]+', message)
    if not url_match:
        return (
            "**Extrair Perfil** — Forneça a URL do site da empresa.\n\n"
            "Exemplo: `/profile https://minhaempresa.com.br`"
        )

    url = url_match.group(0)
    extractor = ProfileExtractor()
    result = extractor.extract(url)

    if result.error:
        return f"**Erro ao extrair perfil:** {result.error}"

    lines = [f"**Sugestão de perfil** — fonte: {result.source_title}"]
    pd = result.profile
    lines.append(f"- **Nome:** {pd.nome or '—'}")
    lines.append(f"- **Tipo:** {pd.tipo_entidade or '—'}")
    lines.append(f"- **One-liner:** {pd.one_liner or '—'}")
    lines.append(f"- **Solução:** {pd.solution_summary or '—'}")
    lines.append(f"- **Atividades:** {pd.descricao_atividades or '—'}")
    if pd.uf:
        lines.append(f"- **UF:** {pd.uf}")
    if pd.ano_fundacao is not None:
        lines.append(f"- **Ano fundação:** {pd.ano_fundacao}")
    if pd.tamanho_empresa:
        lines.append(f"- **Porte:** {pd.tamanho_empresa}")
    if pd.trl is not None:
        lines.append(f"- **TRL:** {pd.trl}")

    conf_label = "Baixa" if result.low_confidence else "Média/Alta"
    lines.append(f"\n**Confiança:** {conf_label}")
    lines.append("*Isso é uma sugestão — nada foi salvo.*")
    return "\n".join(lines)


def _dispatch_review(
    db,
    session_id: str,
    workspace_id: str,
    profile,
    message: str,
    library_items: list | None = None,
) -> str:
    """Ação /review: dispara o Critic sobre a seção especificada.

    Consulta sem side-effect — nenhum set_section_content, nenhum save.
    Sem seção resolvível → lista o outline e não chama o critic.
    """
    from radar.core.llm.agent_tools.critic_agent import run_critic
    from radar.core.services import writing_session as _ws_mod

    ws_cls = _ws_mod.WritingSession
    session = ws_cls(
        db=db,
        workspace_id=workspace_id,
        profile=profile,
        session_id=session_id,
        library_items=library_items or [],
    )

    section_title = message.strip()

    if not section_title:
        outline = getattr(session, "_proposal_outline", [])
        if not outline:
            return "Nenhuma seção disponível para revisão."
        lines = ["**Revisar Seção** — Seções disponíveis:"]
        for t in outline:
            has = bool(getattr(session, "_doc_sections", {}).get(t, "").strip())
            lines.append(f"- `{t}`{' (com rascunho)' if has else ''}")
        lines.append("\nUse `/review <título>` para revisar uma seção específica.")
        return "\n".join(lines)

    doc_sections = getattr(session, "_doc_sections", {})
    outline = getattr(session, "_proposal_outline", [])

    target = section_title
    if target not in doc_sections:
        norm = target.strip().lower()
        for t in outline:
            if t.strip().lower() == norm:
                target = t
                break
        else:
            avail = "\n".join(f"- `{t}`" for t in outline)
            return f"Seção \"{section_title}\" não encontrada. Seções disponíveis:\n{avail}"

    content = doc_sections.get(target, "")
    if not content.strip():
        return f"A seção **{target}** ainda não tem conteúdo."

    try:
        result = run_critic(content, target, session)
    except Exception as e:
        logger.error("_dispatch_review: run_critic falhou: %s", e)
        return f"**Erro ao revisar:** {e}"

    lines = [f"**Revisão da seção:** {target}"]
    verdict = "✅ Aprovado" if result.approved else "❌ Bloqueado"
    lines.append(f"**Veredito:** {verdict}")
    if result.feedback:
        lines.append(f"**Feedback:** {result.feedback}")
    if result.issues:
        lines.append("**Issues:**")
        for issue in result.issues:
            lines.append(f"- {issue}")
    lines.append("\n*Isso é uma consulta — nada foi alterado.*")
    return "\n".join(lines)
