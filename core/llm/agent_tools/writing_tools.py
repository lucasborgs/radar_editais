"""Tools do agente de WritingSession (Sprint 2 do Cenário B).

Seis tools que substituem o pipeline determinístico atual de
`WritingSession.turn`:

  • search_edital       — RAG sobre chunks do edital e análogos
  • search_library      — RAG sobre a content library do workspace
  • read_section        — lê uma seção já redigida (resolve gap principal:
                          hoje o LLM não vê o que ele próprio escreveu)
  • read_full_proposal  — concat de todas as seções, em ordem do outline
  • save_draft          — substitui parsing da tag <draft> no texto final
  • request_user_info   — pede info ao usuário via canal estruturado
                          (substitui a convenção [COMPLETAR: ...] no texto)

Princípios (vide core/agent_runtime.py):
  • Nunca lança exceção pro loop. Erros viram strings.
  • Mensagem de retorno orienta o modelo sobre o próximo passo.
  • Side effects (save_draft, request_user_info) mutam estado da sessão
    e são persistidos pela WritingSession após o turn fechar.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.llm.agent_runtime import Tool, tool
from core.retrieval.retriever import (
    format_chunks_for_prompt,
    retrieve_chunks,
    retrieve_library_items,
)

if TYPE_CHECKING:
    from core.services.writing_session import WritingSession

logger = logging.getLogger(__name__)


def build_writing_tools(session: WritingSession) -> list[Tool]:
    """Constrói a lista de tools para o agente desta sessão.

    Cada tool é uma closure sobre `session`. Mutações em session
    (save_draft, request_user_info) ficam visíveis após o agente terminar
    — a WritingSession persiste o estado final via update no Postgres.
    """

    @tool
    def search_edital(query: str, k: int = 5) -> str:
        """Busca dados da oportunidade-alvo.

        Em proposta de edital: trechos relevantes do edital atual e dos análogos
        (requisitos, prazos, mecanismo, TRL, contrapartida, elegibilidade, valores).
        Em pitch de captação: os dados do FUNDO-ALVO (tese, temas, setores, estágio,
        ticket, portfólio) — para ancorar o fit. Aceita PT-BR; frases curtas funcionam
        melhor que parágrafos.

        NÃO use para ler a proposta/pitch que o usuário está escrevendo (use
        read_section ou read_full_proposal para isso).
        """
        # Pitch (entidade): o substrato é o nó do fundo, não chunks de edital.
        if getattr(session, "mode", "proposal") == "pitch":
            return session._pitch_target_context or (
                "Nenhum dado do fundo-alvo disponível. Prossiga com o perfil da startup."
            )
        try:
            chunks = retrieve_chunks(
                session._db,
                session._scope_edital_ids,
                query=query,
                k=k,
            )
            if not chunks:
                return (
                    "Nenhum trecho relevante encontrado para essa query. "
                    "Tente reformular ou prossiga com o que já tem."
                )
            return format_chunks_for_prompt(
                chunks, edital_ids=session._scope_edital_ids,
            )
        except Exception as e:
            logger.warning("[%s] search_edital falhou: %s", session.session_id, e)
            return (
                f"Erro ao buscar no edital: {e}. "
                "Continue com o que sabe do perfil e do histórico."
            )

    @tool
    def search_library(query: str, k: int = 3) -> str:
        """Busca itens da biblioteca de conteúdo da empresa: propostas
        anteriores, descrições de produtos, casos de uso, dados técnicos.

        Use quando precisar de informação concreta sobre a empresa que NÃO
        está no perfil estrutural — por exemplo, métricas de um produto,
        descrição detalhada de uma metodologia já usada, ou trecho narrativo
        de uma proposta anterior.
        """
        try:
            items = retrieve_library_items(
                session.workspace_id, query=query, k=k,
            )
            if not items:
                return "Biblioteca sem matches relevantes para essa query."

            parts: list[str] = []
            for item in items:
                type_ = (item.get("type") or "doc").upper()
                parts.append(f"\n[{type_}] {item.get('title', '(sem título)')}")
                if item.get("summary"):
                    parts.append(f"  {item['summary']}")
                for fact in (item.get("key_facts") or [])[:4]:
                    parts.append(f"  • {fact}")
            return "\n".join(parts)
        except Exception as e:
            logger.warning("[%s] search_library falhou: %s", session.session_id, e)
            return f"Erro ao buscar na biblioteca: {e}."

    @tool
    def read_section(title: str) -> str:
        """Lê o conteúdo já redigido de uma seção da proposta.

        Use antes de reescrever uma seção (para não duplicar) ou para verificar
        coerência com o que já foi escrito. Use o título EXATO da seção como
        aparece no outline.
        """
        # Lookup tolerante: tenta exato, depois case-insensitive.
        content = session._doc_sections.get(title)
        if content is None:
            for t, c in session._doc_sections.items():
                if t.lower() == title.lower():
                    content = c
                    break

        if content is None or not content.strip():
            outline_titles = ", ".join(
                f"'{t}'" for t in session._proposal_outline[:8]
            )
            return (
                f"Seção '{title}' está vazia ou não existe no outline. "
                f"Seções disponíveis: {outline_titles}..."
            )
        return f"Seção '{title}':\n\n{content}"

    @tool
    def read_full_proposal() -> str:
        """Lê a proposta inteira (todas as seções já redigidas) na ordem
        do outline.

        Use antes de redigir conclusão, sumário executivo, ou quando precisar
        avaliar coerência global. Caro de tokens — prefira read_section se
        souber exatamente qual seção precisa.
        """
        outline = session._proposal_outline
        if not outline:
            return "Outline da proposta ainda não foi definido."

        parts: list[str] = []
        any_content = False
        for title in outline:
            content = session._doc_sections.get(title, "")
            if content.strip():
                any_content = True
                parts.append(f"## {title}\n\n{content}")
            else:
                parts.append(f"## {title}\n\n*[A preencher]*")

        if not any_content:
            return "Proposta ainda vazia — nenhuma seção foi redigida."
        return "\n\n---\n\n".join(parts)

    @tool
    def plan_writing_session(focus: str = "") -> str:
        """Gera um plano de trabalho estratégico para esta sessão de escrita.

        Analisa o estado atual da proposta (quais seções estão vazias, em
        rascunho ou completas) e sugere a ordem mais estratégica para trabalhar,
        com justificativa para cada prioridade.

        Use no início de uma sessão, ou quando o usuário pedir orientação sobre
        por onde começar ou em que focar.

        Args:
            focus: objetivo específico desta sessão, se houver
                   (ex: "terminar a parte técnica", "revisar orçamento").
                   Deixe vazio para plano geral.
        """
        import os

        from core.llm.llm_client import make_client

        outline = session._proposal_outline
        if not outline:
            return "Proposta sem outline definido — peça ao usuário que defina as seções primeiro."

        status_lines = []
        for title in outline:
            content = session._doc_sections.get(title, "")
            wc = len(content.split()) if content.strip() else 0
            if wc == 0:
                st = "vazia"
            elif wc < 80:
                st = f"rascunho inicial ({wc} palavras)"
            else:
                st = f"redigida ({wc} palavras)"
            status_lines.append(f"• {title}: {st}")

        sections_block = "\n".join(status_lines)
        focus_line = f"\nOBJETIVO DESTA SESSÃO: {focus.strip()}" if focus.strip() else ""

        system = (
            "Você é um consultor de captação de recursos especializado em estratégia "
            "de escrita de propostas para editais de fomento no Brasil. "
            "Seja direto, prático e acionável."
        )
        user = (
            f"ESTADO ATUAL DA PROPOSTA:\n{sections_block}\n{focus_line}\n\n"
            "Sugira a ordem estratégica para trabalhar as seções nesta sessão. "
            "Para cada seção priorizada, inclua 1 linha de justificativa. "
            "Priorize seções que desbloqueiam outras ou têm maior impacto na aprovação."
        )

        try:
            client = make_client(api_key=os.environ["OPENAI_API_KEY"])
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                max_tokens=600,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("[%s] plan_writing_session: LLM falhou: %s", session.session_id, e)
            return (
                "Plano sem IA (use como ponto de partida):\n"
                + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(outline))
            )

    @tool
    def save_draft(section_title: str, content: str, force: bool = False) -> str:
        """Salva um rascunho completo de uma seção da proposta.

        Por padrão, passa por revisão automática (critic) antes de salvar.
        Se o critic encontrar problemas, descreve os problemas sem salvar —
        corrija e chame save_draft novamente, ou use force=True para salvar
        ignorando a revisão (decisão explícita do usuário).

        Use APENAS quando o conteúdo está fechado e pronto para persistir.
        Use o título EXATO da seção (do outline).

        Args:
            section_title: título exato da seção conforme o outline
            content: markdown bem formatado, pronto para persistir
            force: True para salvar sem revisão do critic
        """
        if not content.strip():
            return (
                "Erro: content vazio. save_draft só persiste rascunhos com texto. "
                "Se quer limpar a seção, peça confirmação ao usuário primeiro."
            )

        # Validação do título antes de chamar o critic (evita custo desnecessário).
        target_title = section_title
        if target_title not in session._proposal_outline:
            match = next(
                (t for t in session._proposal_outline
                 if t.lower() == target_title.lower()),
                None,
            )
            if match:
                target_title = match
            else:
                outline_str = ", ".join(
                    f"'{t}'" for t in session._proposal_outline[:8]
                )
                return (
                    f"Erro: seção '{section_title}' não está no outline. "
                    f"Use uma dessas: {outline_str}..."
                )

        # Critic review — só pula se force=True (decisão explícita do usuário).
        if not force:
            from core.llm.agent_tools.critic_agent import run_critic
            critic = run_critic(content, target_title, session)
            if not critic.approved:
                issues_str = "\n".join(f"• {issue}" for issue in critic.issues)
                return (
                    f"Critic encontrou {len(critic.issues)} problema(s) antes de salvar:\n"
                    f"{issues_str}\n\n"
                    f"Diagnóstico: {critic.feedback}\n\n"
                    "Revise o rascunho e tente save_draft novamente, ou chame "
                    "save_draft com force=True para salvar mesmo assim."
                )

        try:
            session.set_section_content(target_title, content)
            suffix = "" if force else " (aprovado pelo critic)"
            return (
                f"Rascunho salvo em '{target_title}' ({len(content)} chars){suffix}. "
                "Continue a conversa ou prossiga para a próxima seção."
            )
        except Exception as e:
            logger.warning("[%s] save_draft falhou: %s", session.session_id, e)
            return f"Erro ao salvar rascunho: {e}"

    @tool
    def recall_company_learnings(topic: str = "") -> str:
        """Consulta aprendizados estratégicos de propostas anteriores desta empresa.

        Retorna observações e padrões sintetizados a partir de aplicações
        passadas (aprovadas, reprovadas, submetidas). Use quando:
        - O usuário perguntar sobre histórico ou experiência da empresa
        - Precisar de contexto estratégico além do perfil estrutural
        - Quiser saber se uma abordagem já foi testada com sucesso ou falhou

        Args:
            topic: tema de interesse (ex: 'TRL', 'contrapartida', 'orçamento').
                   Deixe vazio para todos os aprendizados disponíveis.
        """
        from core.reflection_service import search_insights_for_tool
        return search_insights_for_tool(session._db, session.workspace_id)

    @tool
    def request_user_info(field: str, prompt: str) -> str:
        """Pede ao usuário uma informação que falta e é necessária para
        redigir com precisão (ex: CNPJ, valor de contrapartida, nome do
        coordenador, TRL específico de um subprojeto).

        O pedido vai aparecer no UI como prompt destacado. Você PODE continuar
        redigindo o que conseguir nesse turn e usar [COMPLETAR: ...] como
        placeholder onde a info iria — o usuário responde no próximo turn.

        Use APENAS para info que não dá pra inferir do perfil, library, edital
        ou contexto da conversa. Se a info pode ser estimada, redija com a
        estimativa em vez de pedir.

        Args:
            field: nome curto kebab-case (ex: 'cnpj', 'valor-contrapartida')
            prompt: pergunta clara em PT-BR, 1 frase
        """
        if not field or not prompt:
            return "Erro: field e prompt são obrigatórios e não-vazios."

        session._pending_user_input = {"field": field, "prompt": prompt}
        return (
            f"Pergunta encaminhada ao usuário (campo '{field}'). "
            "Continue redigindo com [COMPLETAR: ...] como placeholder se for útil."
        )

    # DeepResearch (Fase A): tool stateless de busca web. Subagente-como-tool —
    # devolve fato COM fonte; NÃO persiste (gate humano → library é a Fase B).
    from core.llm.agent_tools.research_tools import build_research_tools

    return [
        plan_writing_session,
        search_edital,
        search_library,
        read_section,
        read_full_proposal,
        save_draft,
        request_user_info,
        recall_company_learnings,
        *build_research_tools(),
    ]
