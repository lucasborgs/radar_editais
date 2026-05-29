"""Tools do agente de KGMatch.explore (Sprint 3 do Cenário B).

Quatro tools que substituem o pipeline atual onde o catálogo inteiro é
injetado no prompt a cada turn:

  • list_editais     — filtra catálogo por status/tema/limite
  • get_edital       — detalhes completos de UM edital (wiki page)
  • find_analogues   — editais parecidos com um edital de referência
  • get_graph_neighbors — vizinhos de um nó qualquer no grafo (tema, publico,
                          subprograma, fonte)

Princípios:
  • Stateless — explore é chat público, sem session/RLS/workspace.
  • Leitura-only sobre knowledge_graph/ e vault Obsidian no disco.
  • Tools wrappeiam métodos existentes de KGMatchService — sem
    duplicação de lógica.
  • Erro-como-string (mesmo padrão de writing_tools).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.agent_runtime import Tool, tool

if TYPE_CHECKING:
    from core.kg_match_service import KGMatchService

logger = logging.getLogger(__name__)


def build_explore_tools(service: KGMatchService) -> list[Tool]:
    """Constrói as 4 tools do agente de explore.

    `service` precisa estar inicializado (com índice carregado). O agente é
    stateless por turno, mas o service é reusável entre turns/usuários.
    """

    @tool
    def list_editais(
        status: str = "",
        tema: str = "",
        limit: int = 20,
    ) -> str:
        """Lista editais do catálogo FINEP filtrando por status e/ou tema.

        Use quando o usuário pergunta sobre quais editais estão abertos,
        quais tocam um tema, ou quer um panorama de uma categoria. Cada
        item retornado vem com id, título, status, prazo e temas — use
        get_edital depois se precisar de detalhes (objetivo, requisitos,
        valores).

        Args:
            status: "ABERTA" | "ENCERRADA" | "" (vazio = todos)
            tema: substring case-insensitive nos temas do edital;
                  "" = nenhum filtro de tema
            limit: máximo de editais a listar (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            editais = service.list_editais(
                status=status or None,
                tema=tema or None,
                limit=limit,
            )
        except Exception as e:
            return f"Erro ao listar editais: {e}."

        if not editais:
            filter_desc = []
            if status:
                filter_desc.append(f"status={status}")
            if tema:
                filter_desc.append(f"tema='{tema}'")
            f = " com " + ", ".join(filter_desc) if filter_desc else ""
            return f"Nenhum edital encontrado{f}."

        lines = [f"Encontrados {len(editais)} editais:"]
        for e in editais:
            themes = ", ".join(e.get("themes", [])[:3])[:60]
            lines.append(
                f"  ID:{e['id']} | {e['title'][:60]} | {e.get('status', '?')} "
                f"| prazo:{e.get('deadline', '?')} | temas:{themes}"
            )
        return "\n".join(lines)

    @tool
    def get_edital(edital_id: str) -> str:
        """Retorna detalhes completos de um edital específico do catálogo.

        Use quando o usuário pergunta sobre um edital nomeado por ID, ou
        depois de list_editais para aprofundar em um candidato. Devolve
        objetivo, mecanismo, elegíveis, valor, TRL, requisitos-chave.

        Args:
            edital_id: identificador do edital (string numérica)
        """
        try:
            card = service.get_edital_by_id(edital_id)
        except Exception as e:
            return f"Erro ao buscar edital {edital_id}: {e}."

        if not card:
            return (
                f"Edital ID {edital_id} não encontrado no catálogo. "
                "Use list_editais para descobrir IDs válidos."
            )

        parts = [
            f"Edital {edital_id} — {card.get('title', '(sem título)')}",
            f"Status: {card.get('status', '?')}",
        ]
        if card.get("deadline"):
            parts.append(f"Prazo: {card['deadline']}")
        if card.get("objective"):
            parts.append(f"Objetivo: {card['objective']}")
        if card.get("mechanism"):
            parts.append(f"Mecanismo: {card['mechanism']}")
        if card.get("eligible_entities"):
            ents = card["eligible_entities"]
            parts.append(
                f"Elegíveis: {', '.join(ents) if isinstance(ents, list) else ents}"
            )
        vr = card.get("value_range") or {}
        if vr.get("min_brl") or vr.get("max_brl"):
            parts.append(
                f"Valor: R${vr.get('min_brl', '?')} – R${vr.get('max_brl', '?')}"
            )
        tr = card.get("trl_range") or {}
        if tr.get("min") is not None or tr.get("max") is not None:
            parts.append(f"TRL: {tr.get('min', '?')}–{tr.get('max', '?')}")
        if card.get("counterpart_required") is not None:
            parts.append(
                f"Contrapartida: {'sim' if card['counterpart_required'] else 'não'}"
            )
        themes = card.get("themes", [])
        if themes:
            parts.append(f"Temas: {', '.join(themes[:6])}")
        for req in (card.get("key_requirements") or [])[:5]:
            parts.append(f"  • {req}")
        return "\n".join(parts)

    @tool
    def find_analogues(edital_id: str) -> str:
        """Encontra editais análogos a um de referência via grafo (mesmo tema,
        público, subprograma ou fonte de recurso).

        Use quando o usuário pergunta "editais parecidos com X" ou quer
        explorar alternativas a um candidato específico. Retorna até 10
        IDs+títulos. Use get_edital depois para aprofundar.

        Args:
            edital_id: identificador do edital de referência
        """
        try:
            analogue_ids = service._find_analogue_ids(edital_id)
        except Exception as e:
            return f"Erro ao buscar análogos de {edital_id}: {e}."

        if not analogue_ids:
            return (
                f"Nenhum análogo encontrado para o edital {edital_id}. "
                "Pode ser que o edital não esteja indexado no grafo, ou que "
                "seja muito singular."
            )

        lines = [f"Análogos do edital {edital_id} (até 10):"]
        for aid in analogue_ids[:10]:
            card = service.get_edital_by_id(aid)
            title = card.get("title", "(sem título)") if card else "(detalhes ausentes)"
            status = card.get("status", "?") if card else "?"
            lines.append(f"  ID:{aid} | {title[:65]} | {status}")
        return "\n".join(lines)

    @tool
    def get_graph_neighbors(node_id: str) -> str:
        """Lista vizinhos de um nó do grafo (tema, público, subprograma, fonte).

        Use quando o usuário pergunta sobre uma categoria (ex.: "que editais
        atendem a bioeconomia?", "quais editais subvencionam ICTs?"). Para
        nós de tipo edital, prefira find_analogues. Para detalhar um edital
        vizinho, use get_edital com o ID retornado.

        Args:
            node_id: identificador do nó no grafo (formato "radar-editais/<folder>/<slug>")
        """
        try:
            edital_ids = service._edital_ids_for_node(node_id)
        except Exception as e:
            return f"Erro ao buscar vizinhos de {node_id}: {e}."

        if not edital_ids:
            return (
                f"Nó '{node_id}' não tem editais ligados (ou não existe no vault). "
                "Verifique o formato (radar-editais/<folder>/<slug>) e tente de novo."
            )

        lines = [f"Editais ligados a '{node_id}' ({len(edital_ids)}):"]
        for eid in edital_ids[:15]:
            card = service.get_edital_by_id(eid)
            title = card.get("title", "(sem título)") if card else "(detalhes ausentes)"
            status = card.get("status", "?") if card else "?"
            lines.append(f"  ID:{eid} | {title[:65]} | {status}")
        if len(edital_ids) > 15:
            lines.append(f"  ... e mais {len(edital_ids) - 15} editais")
        return "\n".join(lines)

    return [list_editais, get_edital, find_analogues, get_graph_neighbors]
