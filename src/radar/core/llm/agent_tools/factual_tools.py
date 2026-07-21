"""Tool factual do ExploreAgent, escopada ao edital em foco."""
from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, tool

from radar.core.services.factual_retrieval import format_factual_evidence, retrieve_edital_evidence

logger = logging.getLogger(__name__)


def build_factual_tools(edital_id: str, retrieval_profile: str) -> list[BaseTool]:
    @tool
    def search_edital_factual(query: str) -> str:
        """Busca evidência normativa no edital em foco, sem usar editais análogos.

        Use obrigatoriamente antes de responder fatos detalhados, listas,
        admissibilidade, elegibilidade, itens financiáveis, contrapartida,
        documentos, prazos ou critérios. O retorno traz documento, versão,
        seção, página e texto. Cite esses campos; se a autoridade estiver
        ausente ou conflitante, declare a limitação.

        Args:
            query: pergunta factual original do usuário.
        """
        try:
            chunks = retrieve_edital_evidence(
                edital_id, query, profile=retrieval_profile,
            )
            return format_factual_evidence(chunks)
        except Exception as exc:  # tool nunca derruba o loop ReAct
            logger.warning("search_edital_factual falhou (%s): %s", edital_id, exc)
            return f"Erro ao recuperar evidência vigente de {edital_id}: {exc}."

    return [search_edital_factual]

