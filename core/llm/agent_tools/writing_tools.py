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
import re
import unicodedata
from typing import TYPE_CHECKING, Annotated

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState

from core.llm.agent_runtime import _cap
from core.retrieval.retriever import retrieve_chunks, retrieve_library_items

if TYPE_CHECKING:
    from core.services.writing_session import WritingSession

logger = logging.getLogger(__name__)

# Caps por-tool (spec 02): complementares ao cap central em agent_runtime.
# Semânticos — evitam que read_full_proposal/search_edital inflem o histórico
# antes mesmo do teto global. Defaults folgados; calibrar com o log de disparo.
# Cap total de read_full_proposal: proposta inteira é cara; acima disso, o aviso
# orienta o modelo a usar read_section para detalhe pontual.
READ_FULL_PROPOSAL_CHAR_CAP = int(os.getenv("READ_FULL_PROPOSAL_CHAR_CAP", "8000"))
SEARCH_EDITAL_CHAR_CAP = int(os.getenv("SEARCH_EDITAL_CHAR_CAP", "8000"))

# Termos genéricos demais para ancorar o snippet (perguntas em PT-BR sobre o
# edital tendem a repeti-los) — sem isso o "hit" mais cedo no texto costuma
# ser um desses em vez do termo que importa.
_SNIPPET_STOPWORDS = {
    "que", "para", "sobre", "como", "quais", "qual", "uma", "umas", "uns",
    "com", "sem", "por", "dos", "das", "dele", "dela", "esse", "essa", "este",
    "esta", "isso", "aborda", "fala", "trata", "secao", "edital", "sao",
}


def _fold(text: str) -> str:
    """Remove acentos e normaliza caixa preservando o índice 1:1 com o original
    (NFKD decompõe cada char acentuado em base+combining mark; ao descartar só
    as combining marks, o comprimento da string não muda)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _snippet_around_query(txt: str, query: str, *, window: int = 280, lead: int = 60) -> str:
    """Recorta o preview do chunk centrado no primeiro termo da query encontrado.

    search_edital só mostra um resumo por chunk (o texto denso vai para
    read_exact_chunk) — um corte cru no prefixo esconde o trecho relevante
    sempre que ele não abre o chunk, o que é comum em chunks longos que
    cobrem vários itens numerados do edital. Sem termo encontrado, cai no
    corte de prefixo de sempre.
    """
    if len(txt) <= window:
        return txt

    folded = _fold(txt)
    terms = [
        w for w in re.findall(r"\w+", _fold(query))
        if len(w) >= 4 and w not in _SNIPPET_STOPWORDS
    ]
    hit = -1
    for term in terms:
        idx = folded.find(term)
        if idx != -1 and (hit == -1 or idx < hit):
            hit = idx

    if hit == -1:
        return txt[:window] + "…"

    start = max(0, hit - lead)
    end = min(len(txt), start + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(txt) else ""
    return f"{prefix}{txt[start:end]}{suffix}"


def build_writing_tools(session: WritingSession) -> list[BaseTool]:
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
            from core.kg import entity_catalog
            card = entity_catalog.get_edital(session.edital_id)
            if card:
                _skill_mechanism = str(card.get("mechanism", "") or "")
        except Exception as e:
            logger.debug("load_skill: falha ao resolver mechanism de %s: %s",
                         getattr(session, "edital_id", "?"), e)

    @tool
    def search_edital(
        query: str,
        k: int = 5,
        documents: Annotated[dict[str, str] | None, InjectedState("documents")] = None,
    ) -> str:
        """Busca dados da oportunidade-alvo.

        Em proposta de edital: trechos relevantes do edital atual e dos análogos
        (requisitos, prazos, mecanismo, TRL, contrapartida, elegibilidade, valores).
        Em pitch de captação: os dados do FUNDO-ALVO (tese, temas, setores, estágio,
        ticket, portfólio) — para ancorar o fit. Aceita PT-BR; frases curtas funcionam
        melhor que parágrafos.

        NÃO use para ler a proposta/pitch que o usuário está escrevendo (use
        read_section ou read_full_proposal para isso).

        Retorna APENAS um resumo de cada trecho com chunk_id. Para ler o texto
        completo, use a ferramenta read_exact_chunk com o chunk_id exibido.
        """
        # Pitch (entidade): o substrato é o nó do fundo, não chunks de edital.
        if getattr(session, "mode", "proposal") == "pitch":
            return session._source_card_context or (
                "Nenhum dado do fundo-alvo disponível. Prossiga com o perfil da startup."
            )

        _docs = documents if documents is not None else {}

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

            # Salva o texto completo no state["documents"] e constrói o sumário
            # com ponteiros — o texto denso não vai para o ToolMessage.
            primary = session._scope_edital_ids[0] if session._scope_edital_ids else None
            pointer_parts: list[str] = []
            for c in chunks:
                chunk_id = str(c.get("id", ""))
                txt = c.get("text", "")
                if chunk_id and txt:
                    _docs[chunk_id] = txt

                section = c.get("section") or "sem seção"
                source = c.get("source_file") or "fonte desconhecida"
                score = c.get("score") or 0.0
                chunk_edital = c.get("edital_id")
                is_analogue = primary is not None and chunk_edital and chunk_edital != primary

                label = f"Análogo {chunk_edital} — {section}" if is_analogue else section
                snippet = _snippet_around_query(txt, query)
                pointer_parts.append(
                    f"[{chunk_id}] {label} ({source}, score={score:.3f})\n"
                    f"  {snippet}"
                )

            summary = (
                f"Foram encontrados {len(chunks)} trecho(s) relevante(s). "
                f"Para ler o texto COMPLETO de qualquer trecho, "
                f"use a ferramenta read_exact_chunk passando o chunk_id exato.\n\n"
                "<dados_externos>\n"
                + "\n\n".join(pointer_parts)
                + "\n</dados_externos>"
            )
            return _cap(
                summary, SEARCH_EDITAL_CHAR_CAP, tool_name="search_edital",
            )
        except Exception as e:
            logger.warning("[%s] search_edital falhou: %s", session.session_id, e)
            return (
                f"Erro ao buscar no edital: {e}. "
                "Continue com o que sabe do perfil e do histórico."
            )

    @tool
    def read_exact_chunk(
        chunk_id: str,
        documents: Annotated[dict[str, str] | None, InjectedState("documents")] = None,
    ) -> str:
        """Lê o texto COMPLETO de um trecho do edital previamente referenciado.

        Use APENAS quando precisar do texto literal e completo de um chunk cujo
        resumo foi exibido por search_edital. Recebe o `chunk_id` exato mostrado
        no resultado da busca — não invente ou modifique o ID.

        Args:
            chunk_id: identificador exato exibido entre colchetes no resultado
                      de search_edital, ex.: "abc123-def456"
        """
        _docs = documents if documents is not None else {}
        content = _docs.get(chunk_id)
        if content is None:
            return (
                f"Chunk '{chunk_id}' não encontrado no cache da sessão. "
                "Faça uma nova busca com search_edital para obter referências "
                "atualizadas."
            )
        return f"<dados_externos>\n{content}\n</dados_externos>"

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

        Use APENAS quando o conteúdo está fechado e pronto para persistir.
        Use o título EXATO da seção (do outline).

        Por padrão passa pelo critic automático. force=True ignora o critic
        (útil em geração em lote ou bypass explícito).

        Args:
            section_title: título exato da seção conforme o outline
            content: markdown bem formatado, pronto para persistir
            force: True para salvar sem revisão do critic
        """
        # Helper local: todo caminho de retorno anexa exatamente um entry em
        # _tool_results, mantendo a invariante 1:1 com _consume_save_draft_result
        # (F3, B1). Early-returns de validação e erro usam sentinela (None/None).
        def _r(entry_title, entry_verdict, text):
            session._tool_results.append({
                "section_title": entry_title,
                "critic_result": entry_verdict,
            })
            return text

        if not content.strip():
            return _r(
                None, None,
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
                return _r(
                    None, None,
                    f"Erro: seção '{section_title}' não está no outline. "
                    f"Use uma dessas: {outline_str}..."
                )

        # Critic review — só pula se force=True (decisão explícita do usuário).
        critic_verdict = None
        if not force:
            from core import telemetry
            from core.llm.agent_tools.critic_agent import run_critic
            # Captura o contexto Langfuse antes de chamar o critic: o contextvar OTel
            # não é garantido no thread pool do LangGraph para tools síncronas.
            _trace_ctx = telemetry.current_trace_context()
            critic = run_critic(content, target_title, session, trace_context=_trace_ctx)
            critic_verdict = {
                "approved": critic.approved,
                "issues": critic.issues,
                "feedback": critic.feedback,
            }
            if critic.fail_open:
                session._critic_fail_open_count += 1
            if not critic.approved:
                session._critic_block_count += 1
                issues_str = "\n".join(f"• {issue}" for issue in critic.issues)
                return _r(
                    target_title, critic_verdict,
                    f"Critic encontrou {len(critic.issues)} problema(s) antes de salvar:\n"
                    f"{issues_str}\n\n"
                    f"Diagnóstico: {critic.feedback}\n\n"
                    "Revise o rascunho e tente save_draft novamente, ou chame "
                    "save_draft com force=True para salvar mesmo assim."
                )
            session._critic_pass_count += 1

        # Captura conteúdo ANTIGO antes de sobrescrever (para o scope classifier)
        old_content = session._doc_sections.get(target_title, "")

        try:
            session.set_section_content(target_title, content)
        except Exception as e:
            logger.warning("[%s] save_draft falhou: %s", session.session_id, e)
            return _r(target_title, critic_verdict, f"Erro ao salvar rascunho: {e}")

        suffix = "" if force else " (aprovado pelo critic)"

        # Scope classifier: best-effort (heurística de ripple), NÃO deve mascarar
        # um save que já teve sucesso — se falhar (ex.: rede), loga e segue.
        if not force and not getattr(session, '_ripple_active', False):
            try:
                from core.llm.agent_tools.scope_classifier import classify_correction_scope
                scope = classify_correction_scope(old_content, content, target_title, session)
                if scope and scope.get("type") == "conceptual":
                    session._ripple_suggestion = {
                        "source_section": target_title,
                        "affected_sections": scope.get("changed_elements", []),
                    }
            except Exception as e:
                logger.warning(
                    "[%s] scope classifier falhou (save já persistido): %s",
                    session.session_id, e,
                )

        return _r(
            target_title, critic_verdict,
            f"Rascunho salvo em '{target_title}' ({len(content)} chars){suffix}. "
            "Continue a conversa ou prossiga para a próxima seção."
        )

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
        return search_insights_for_tool(session._db, session.workspace_id, query=topic)

    @tool
    def ask_about_edital(question: str) -> str:
        """Tira uma dúvida pontual sobre o edital sem sair do contexto de escrita.

        Use quando você precisa de um esclarecimento rápido sobre o edital (ex:
        'qual o prazo?', 'o que o edital diz sobre contrapartida?', 'são
        elegíveis empresas de qual porte?'). Esta tool consulta o edital via
        um agente exploratório e devolve uma resposta concisa (~200 palavras).

        NÃO use para buscar chunks específicos (use search_edital) ou para
        ler seções já escritas (use read_section). Use APENAS para perguntas
        que precisam de uma visão integrada do edital.

        Args:
            question: pergunta clara em PT-BR sobre o edital
        """
        if not question.strip():
            return "Erro: pergunta vazia."
        try:
            from core.services.explore_agent import ExploreAgent
            agent = ExploreAgent()
            edital_id = getattr(session, "edital_id", None)
            answer = agent.explore(
                message=question,
                edital_ids=[edital_id] if edital_id else None,
                has_profile=False,
            )
            return answer or "Não consegui esclarecer essa dúvida agora."
        except Exception as e:
            logger.debug("ask_about_edital: erro ao esclarecer: %s", e)
            return "Não consegui esclarecer essa dúvida agora. Tente novamente mais tarde."

    @tool
    def request_user_info(field: str, prompt: str) -> str:
        """Pede ao usuário uma informação que falta e é necessária para
        redigir com precisão (ex: CNPJ, valor de contrapartida, nome do
        coordenador, TRL específico de um subprojeto).

        IMPORTANTE: esta tool PAUSA você imediatamente — o turno congela, a
        pergunta vai ao usuário, e você só CONTINUA quando ele responder (a
        resposta dele volta como o resultado desta tool, no mesmo raciocínio).
        Por isso: faça as buscas e escreva o que já consegue ANTES de chamar; e
        chame request_user_info SOZINHA (não no mesmo passo que save_draft/
        search_edital), senão essas ações repetem ao retomar.

        Use APENAS para info que não dá pra inferir do perfil, library, edital
        ou contexto da conversa. Se a info pode ser estimada, redija com a
        estimativa em vez de pedir.

        Args:
            field: nome curto kebab-case (ex: 'cnpj', 'valor-contrapartida')
            prompt: pergunta clara em PT-BR, 1 frase
        """
        if not field or not prompt:
            return "Erro: field e prompt são obrigatórios e não-vazios."

        # interrupt() congela o grafo e devolve {field, prompt} ao caller (a
        # WritingSession surfaça como pending_user_input). Ao retomar via
        # Command(resume=<resposta>), interrupt() RETORNA a resposta aqui — que
        # vira o tool-result que você usa para redigir. Requer grafo compilado
        # com checkpointer (run_writing_turn). Etapa 3 da migração LangGraph.
        from langgraph.types import interrupt

        answer = interrupt({"field": field, "prompt": prompt})
        return f"O usuário respondeu (campo '{field}'): {answer}"

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

    from core.llm.agent_tools.match_tools import build_match_tools
    from core.llm.agent_tools.planning_tools import PlanState, build_planning_tools
    from core.llm.agent_tools.research_tools import build_research_tools

    # Investidor/programa por afinidade ao perfil (hardening 2026-07-02): mesmo
    # motor de match do ExploreAgent (match_tools.py → match_v3). Só
    # find_matching_entities — find_matching_editais fica de fora porque
    # descobrir OUTROS editais é fora de escopo de uma sessão já ancorada num
    # edital (arrisca desviar o agente no meio do rascunho). Com workspace
    # autenticado o lado empresa vem de company_chunks (perfil + library);
    # getattr defensivo para sessões fake/sem auth (mesmo padrão de
    # build_research_tools abaixo).
    match_tools: list[BaseTool] = []
    profile_text = getattr(session, "_profile_context", None)
    profile_obj = getattr(session, "profile", None)
    if profile_text and profile_obj is not None:
        match_tools = [
            t for t in build_match_tools(
                profile_text,
                profile=profile_obj,
                workspace_id=getattr(session, "workspace_id", None),
                db=getattr(session, "_db", None),
            )
            if t.name == "find_matching_entities"
        ]

    return [
        search_edital,
        read_exact_chunk,
        load_skill,
        search_library,
        read_section,
        read_full_proposal,
        save_draft,
        request_user_info,
        recall_company_learnings,
        ask_about_edital,
        *match_tools,
        # Fase B (Item 2): com session, deep_research persiste cada finding em
        # research_findings (verified=false) para o gate humano de promoção.
        # getattr defensivo: sessões sem workspace_id/_db (fakes, contextos sem
        # auth) caem no modo stateless da Fase A em vez de quebrar.
        *build_research_tools(
            workspace_id=getattr(session, "workspace_id", None),
            db=getattr(session, "_db", None),
        ),
        *build_planning_tools(PlanState()),
    ]
