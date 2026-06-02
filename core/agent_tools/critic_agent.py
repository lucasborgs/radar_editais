"""Critic sub-agent — revisão 1-shot de rascunho de seção antes de salvar.

Chamado internamente pela tool `save_draft` (writing_tools.py). Recupera
trechos do edital relevantes para a seção, envia ao LLM com o rascunho e
retorna um CriticResult com approved + lista de issues específicos.

Princípios:
  • 1-shot (não é um agente com tool loop) — o edital é injetado diretamente
    via retriever, sem custo extra de round-trips.
  • Falha graciosa: erro de LLM ou retriever → CriticResult(approved=True)
    com nota de indisponibilidade. Save nunca bloqueia por falha do critic.
  • Apenas problemas reais: não bloqueia por estilo, apenas por contradições
    com o edital ou omissão de requisitos obrigatórios.
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
isto é, quando o texto CONTRADIZ o edital ou contém inconsistência factual interna.

Você NÃO avalia completude, abrangência, detalhamento, estilo ou se a seção cobre todos
os tópicos desejáveis — isso é trabalho de outra etapa (checklist), não sua. A ausência
de uma informação NUNCA é motivo para bloquear: só o que está ESCRITO e está ERRADO conta.

Bloqueie (approved=false) SOMENTE quando o rascunho AFIRMAR:
  (a) um fato que contradiz o edital (prazo, valor, TRL, elegibilidade, mecanismo), ou
  (b) algo internamente inconsistente que torne a proposta incorreta.

Na dúvida, aprove. Sempre responda com JSON válido."""

_CRITIC_USER = """TRECHOS RELEVANTES DO EDITAL:
{edital_context}

SEÇÃO: {section_title}
RASCUNHO:
{draft}

Confira, usando SOMENTE os trechos do edital acima, se o RASCUNHO afirma algo FALSO:
1. Alguma afirmação contradiz o edital (prazo, valor, TRL, elegibilidade, mecanismo)?
2. Há inconsistência factual interna que torne a proposta incorreta?

NÃO reporte omissões, falta de detalhe ou tópicos ausentes — apenas afirmações erradas.
`issues` deve conter SOMENTE afirmações falsas/contraditórias que justifiquem NÃO salvar.

Responda com JSON:
{{
  "approved": true ou false,
  "issues": ["descrição objetiva de cada afirmação FALSA/contraditória encontrada"],
  "feedback": "diagnóstico geral em 1 frase"
}}
Se o rascunho não afirma nada falso: approved=true, issues=[]."""


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
    from core.retriever import format_chunks_for_prompt, retrieve_chunks
    from core.llm_client import make_client

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

    user_msg = _CRITIC_USER.format(
        edital_context=edital_context,
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
