"""Tool de DeepResearch para agentes (subagente-como-tool).

DeepResearch Fase A (ver docs/spec_deepresearch.md). Expõe `deep_research` como
uma tool que, por dentro, roda um subagente de pesquisa web (core.deep_research)
e devolve uma resposta sintetizada COM bloco de fontes. Contida num caixote
bounded: o crawl multi-step fica isolado; o agente chamador recebe string limpa.

Guard-rail (Fase A): a tool é stateless e NÃO persiste nada (nem KG nem library).
A entrada de um fato na memória do projeto é o gate humano da Fase B.
"""
from __future__ import annotations

import logging

from core.deep_research import run_deep_research
from core.llm.agent_runtime import Tool, tool

logger = logging.getLogger(__name__)


def build_research_tools() -> list[Tool]:
    """Tool stateless de pesquisa web. Sem dependência de session — pode ser
    anexada a qualquer agente (Redator hoje; Explorador no futuro)."""

    @tool
    def deep_research(question: str) -> str:
        """Pesquisa um dado ou informação na internet e responde COM as fontes.

        Use quando precisa de um fato externo que NÃO está no edital nem na
        biblioteca do usuário (ex.: um número de mercado, uma definição técnica,
        um dado sobre uma instituição). A resposta vem com as URLs de origem —
        trate como informação externa a verificar, não como verdade do projeto.
        Não persiste nada: é só consulta.

        Args:
            question: a pergunta a pesquisar, específica e autocontida.
        """
        res = run_deep_research(question)
        if not res.answer and not res.sources:
            return (
                "Não consegui pesquisar agora (busca web indisponível ou sem "
                "resultados). Tente novamente ou reformule."
            )
        parts = [res.answer.strip()]
        if res.sources:
            parts.append("\nFontes encontradas:")
            for s in res.sources:
                parts.append(f"- {s.title or s.url} — {s.url}")
        return "\n".join(p for p in parts if p)

    return [deep_research]
