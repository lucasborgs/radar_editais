"""Critic sub-agent — revisão 1-shot de rascunho de seção antes de salvar.

Chamado internamente pela tool `save_draft` (writing_tools.py). Recupera
trechos do edital relevantes para a seção, envia ao LLM com o rascunho e
retorna um CriticResult com approved + lista de issues específicos.

Princípios:
  • 1-shot (não é um agente com tool loop) — o edital é injetado diretamente
    via retriever, sem custo extra de round-trips.
  • Falha graciosa: erro de LLM ou retriever → CriticResult(approved=True)
    com nota de indisponibilidade. Save nunca bloqueia por falha do critic.
  • Apenas problemas reais: não bloqueia por estilo nem por omissão — apenas
    por afirmações que CONTRADIZEM o edital, contradizem OUTRA seção já redigida
    da proposta (coerência interna), ou são internamente inconsistentes.

Coerência interna: ao salvar uma seção, o critic recebe as demais seções já
redigidas e bloqueia se o rascunho contradiz alguma delas (ex.: a Conclusão
afirma algo incompatível com a Descrição da Equipe). Como toda edição passa
por save_draft, a checagem é bidirecional na prática — a seção que está sendo
salva é sempre cruzada com todas as outras.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_CRITIC_SYSTEM = """Você é um revisor de fatos de propostas para editais de fomento no Brasil.
Sua única função é IMPEDIR que um rascunho seja salvo quando ele AFIRMA algo FALSO —
isto é, quando o texto CONTRADIZ o edital, CONTRADIZ outra seção já redigida da proposta,
ou contém inconsistência factual interna.

Você NÃO avalia completude, abrangência, detalhamento, estilo ou se a seção cobre todos
os tópicos desejáveis — isso é trabalho de outra etapa (checklist), não sua. A ausência
de uma informação NUNCA é motivo para bloquear: só o que está ESCRITO e está ERRADO conta.

Bloqueie (approved=false) SOMENTE quando o rascunho AFIRMAR:
  (a) um fato que contradiz o edital (prazo, valor, TRL, elegibilidade, mecanismo),
  (b) um fato que contradiz OUTRA seção já redigida da proposta (ex.: número, prazo,
      escopo, tamanho da equipe, orçamento ou objetivo divergente do que já foi escrito), ou
  (c) algo internamente inconsistente que torne a proposta incorreta.

Para (b), só conta CONTRADIÇÃO factual entre o que o rascunho afirma e o que outra seção
afirma — não a mera ausência de um tópico em uma das seções. Na dúvida, aprove.
Sempre responda com JSON válido."""

_CRITIC_USER = """{temporal_block}TRECHOS RELEVANTES DO EDITAL:
{edital_context}

OUTRAS SEÇÕES JÁ REDIGIDAS DA PROPOSTA:
{proposal_context}

SEÇÃO SENDO SALVA: {section_title}
RASCUNHO:
{draft}

Confira se o RASCUNHO afirma algo FALSO, usando SOMENTE o edital, o contexto temporal
e as outras seções acima:
1. Alguma afirmação contradiz o edital (prazo, valor, TRL, elegibilidade, mecanismo)?
   Em especial, alguma data/prazo afirmado no rascunho DIVERGE do deadline real do
   contexto temporal acima?
2. Alguma afirmação contradiz uma OUTRA seção já redigida acima (dados, números, escopo,
   equipe, orçamento, objetivo divergentes)?
3. Há inconsistência factual interna que torne a proposta incorreta?

NÃO reporte omissões, falta de detalhe ou tópicos ausentes — apenas afirmações erradas
ou contraditórias. `issues` deve conter SOMENTE o que justifique NÃO salvar.

Responda com JSON:
{{
  "approved": true ou false,
  "issues": ["descrição objetiva de cada afirmação FALSA/contraditória (indique se o conflito é com o edital ou com outra seção)"],
  "feedback": "diagnóstico geral em 1 frase"
}}
Se o rascunho não afirma nada falso nem contraditório: approved=true, issues=[]."""

# Orçamento de chars para o contexto das outras seções no prompt do critic.
# read_full_proposal pode ser grande; truncamos para não estourar tokens.
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


def run_critic(draft: str, section_title: str, session) -> CriticResult:
    """Revisão 1-shot de um rascunho de seção contra o edital.

    Args:
        draft: conteúdo markdown do rascunho a revisar
        section_title: título da seção (para contexto no prompt)
        session: instância de WritingSession (para acesso a db + scope_edital_ids)
    """
    from core.llm_client import make_client
    from core.retriever import format_chunks_for_prompt, retrieve_chunks

    # Recupera trechos do edital relevantes para o conteúdo do rascunho.
    # Usa os primeiros 500 chars como query — suficiente para capturar tema.
    try:
        chunks = retrieve_chunks(
            session._db,
            session._scope_edital_ids,
            query=draft[:500],
            k=5,
        )
        edital_context = (
            format_chunks_for_prompt(chunks, edital_ids=session._scope_edital_ids)
            if chunks
            else "Nenhum trecho do edital disponível para verificação desta seção."
        )
    except Exception as e:
        logger.warning("critic [%s]: retrieve_chunks falhou: %s", session.session_id, e)
        edital_context = "Nenhum trecho do edital disponível para verificação."

    client = make_client(api_key=os.environ["OPENAI_API_KEY"])
    # O critic é um fact-checker de precisão crítica: ele decide se um rascunho
    # pode ser salvo. Modelos fracos (gpt-4o-mini) não seguem de forma confiável
    # a instrução de "só bloquear por contradição, nunca por omissão" — geram
    # falsos positivos que travam rascunhos legítimos. Usa um modelo capaz por
    # default, independente do OPENAI_MODEL barato usado no resto do sistema.
    model = (
        os.getenv("OPENAI_MODEL_CRITIC")
        or os.getenv("OPENAI_MODEL_PRO")
        or "gpt-4o"
    )

    proposal_context = _build_proposal_context(session, section_title)

    # Contexto temporal (Front 3): permite ao critic pegar prazos afirmados no
    # rascunho que divergem do deadline real. Falha graciosa: bloco vazio.
    try:
        from core.temporal import render_temporal_block
        primary_edital = (
            getattr(session, "_scope_edital_ids", None) or [getattr(session, "edital_id", "")]
        )[0]
        temporal_block = render_temporal_block(primary_edital)
        temporal_block = f"{temporal_block}\n\n" if temporal_block else ""
    except Exception as e:
        logger.debug("critic [%s]: temporal block indisponível: %s", session.session_id, e)
        temporal_block = ""

    user_msg = _CRITIC_USER.format(
        temporal_block=temporal_block,
        edital_context=edital_context,
        proposal_context=proposal_context,
        section_title=section_title,
        draft=draft[:3000],
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        data = json.loads(raw)
        return CriticResult(
            approved=bool(data.get("approved", True)),
            issues=list(data.get("issues", [])),
            feedback=str(data.get("feedback", "")),
        )
    except Exception as e:
        logger.warning("critic [%s]: LLM falhou: %s — aprovando por fallback", session.session_id, e)
        return CriticResult(approved=True, feedback=f"Revisão indisponível: {e}")
