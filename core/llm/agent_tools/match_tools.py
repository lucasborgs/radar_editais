"""Tool de match cross-domínio do ExploreAgent (hipergrado, Sprint 1 F2).

`find_matching_editais` embrulha `core.services.hypergraph_match`: extrai os nós da
empresa do perfil (1 LLM call, cacheada por hash do texto) e devolve o ranking de
editais com a justificativa nativa (as arestas que casaram) + prazo/status/valor.
Só registrada quando há PERFIL (o explore público stateless não a vê). Match
geométrico — sem LLM no loop além da extração one-shot do perfil.
"""
from __future__ import annotations

import hashlib
import logging

from langchain_core.tools import BaseTool, tool

from core.services import hypergraph_match

logger = logging.getLogger(__name__)

# Cache de nós-empresa por hash do texto do perfil (efêmero, vida do processo).
# Evita re-extrair (1 LLM call) a cada chamada da tool no mesmo perfil. A
# durabilidade por workspace (company_hypergraphs em PG) é a Opção B, fora da F2.
_NODES_CACHE: dict[str, list[dict]] = {}


def _company_nodes(profile_text: str) -> list[dict]:
    h = hashlib.sha256(profile_text.encode("utf-8")).hexdigest()
    if h not in _NODES_CACHE:
        from core.retrieval.hyper_extractor import run_hyper_extract_company
        _NODES_CACHE[h] = run_hyper_extract_company(profile_text, company_id="explore").nodes
    return _NODES_CACHE[h]


def _format_matches(matches: list) -> str:
    lines = [f"{len(matches)} editais com afinidade ao perfil (match por conteúdo, mais fortes primeiro):"]
    for m in matches:
        disp = [x for x in (m.status, f"prazo {m.prazo}" if m.prazo else "", m.valor) if x]
        meta = f"  ({' | '.join(disp)})" if disp else ""
        lines.append(f"\n• {m.source}/{m.edital_id} — {m.name[:80]}{meta}")
        for p in m.paths[:3]:
            lines.append(f"    porquê: «{p.src}» ↔ «{p.dst}» ({p.dst_type}, {p.score:.2f})")
    lines.append("\n(Afinidade temática, não elegibilidade dura — quem decide é o usuário.)")
    return "\n".join(lines)


def build_match_tools(
    profile_text: str, *, company_nodes: list[dict] | None = None,
) -> list[BaseTool]:
    """Tool de match para o caminho COM perfil. `profile_text` (o bloco de perfil
    formatado) é capturado por closure — duas sessões nunca compartilham perfil.

    `company_nodes` (opcional): nós-empresa já materializados. Quando vêm do
    hipergrado durável da empresa (workspace autenticado), evitam re-extrair (poupa
    1 LLM call) e são mais ricos que a extração efêmera one-shot do perfil. Ausente
    (explore público stateless) → fallback para `_company_nodes(profile_text)`."""

    @tool
    def find_matching_editais(threshold: float = 0.55, top_k: int = 8) -> str:
        """Encontra editais que casam com o PERFIL DA EMPRESA por afinidade de
        conteúdo (tema/tecnologia/aplicação) no hipergrado — match cross-domínio.

        Use quando o usuário com perfil quer descobrir oportunidades ("quais
        editais servem para mim?", "o que tem para a minha empresa?"). Cada match
        traz a justificativa (o que da empresa casou com o quê do edital) + prazo/
        status/valor. É AFINIDADE temática, não elegibilidade dura — apresente como
        ponto de partida, não veredito.

        Args:
            threshold: corte de similaridade (0.0-1.0; default 0.6). Menor = mais
                       editais e mais ruído.
            top_k: máximo de editais a retornar (default 8).
        """
        try:
            # Nós duráveis do hipergrado da empresa têm prioridade; sem eles,
            # cai na extração efêmera (1 LLM call, cacheada por hash do perfil).
            nodes = company_nodes or _company_nodes(profile_text)
        except Exception as e:  # noqa: BLE001 — erro-como-string (padrão das tools)
            return f"Erro ao analisar o perfil: {e}."
        if not nodes:
            return (
                "Perfil insuficiente para o match — peça ao usuário o que a empresa "
                "faz, suas tecnologias e setor de atuação."
            )
        try:
            matches = hypergraph_match.find_matching_editais(
                nodes, threshold=float(threshold), top_k=int(top_k),
            )
        except Exception as e:  # noqa: BLE001
            return f"Erro no match: {e}."
        if not matches:
            return (
                "Nenhum edital com afinidade suficiente ao perfil. Tente um threshold "
                "menor ou refine o perfil (mais detalhe sobre o que a empresa faz)."
            )
        return _format_matches(matches)

    return [find_matching_editais]
