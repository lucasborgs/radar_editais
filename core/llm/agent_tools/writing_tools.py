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
import os
from typing import TYPE_CHECKING

from core.llm.agent_runtime import Tool, _cap, tool
from core.retrieval.retriever import (
    format_chunks_for_prompt,
    retrieve_chunks,
    retrieve_library_items,
)

if TYPE_CHECKING:
    from core.services.writing_session import WritingSession

logger = logging.getLogger(__name__)

# Caps por-tool (spec 02): complementares ao cap central em agent_runtime.
# Semânticos — evitam que read_full_proposal/search_edital inflem o histórico
# antes mesmo do teto global. Defaults folgados; calibrar com o log de disparo.
# Cap total de read_full_proposal: proposta inteira é cara; acima disso, o aviso
# orienta o modelo a usar read_section para detalhe pontual.
READ_FULL_PROPOSAL_CHAR_CAP = int(os.getenv("READ_FULL_PROPOSAL_CHAR_CAP", "8000"))
# Cap por chunk em search_edital: cada trecho do edital é truncado antes de
# concatenar (k chunks somam rápido). Cap total da tool-result fica no env abaixo.
SEARCH_EDITAL_CHUNK_CHAR_CAP = int(os.getenv("SEARCH_EDITAL_CHUNK_CHAR_CAP", "1500"))
SEARCH_EDITAL_CHAR_CAP = int(os.getenv("SEARCH_EDITAL_CHAR_CAP", "8000"))


def build_writing_tools(session: WritingSession) -> list[Tool]:
    """Constrói a lista de tools para o agente desta sessão.

    Cada tool é uma closure sobre `session`. Mutações em session
    (save_draft, request_user_info) ficam visíveis após o agente terminar
    — a WritingSession persiste o estado final via update no Postgres.
    """

    # mechanism (+ source) do edital → habilita a tool load_skill (spec 05): o
    # Redator PUXA o playbook de escrita do instrumento sob demanda (lente + padrões
    # de tom/estrutura), em vez de regra dura — que vem de search_edital (RAG).
    # Resolve uma vez por sessão. Pitch (nó do fundo, sem edital) → mechanism=equity.
    _skill_mechanism = ""
    _skill_source = ""
    if getattr(session, "mode", "proposal") == "pitch":
        _skill_mechanism = "equity"  # gênero outbound roteado ao agente de pitch (D4)
    else:
        # Agência (overlay de fonte) = prefixo do edital_id; o campo `source` da wiki
        # é proveniência de ingestão (etl_process/web), não a agência.
        try:
            from core.kg.edital_id import source_of
            _skill_source = source_of(session.edital_id)
        except Exception:
            _skill_source = ""
        try:
            from core.kg import kg_store
            _wiki = kg_store.load_wiki_page(session.edital_id)
            if _wiki:
                _skill_mechanism = str(_wiki.get("mechanism", "") or "")
        except Exception as e:  # nunca quebra a construção do toolset
            logger.debug("load_skill: falha ao resolver mechanism de %s: %s",
                         getattr(session, "edital_id", "?"), e)

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
            # Cap por chunk: cada trecho é truncado antes da concatenação
            # (k chunks inteiros somam rápido). Cap total na sequência.
            for c in chunks:
                txt = c.get("text", "")
                if txt:
                    c["text"] = _cap(
                        txt, SEARCH_EDITAL_CHUNK_CHAR_CAP,
                        tool_name="search_edital[chunk]",
                    )
            formatted = format_chunks_for_prompt(
                chunks, edital_ids=session._scope_edital_ids,
            )
            return _cap(
                formatted, SEARCH_EDITAL_CHAR_CAP, tool_name="search_edital",
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
        full = "\n\n---\n\n".join(parts)
        # Cap total: a proposta inteira é cara em tokens. Se estourar, trunca e
        # avisa o modelo a usar read_section para o detalhe pontual que precisar.
        if len(full) > READ_FULL_PROPOSAL_CHAR_CAP:
            full = _cap(
                full, READ_FULL_PROPOSAL_CHAR_CAP, tool_name="read_full_proposal",
            )
            full += (
                "\n\nAVISO: proposta truncada por exceder o orçamento de contexto. "
                "Use read_section(title) para ler o conteúdo completo de uma seção "
                "específica quando precisar de detalhe."
            )
        return full

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
    # write_todos: PlanState interno por chamada (= por turno). É o ÚNICO
    # mecanismo de planejamento do Redator (spec 04 removeu plan_writing_session,
    # que duplicava com uma chamada LLM extra). Persistência cross-turn dos todos
    # fica fora de escopo (ver spec).
    @tool
    def load_skill() -> str:
        """Carrega o PLAYBOOK DE ESCRITA deste instrumento: a lente do avaliador e
        os padrões de tom/estrutura que aprovam neste mecanismo (+ praxe da fonte).

        Use antes de redigir, para escrever como um especialista naquele
        instrumento. NÃO traz regra dura (prazo, contrapartida %, rubricas, TRL
        exigido) — isso vem de search_edital (edital). Puxa só quando a seção pede.
        """
        from core.skills import load_playbook
        playbook = load_playbook(_skill_mechanism, _skill_source)
        content = playbook.for_writer()
        if not content.strip():
            return (
                "Sem playbook de escrita específico para este instrumento; "
                "siga o perfil da empresa e os dados do edital (search_edital)."
            )
        label = playbook.mechanism or "genérico"
        if playbook.source:
            label += f" · {playbook.source}"
        return f"PLAYBOOK DE ESCRITA ({label}):\n{content}"

    from core.llm.agent_tools.planning_tools import PlanState, build_planning_tools
    from core.llm.agent_tools.research_tools import build_research_tools

    return [
        search_edital,
        load_skill,
        search_library,
        read_section,
        read_full_proposal,
        save_draft,
        request_user_info,
        recall_company_learnings,
        *build_research_tools(),
        *build_planning_tools(PlanState()),
    ]
