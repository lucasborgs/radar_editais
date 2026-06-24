"""Critic sub-agente — revisão de rascunho de seção antes de salvar.

Chamado internamente pela tool `save_draft` (writing_tools.py:276) como
`run_critic(content, target_title, session)`. Investiga o substrato relevante
(edital no gênero proposta; nó do fundo-alvo no gênero pitch), o perfil da
empresa e as demais seções, e retorna um CriticResult com approved + lista de
issues específicos.

Era 1-shot (um único retrieve com draft[:500] como query) e NÃO via o CompanyProfile
— não detectava elegibilidade incorreta. Agora é um sub-agente com 3 tools e
max_steps=3, que escolhe a query do retrieve e checa o perfil sob demanda.

Princípios (preservados do 1-shot):
  • Falha graciosa: erro do sub-agente/LLM → CriticResult(approved=True) com nota
    de indisponibilidade. Save NUNCA bloqueia por falha do critic.
  • Só contradição, nunca omissão: bloqueia apenas afirmações que CONTRADIZEM o
    substrato (edital/fundo), outra seção já redigida, o CompanyProfile, ou são
    internamente inconsistentes. Na dúvida, APROVE.

Coerência interna: como toda edição passa por save_draft, a seção sendo salva é
sempre cruzada com todas as outras (checagem bidirecional na prática).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

from core.llm.agent_runtime import run_subagent

logger = logging.getLogger(__name__)

# ── System prompt agêntico — gênero PROPOSTA (mode=proposal) ──
# Parte do _CRITIC_SYSTEM 1-shot (contrato só-contradição/nunca-omissão +
# cuidado com "Análogo"), adaptado ao estilo agêntico: usa tools para
# investigar antes de decidir, em ≤3 passos. Item (d) novo: contradição contra
# o CompanyProfile (elegibilidade/porte/setor) é bloqueio.
_CRITIC_SYSTEM = """Você é um revisor de fatos de propostas para editais de fomento no Brasil.
Sua única função é IMPEDIR que um rascunho seja salvo quando ele AFIRMA algo FALSO —
isto é, quando o texto CONTRADIZ o edital, CONTRADIZ outra seção já redigida da proposta,
CONTRADIZ os dados da empresa (perfil), ou contém inconsistência factual interna.

Você NÃO avalia completude, abrangência, detalhamento, estilo ou se a seção cobre todos
os tópicos desejáveis — isso é trabalho de outra etapa (checklist), não sua. A ausência
de uma informação NUNCA é motivo para bloquear: só o que está ESCRITO e está ERRADO conta.

VOCÊ TEM TOOLS para investigar antes de decidir. Use-as com parcimônia (no máximo 3 passos):
  • read_target_context(query): trechos relevantes do EDITAL para a query que VOCÊ escolher
    (escolha termos do rascunho que precise verificar: prazo, valor, TRL, elegibilidade...).
  • read_company_profile(): o perfil da EMPRESA (porte, setor, elegibilidade, capacidades).
  • read_proposal_sections(): as OUTRAS seções já redigidas da proposta.
Não precisa chamar todas; chame só as necessárias para resolver uma dúvida concreta.

Bloqueie (approved=false) SOMENTE quando o rascunho AFIRMAR:
  (a) um fato que contradiz o edital (prazo, valor, TRL, elegibilidade, mecanismo),
  (b) um fato que contradiz OUTRA seção já redigida da proposta (ex.: número, prazo,
      escopo, tamanho da equipe, orçamento ou objetivo divergente do que já foi escrito),
  (c) algo internamente inconsistente que torne a proposta incorreta, ou
  (d) algo sobre a EMPRESA que CONTRADIZ o perfil (ex.: afirma elegibilidade, porte ou
      setor que a empresa NÃO tem). Isso é contradição factual, não omissão.

Para (b) e (d), só conta CONTRADIÇÃO factual entre o que o rascunho afirma e o substrato —
NUNCA a mera ausência de um tópico. Na dúvida, APROVE. Esta é a regra dominante: prefira
um falso-negativo (deixar passar) a um falso-positivo (travar rascunho legítimo).

ATENÇÃO AOS ANÁLOGOS: os trechos de read_target_context podem incluir referências marcadas
"Análogo <id>". Esses são de OUTROS editais (referência de redação), NÃO do edital sob
revisão. NUNCA bloqueie por divergência entre o rascunho e um trecho "Análogo" — em
especial, não trate o TEMA/escopo de um análogo como se fosse o do edital desta proposta.
Só os trechos SEM o rótulo "Análogo" (o edital primário) fundamentam um bloqueio contra o edital.

Quando terminar, responda com UMA mensagem final contendo APENAS um JSON válido:
{"approved": true ou false,
 "issues": ["descrição objetiva de cada afirmação FALSA/contraditória (indique se o conflito é com o edital, com outra seção ou com o perfil)"],
 "feedback": "diagnóstico geral em 1 frase"}
Se o rascunho não afirma nada falso nem contraditório: approved=true, issues=[]."""

# ── System prompt agêntico — gênero PITCH (mode=pitch, alvo = fundo investidor) ──
# Mesma máquina (só-contradição, nunca-omissão); o substrato da checagem (a) é o
# nó do fundo-alvo (via read_target_context, que em pitch retorna o contexto do
# fundo). Item (d) idêntico — contradição contra o perfil da startup.
_PITCH_CRITIC_SYSTEM = """Você é um revisor de fatos de pitches de captação (venture capital) para startups deep-tech.
Sua única função é IMPEDIR que um rascunho seja salvo quando ele AFIRMA algo FALSO —
isto é, quando o texto CONTRADIZ os dados do fundo-alvo, CONTRADIZ outra seção já redigida
do pitch, CONTRADIZ os dados da startup (perfil), ou contém inconsistência factual interna.

Você NÃO avalia completude, abrangência, persuasão, estilo ou se a seção cobre todos os
tópicos desejáveis. A ausência de uma informação NUNCA é motivo para bloquear: só o que
está ESCRITO e está ERRADO conta.

VOCÊ TEM TOOLS para investigar antes de decidir. Use-as com parcimônia (no máximo 3 passos):
  • read_target_context(query): os dados do FUNDO-ALVO (tese, temas, setores, estágio,
    ticket, lead/follow). A query é informativa; o contexto do fundo é retornado integral.
  • read_company_profile(): o perfil da STARTUP (estágio, setor, capacidades).
  • read_proposal_sections(): as OUTRAS seções já redigidas do pitch.
Não precisa chamar todas; chame só as necessárias para resolver uma dúvida concreta.

Bloqueie (approved=false) SOMENTE quando o rascunho AFIRMAR:
  (a) algo sobre o FUNDO-ALVO que contradiz seus dados (tese, temas, setores, estágio alvo,
      ticket, lead/follow) — ex.: dizer que o fundo investe em série B quando o estágio é seed,
  (b) um fato que contradiz OUTRA seção já redigida do pitch (ex.: TAM, tração, tamanho do
      time, ask/round divergente do que já foi escrito),
  (c) algo internamente inconsistente que torne o pitch incorreto, ou
  (d) algo sobre a STARTUP que CONTRADIZ o perfil (ex.: estágio ou setor que ela NÃO tem).
      Isso é contradição factual, não omissão.

Você NÃO valida fatos externos (não sabe o MRR/tração real da startup) — só contradições
contra o que está no contexto. Na dúvida, APROVE. Esta é a regra dominante: prefira um
falso-negativo a um falso-positivo.

Quando terminar, responda com UMA mensagem final contendo APENAS um JSON válido:
{"approved": true ou false,
 "issues": ["descrição objetiva de cada afirmação FALSA/contraditória (indique se o conflito é com o fundo, com outra seção ou com o perfil)"],
 "feedback": "diagnóstico geral em 1 frase"}
Se o rascunho não afirma nada falso nem contraditório: approved=true, issues=[]."""

# Backstop de chars para o rascunho no prompt do Critic.
# 3 000 era o cap anterior — deixava metade das páginas fora da revisão.
# 30 000 cobre propostas longas (típicas ~8-12k chars) com margem de segurança.
_CRITIC_DRAFT_CHAR_CAP = int(os.getenv("CRITIC_DRAFT_CHAR_CAP", "30000"))

# Tarefa enviada ao sub-agente (user_message). O rascunho vai inteiro até o backstop;
# o resto do substrato chega via tools.
_CRITIC_TASK = """SEÇÃO SENDO SALVA: {section_title}

RASCUNHO A REVISAR:
{draft}

Investigue (usando as tools quando precisar verificar uma afirmação concreta) e decida
se o rascunho afirma algo FALSO ou contraditório. Lembre: só contradição bloqueia, nunca
omissão; na dúvida, aprove. Termine com o JSON do veredito."""

# Orçamento de chars para o contexto das outras seções no prompt do critic.
_PROPOSAL_CTX_BUDGET = 6000


def _build_proposal_context(session, current_title: str, budget: int = _PROPOSAL_CTX_BUDGET) -> str:
    """Concatena as outras seções já redigidas (exclui a que está sendo salva).

    Ordem do outline; cai para ordem de inserção se não houver outline. Trunca
    no orçamento de chars. Defensivo: se a sessão não expõe seções/outline,
    devolve aviso de "sem outras seções" em vez de quebrar.
    """
    outline = getattr(session, "_proposal_outline", None) or []
    sections = getattr(session, "_doc_sections", None) or {}
    titles = outline if outline else list(sections.keys())
    cur_norm = (current_title or "").strip().lower()

    parts: list[str] = []
    used = 0
    for title in titles:
        if (title or "").strip().lower() == cur_norm:
            continue  # exclui a própria seção sendo salva
        content = (sections.get(title) or "").strip()
        if not content:
            continue
        block = f"## {title}\n{content}"
        if used + len(block) > budget:
            block = block[: max(0, budget - used)]
        parts.append(block)
        used += len(block)
        if used >= budget:
            break

    if not parts:
        return "Nenhuma outra seção foi redigida ainda — não há o que cruzar."
    return "\n\n".join(parts)


@dataclass
class CriticResult:
    approved: bool
    issues: list[str] = field(default_factory=list)
    feedback: str = ""


def build_critic_tools(session, section_title: str):
    """Constrói as 3 tools do sub-agente critic, com closure sobre a sessão e
    o título da seção sendo salva.

    As tools são substrato-agnósticas no contrato (mesma assinatura nos dois
    gêneros); o que muda é o que `read_target_context` resolve internamente.
    Toda tool falha graciosamente: erro → string de "indisponível", nunca
    propaga exceção pro loop do agente.
    """
    from langchain_core.tools import tool

    @tool
    def read_target_context(query: str) -> str:
        """Retorna o substrato-alvo da verificação para a query informada.

        Em proposta de edital: trechos relevantes do EDITAL atual (e análogos)
        para a `query` — use termos do rascunho que precise verificar (prazo,
        valor, TRL, elegibilidade, mecanismo). Trechos marcados "Análogo" são de
        OUTROS editais (referência), não fundamentam bloqueio.
        Em pitch de captação: os dados do FUNDO-ALVO (tese, temas, setores,
        estágio, ticket). A query é informativa; o contexto do fundo vem integral.
        """
        mode = getattr(session, "mode", "proposal")
        if mode == "pitch":
            ctx = getattr(session, "_pitch_target_context", "") or ""
            if not ctx.strip():
                return "Nenhum dado do fundo-alvo disponível para verificação."
            return ctx

        # Gênero proposta: retrieve do edital + bloco temporal (igual ao 1-shot).
        from core.retrieval.retriever import format_chunks_for_prompt, retrieve_chunks

        try:
            chunks = retrieve_chunks(
                session._db,
                session._scope_edital_ids,
                query=query or "",
                k=5,
            )
            edital_context = (
                format_chunks_for_prompt(chunks, edital_ids=session._scope_edital_ids)
                if chunks
                else "Nenhum trecho do edital disponível para verificação desta seção."
            )
        except Exception as e:
            logger.warning("critic [%s]: retrieve_chunks falhou: %s", session.session_id, e)
            return "Trechos do edital indisponíveis para verificação (falha no retrieve)."

        # Contexto temporal (Front 3): permite pegar prazos afirmados no rascunho
        # que divergem do deadline real. Falha graciosa: bloco vazio.
        temporal_block = ""
        try:
            from core.kg.temporal import render_temporal_block

            primary_edital = (
                getattr(session, "_scope_edital_ids", None)
                or [getattr(session, "edital_id", "")]
            )[0]
            rendered = render_temporal_block(primary_edital)
            temporal_block = f"{rendered}\n\n" if rendered else ""
        except Exception as e:
            logger.debug("critic [%s]: temporal block indisponível: %s", session.session_id, e)

        return f"{temporal_block}TRECHOS RELEVANTES DO EDITAL:\n{edital_context}"

    @tool
    def read_company_profile() -> str:
        """Retorna o perfil estrutural da empresa (porte, setor, elegibilidade,
        capacidades).

        Use para checar se o rascunho AFIRMA sobre a empresa algo que o perfil
        contradiz (ex.: elegibilidade/porte/setor que a empresa não tem). Isso é
        contradição, não omissão — e fundamenta um bloqueio.
        """
        ctx = getattr(session, "_profile_context", "") or ""
        if not ctx.strip():
            return "Perfil da empresa indisponível — não há dados para cruzar."
        return f"PERFIL DA EMPRESA:\n{ctx}"

    @tool
    def read_proposal_sections() -> str:
        """Retorna as OUTRAS seções já redigidas da proposta/pitch (exclui a que
        está sendo salva), para checar coerência entre seções.
        """
        try:
            return _build_proposal_context(session, section_title)
        except Exception as e:
            logger.warning("critic [%s]: read_proposal_sections falhou: %s", session.session_id, e)
            return "Outras seções indisponíveis para verificação."

    return [read_target_context, read_company_profile, read_proposal_sections]


def run_critic(
    draft: str,
    section_title: str,
    session,
    trace_context: dict | None = None,
) -> CriticResult:
    """Revisão de um rascunho de seção contra o substrato do gênero, via sub-agente.

    `session.mode` decide o substrato da checagem de contradição: edital (chunks
    de `edital_chunks`) no gênero proposta, ou o nó do fundo-alvo (`pitch`). A
    coerência entre seções, a consistência interna e a checagem contra o perfil
    valem nos dois.

    Item 5 da spec: refatorado de 1-shot para sub-agente (run_subagent) com 3
    tools e max_steps=3. Força OpenAI gpt-4o (modelo capaz; modelos fracos geram
    falsos-positivos) com temperature 0.05. Falha graciosa: stop_reason=="error"
    ou parse inválido → approved=True (save nunca bloqueia por falha do critic).

    Args:
        draft: conteúdo markdown do rascunho a revisar
        section_title: título da seção (para contexto no prompt)
        session: instância de WritingSession (acesso a db + scope_edital_ids + perfil)
        trace_context: contexto Langfuse do turno pai para aninhar o span do
            sub-agente. Deve ser capturado no call site (antes do thread pool).
    """
    mode = getattr(session, "mode", "proposal")
    system_prompt = _PITCH_CRITIC_SYSTEM if mode == "pitch" else _CRITIC_SYSTEM
    tools = build_critic_tools(session, section_title)
    task = _CRITIC_TASK.format(
        section_title=section_title,
        draft=draft[:_CRITIC_DRAFT_CHAR_CAP],
    )

    # Força OpenAI gpt-4o (replica a escolha de modelo do critic 1-shot): o critic
    # é um fact-checker de precisão crítica; modelos fracos não seguem de forma
    # confiável a instrução "só contradição, nunca omissão" → falsos-positivos.
    model = os.getenv("OPENAI_MODEL_CRITIC") or os.getenv("OPENAI_MODEL_PRO") or "gpt-4o-mini"

    # Endpoint OpenAI-compat do critic, parametrizável para bake-off: permite mirar o critic para um provider
    # OpenAI-COMPAT arbitrário (DeepSeek, vLLM/local, modelo ZDR pago) SEM editar
    # código, independentemente do endpoint do writing agent. Precedência:
    # CRITIC_OPENAI_* → AGENT_OPENAI_* (resolvido no agent_runtime) → OPENAI
    # canônica. None (default, nenhuma env nova setada) → comportamento
    # BYTE-IDÊNTICO ao anterior (endpoint canônico OpenAI).
    #
    # AVISO (tier agêntico = dado de cliente): o critic lê rascunhos de
    # proposta/pitch com dados confidenciais do cliente. É PROIBIDO mirar um
    # endpoint free-tier-com-treino; use só provider ZDR/pago.
    critic_base_url = os.getenv("CRITIC_OPENAI_BASE_URL") or None
    critic_api_key = os.getenv("CRITIC_OPENAI_API_KEY") or None

    result = run_subagent(
        name="critic",
        system=system_prompt,
        user_message=task,
        tools=tools,
        provider="openai",
        model=model,
        max_steps=3,
        temperature=0.05,
        openai_base_url=critic_base_url,
        openai_api_key=critic_api_key,
        trace_context=trace_context,
    )

    session_id = getattr(session, "session_id", "?")

    if result.stop_reason == "error":
        logger.warning("critic [%s]: sub-agente falhou (error) — aprovando por fallback", session_id)
        return CriticResult(approved=True, feedback="Revisão indisponível: erro no sub-agente.")

    # Observabilidade: steps por decisão (a spec quer média de retrieves para
    # calibração — média > 1.5 retrieves indica retrieve inicial fraco).
    n_steps = len(result.steps)

    raw = (result.final_text or "").strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    try:
        data = json.loads(raw)
    except Exception as e:
        logger.warning(
            "critic [%s]: %d steps, parse do veredito falhou (%s) — aprovando por fallback",
            session_id, n_steps, e,
        )
        return CriticResult(approved=True, feedback=f"Revisão indisponível: {e}")

    approved = bool(data.get("approved", True))
    logger.info("critic [%s]: %d steps, approved=%s", session_id, n_steps, approved)
    return CriticResult(
        approved=approved,
        issues=list(data.get("issues", [])),
        feedback=str(data.get("feedback", "")),
    )
