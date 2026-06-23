"""Tool de DeepResearch para agentes (subagente-como-tool).

DeepResearch. Expõe `deep_research` como uma tool
que, por dentro, roda um subagente de pesquisa web (core.deep_research) e devolve
uma resposta sintetizada COM bloco de fontes. Contida num caixote bounded: o crawl
multi-step fica isolado; o agente chamador recebe string limpa.

Fase A: a tool era stateless e NÃO persistia nada.

Fase B: quando `workspace_id` e `db` são
fornecidos, cada finding é persistido em `research_findings` (verified=false) como
EFEITO COLATERAL SILENCIOSO — a string devolvida ao agente é a mesma de sempre. O
humano depois promove findings da fila para a content_library (gate humano). Sem
workspace_id+db, comportamento da Fase A (stateless) fica intacto.
"""
from __future__ import annotations

import json
import logging
import os

from langchain_core.tools import BaseTool, tool

from core.deep_research import run_deep_research

logger = logging.getLogger(__name__)

# Guard-rail: teto de findings PENDENTES (reviewed_at is null) por workspace antes
# de o deep_research parar de criar novos. A pesquisa ainda serve o turno; só não
# vira finding. Evita inundar a fila de revisão.
RESEARCH_FINDINGS_MAX_PENDING = int(os.getenv("RESEARCH_FINDINGS_MAX_PENDING", "50"))


def _count_pending_findings(db, workspace_id: str) -> int:
    """Conta findings pendentes (não revisados) do workspace. Best-effort: em caso
    de erro retorna 0 para não bloquear a pesquisa (fail-open)."""
    try:
        res = (
            db.table("research_findings")
            .select("id", count="exact")
            .eq("workspace_id", workspace_id)
            .is_("reviewed_at", "null")
            .execute()
        )
        return res.count or 0
    except Exception as e:  # noqa: BLE001
        logger.warning("Falha ao contar research_findings pendentes: %s", e)
        return 0


def _persist_finding(db, workspace_id: str, question: str, res) -> None:
    """Insere um finding em research_findings. Falha → loga warning e segue
    (persistência é efeito colateral; nunca quebra a tool)."""
    try:
        sources = [
            {"url": s.url, "title": s.title or s.url}
            for s in (res.sources or [])
        ]
        db.table("research_findings").insert({
            "workspace_id": workspace_id,
            "question": question,
            "answer": res.answer,
            "sources": json.loads(json.dumps(sources)),
            "query": question,
            "verified": False,
        }).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning("Falha ao persistir research_finding: %s", e)


def build_research_tools(workspace_id: str | None = None, db=None) -> list[BaseTool]:
    """Tool de pesquisa web. Backward-compatible:

    - Sem args (`build_research_tools()`): stateless, comportamento da Fase A.
    - Com `workspace_id` + `db`: cada finding é persistido em research_findings
      (Fase B). A string devolvida ao agente é idêntica nos dois casos.

    Pode ser anexada a qualquer agente (Redator com session hoje; Explorador
    público — sem db — fica stateless).
    """
    persist = workspace_id is not None and db is not None

    @tool
    def deep_research(question: str) -> str:
        """Pesquisa um dado ou informação na internet e responde COM as fontes.

        Use quando precisa de um fato externo que NÃO está no edital nem na
        biblioteca do usuário (ex.: um número de mercado, uma definição técnica,
        um dado sobre uma instituição). A resposta vem com as URLs de origem —
        trate como informação externa a verificar, não como verdade do projeto.

        Args:
            question: a pergunta a pesquisar, específica e autocontida.
        """
        from core import telemetry
        # Captura o contexto Langfuse antes de chamar o sub-agente: o contextvar OTel
        # não é garantido no thread pool do LangGraph para tools síncronas.
        _trace_ctx = telemetry.current_trace_context()
        res = run_deep_research(question, trace_context=_trace_ctx)
        if not res.answer and not res.sources:
            return (
                "Não consegui pesquisar agora (busca web indisponível ou sem "
                "resultados). Tente novamente ou reformule."
            )

        if persist:
            # Efeito colateral silencioso: persiste se a fila não estiver cheia.
            pending = _count_pending_findings(db, workspace_id)
            if pending >= RESEARCH_FINDINGS_MAX_PENDING:
                logger.info(
                    "research_findings no teto (%d >= %d) para workspace %s — "
                    "pesquisa servida mas não persistida.",
                    pending, RESEARCH_FINDINGS_MAX_PENDING, workspace_id,
                )
            else:
                _persist_finding(db, workspace_id, question, res)

        parts = [res.answer.strip()]
        if res.sources:
            parts.append("\nFontes encontradas:")
            for s in res.sources:
                parts.append(f"- {s.title or s.url} — {s.url}")
        return "\n".join(p for p in parts if p)

    return [deep_research]
