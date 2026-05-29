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

from core.agent_runtime import Tool, tool
from core.retriever import (
    format_chunks_for_prompt,
    retrieve_chunks,
    retrieve_library_items,
)

if TYPE_CHECKING:
    from core.writing_session import WritingSession

logger = logging.getLogger(__name__)


def build_writing_tools(session: WritingSession) -> list[Tool]:
    """Constrói a lista de tools para o agente desta sessão.

    Cada tool é uma closure sobre `session`. Mutações em session
    (save_draft, request_user_info) ficam visíveis após o agente terminar
    — a WritingSession persiste o estado final via update no Postgres.
    """

    @tool
    def search_edital(query: str, k: int = 5) -> str:
        """Busca trechos relevantes do edital atual e dos editais análogos.

        Use para perguntas sobre requisitos, prazos, mecanismo de financiamento,
        TRL aceito, contrapartida, elegibilidade, valores. Aceita PT-BR; frases
        curtas funcionam melhor que parágrafos.

        NÃO use para ler a proposta que o usuário está escrevendo (use
        read_section ou read_full_proposal para isso).
        """
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
    def save_draft(section_title: str, content: str) -> str:
        """Salva um rascunho completo de uma seção da proposta.

        Use APENAS quando o conteúdo está fechado e pronto para persistir —
        não use para sketches, listas de bullets exploratórias, ou pedaços
        parciais. Use o título EXATO da seção (do outline).

        O conteúdo deve ser markdown bem formatado. Substituirá qualquer
        rascunho anterior dessa seção.
        """
        if not content.strip():
            return (
                "Erro: content vazio. save_draft só persiste rascunhos com texto. "
                "Se quer limpar a seção, peça confirmação ao usuário primeiro."
            )

        # Validação leve: o título precisa estar no outline. Se não, tenta
        # encontrar match case-insensitive antes de aceitar.
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

        try:
            session.set_section_content(target_title, content)
            return (
                f"Rascunho salvo em '{target_title}' ({len(content)} chars). "
                "Continue a conversa ou prossiga para a próxima seção."
            )
        except Exception as e:
            logger.warning("[%s] save_draft falhou: %s", session.session_id, e)
            return f"Erro ao salvar rascunho: {e}"

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

    return [
        search_edital,
        search_library,
        read_section,
        read_full_proposal,
        save_draft,
        request_user_info,
    ]
