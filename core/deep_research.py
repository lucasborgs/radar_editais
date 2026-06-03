"""Engine do DeepResearch — subagente que busca na web e sintetiza COM fonte.

DeepResearch Fase A (ver docs/spec_deepresearch.md). Padrão subagente-como-tool:
`run_deep_research` roda um `run_agent` interno com duas tools (web_search via
core.web_search; fetch_url para leitura profunda). Devolve `DeepResearchResult`
estruturado (answer + sources) — a função pura que tanto a tool do Redator
(core.agent_tools.research_tools) quanto, no futuro, a UI/gate (Fase B) consomem.

Princípio inegociável (anti-fabricação, [[project_robustez_spec]]): o subagente
sintetiza SÓ o que as fontes trazem, cita URL, e diz "não encontrei" quando for o
caso. Fato da web é claim externo com fonte — nunca verdade preenchida de memória.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from core import web_search as ws
from core.agent_runtime import resolve_agent_provider, run_agent, tool

logger = logging.getLogger(__name__)

DEEP_RESEARCH_MAX_STEPS = int(os.getenv("DEEP_RESEARCH_MAX_STEPS", "5"))
_FETCH_CHAR_LIMIT = 3000


@dataclass
class Source:
    title: str
    url: str
    snippet: str = ""


@dataclass
class DeepResearchResult:
    """Resultado do subagente. `sources` são os hits que ele de fato consultou
    (deduplicados por URL) — base do gate de learning da Fase B."""
    answer: str
    sources: list[Source] = field(default_factory=list)
    stop_reason: str = "end_turn"


_SYSTEM = (
    "Você é um pesquisador web que responde a uma pergunta pontual com FATOS "
    "RASTREÁVEIS. Regras invioláveis:\n"
    "• Use a tool web_search para buscar; use fetch_url para ler uma página "
    "promissora a fundo quando o trecho não bastar.\n"
    "• Sintetize APENAS o que as fontes retornaram. NUNCA complete com "
    "conhecimento próprio nem suponha.\n"
    "• Toda afirmação relevante deve vir de uma fonte que você buscou. Cite a "
    "URL ao lado do fato.\n"
    "• Se as buscas não trouxerem evidência suficiente, responda exatamente que "
    "não encontrou — não invente.\n"
    "• Responda em português, conciso, e termine com uma lista 'Fontes:' das "
    "URLs efetivamente usadas."
)


def run_deep_research(
    question: str,
    *,
    model: str | None = None,
    provider: str = "anthropic",
    max_steps: int = DEEP_RESEARCH_MAX_STEPS,
) -> DeepResearchResult:
    """Roda o subagente de pesquisa web sobre `question`. Stateless: nada é
    persistido (nem KG nem library) — a persistência é o gate humano da Fase B."""
    collected: list[ws.SearchHit] = []

    @tool
    def web_search(query: str, k: int = 5) -> str:
        """Busca na web por uma query. Retorna título, URL e trecho de cada
        resultado. Use para encontrar fontes sobre o que o usuário pediu."""
        try:
            hits = ws.web_search(query, k)
        except ws.WebSearchError as e:
            return f"Busca web indisponível: {e}."
        except Exception as e:  # rede/HTTP — não quebra o loop
            return f"Erro na busca: {e}."
        if not hits:
            return "Nenhum resultado para essa query. Tente reformular."
        collected.extend(hits)
        lines = []
        for h in hits:
            lines.append(
                f"• {h.title}\n  URL: {h.url}\n  Trecho: {h.snippet}"
                + (f"\n  Conteúdo: {h.content[:600]}" if h.content else "")
            )
        return "\n".join(lines)

    @tool
    def fetch_url(url: str) -> str:
        """Lê o conteúdo de uma URL específica (texto limpo). Use quando o trecho
        da busca não basta e a página parece ter a resposta."""
        from core.agent_tools.profile_tools import _fetch_and_parse
        try:
            d = _fetch_and_parse(url)
        except Exception as e:
            return f"Erro ao ler {url}: {e}."
        return f"{d.get('title', url)}\n{d.get('text', '')[:_FETCH_CHAR_LIMIT]}"

    prov, mdl = resolve_agent_provider(
        provider,  # type: ignore[arg-type]
        model or os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6"),
    )

    try:
        result = run_agent(
            system=_SYSTEM,
            initial_messages=[{"role": "user", "content": question}],
            tools=[web_search, fetch_url],
            model=mdl,
            provider=prov,
            max_steps=max_steps,
        )
    except Exception as e:
        logger.error("run_deep_research falhou: %s", e)
        return DeepResearchResult(answer="", sources=[], stop_reason="error")

    # Fontes = hits consultados, deduplicados por URL (preservando ordem).
    seen: set[str] = set()
    sources: list[Source] = []
    for h in collected:
        if h.url and h.url not in seen:
            seen.add(h.url)
            sources.append(Source(title=h.title, url=h.url, snippet=h.snippet))

    return DeepResearchResult(
        answer=result.final_text,
        sources=sources,
        stop_reason=result.stop_reason,
    )
