"""Tools do agente de KGMatch.explore (Sprint 3 do Cenário B).

Tools que substituem o pipeline atual onde o catálogo inteiro é
injetado no prompt a cada turn:

  • list_editais     — filtra catálogo por status/tema/limite
  • get_edital       — detalhes completos de UM edital (wiki page)
  • find_analogues   — editais parecidos com um edital de referência
  • get_graph_neighbors — vizinhos de um nó qualquer no grafo (tema, publico,
                          subprograma, fonte)
  • find_ict_partners — ICTs parceiras candidatas para um edital (Fase C,
                        sugestão temática via core.ict_match)

Princípios:
  • Stateless — explore é chat público, sem session/RLS/workspace.
  • Leitura-only sobre data/knowledge_graph/ e vault Obsidian no disco.
  • Tools wrappeiam métodos existentes de KGMatchService — sem
    duplicação de lógica.
  • Erro-como-string (mesmo padrão de writing_tools).
"""
from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from core.kg import kg_store
from core.llm.agent_runtime import Tool, _cap, tool
from core.retrieval.retriever import format_chunks_for_prompt, retrieve_chunks

# Stopwords PT/EN + conectivos que não discriminam tema (não devem casar
# sozinhos). Termos curtos como "ia"/"ai" caem pelo corte de tamanho (<4).
_THEME_STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "para", "por", "com", "sem", "ou", "the", "of", "for", "and", "que",
    "uma", "um", "sobre", "como",
}


def _theme_tokens(text: str) -> list[str]:
    """Tokens significativos (≥4 chars, sem stopwords), lowercase."""
    raw = re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE)
    return [t for t in raw if len(t) >= 4 and t not in _THEME_STOPWORDS]


def _theme_match(needle: str, themes: list[str]) -> bool:
    """Casa uma query de tema (needle) contra os temas/setores de um nó. Vazio =
    casa tudo (sem filtro).

    Tolerante a linguagem natural: além do substring direto, casa por TOKEN
    BIDIRECIONAL — qualquer token significativo (≥4 chars) da query que seja
    substring de um token do tema, ou vice-versa. Assim 'IA em saúde' casa
    'saúde e ciências da vida' (via 'saúde') e 'IA no agronegócio' casa 'agro,
    bioeconomia e alimentos' (via agro⊂agronegócio). Recall-first: o explore
    prefere oferecer demais a devolver vazio para uma pergunta razoável."""
    n = (needle or "").strip().lower()
    if not n:
        return True
    blob = " ".join(t or "" for t in themes).lower()
    if n in blob:  # caminho rápido: substring direto
        return True
    ntoks = _theme_tokens(n)
    ttoks = _theme_tokens(blob)
    return any(nt in tt or tt in nt for nt in ntoks for tt in ttoks)

if TYPE_CHECKING:
    from core.services.kg_match_service import KGMatchService

logger = logging.getLogger(__name__)

# Caps de orçamento de contexto para search_edital_trechos. O explore soma N
# editais × k trechos numa só chamada — cresce rápido. Defaults folgados;
# calibrar com o log de disparo (mesma disciplina do writing/spec 02).
EXPLORE_CHUNK_CHAR_CAP = int(os.getenv("EXPLORE_CHUNK_CHAR_CAP", "800"))   # por trecho
EXPLORE_TRECHOS_CHAR_CAP = int(os.getenv("EXPLORE_TRECHOS_CHAR_CAP", "6000"))  # total da tool-result
MAX_EDITAIS = 5  # teto de editais por chamada (orçamento de contexto)


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
            tema: palavra-chave ou tema (casa por token, tolerante a frase —
                  ex.: "IA em saúde" casa "saúde e ciências da vida");
                  "" = nenhum filtro de tema
            limit: máximo de editais a listar (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            # Filtro de tema robusto (token bidirecional) no lado da tool — não
            # delega o `tema` ao service (substring-direto, frágil p/ frase).
            editais = service.list_editais(status=status or None, limit=200)
            if tema:
                editais = [e for e in editais if _theme_match(tema, e.get("themes", []))]
            editais = editais[:limit]
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

    @tool
    def find_ict_partners(edital_id: str) -> str:
        """Sugere ICTs (instituições de C&T) parceiras para um edital, por
        afinidade temática.

        Use quando o usuário pergunta sobre parceiros/ICTs para um edital, ou
        quando o edital exige parceria com ICT. As ICTs são candidatas por
        sobreposição de tema — é uma SUGESTÃO para o usuário avaliar, não uma
        parceria firmada. Devolve nome, tipo, temas em comum e contato.

        Args:
            edital_id: identificador do edital (ex.: "finep:782")
        """
        from core import ict_match

        try:
            entry = ict_match.edital_entry(edital_id)
            partners = ict_match.find_partners(edital_id, k=5)
        except Exception as e:
            return f"Erro ao buscar parceiros ICT de {edital_id}: {e}."

        if entry is None:
            return (
                f"Edital {edital_id} não encontrado no índice. "
                "Use list_editais para descobrir IDs válidos."
            )

        requires = entry.get("requires_ict_partner", False)
        header = (
            f"Edital {edital_id} exige parceria com ICT."
            if requires else
            f"Edital {edital_id} NÃO aparenta exigir parceria com ICT "
            "(sugestões abaixo são por afinidade temática, não exigência)."
        )

        if not partners:
            return (
                f"{header}\n"
                "Nenhuma ICT com afinidade temática encontrada — o edital pode "
                "não ter tema mapeado, ou não há ICT compatível no grafo."
            )

        lines = [header, f"ICTs candidatas (até {len(partners)}, por tema em comum):"]
        for p in partners:
            contact = p.contact.get("email") or p.contact.get("site") or "(sem contato)"
            lines.append(
                f"  {p.name} [{p.kind}] | temas: {', '.join(p.themes_match)} "
                f"| {contact} | {p.url}"
            )
        return "\n".join(lines)

    @tool
    def list_icts(tema: str = "", limit: int = 20) -> str:
        """Lista ICTs (institutos de C&T, ex.: unidades EMBRAPII) por tema/setor.

        Use quando o usuário pergunta QUEM pode executar/fazer parceria num
        tema, ou quer um panorama da capacidade instalada de pesquisa. ICTs não
        lançam editais — viabilizam projetos (parceria). Para ICTs de um edital
        específico, prefira find_ict_partners.

        Args:
            tema: palavra-chave ou tema (casa por token, tolerante a frase);
                  "" = todas
            limit: máximo a listar (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            icts = [i for i in kg_store.load_icts() if _theme_match(tema, i.get("themes", []))]
        except Exception as e:
            return f"Erro ao listar ICTs: {e}."
        if not icts:
            return f"Nenhuma ICT encontrada{f' para tema={tema!r}' if tema else ''}."
        lines = [f"Encontradas {len(icts)} ICTs (mostrando até {limit}):"]
        for i in icts[:limit]:
            contact = (i.get("contact") or {}).get("email") or i.get("url", "")
            themes = ", ".join(i.get("themes", [])[:3])[:60]
            lines.append(f"  {i.get('name', i['id'])[:55]} | temas:{themes} | {contact}")
        return "\n".join(lines)

    @tool
    def list_investidores(tema: str = "", limit: int = 20) -> str:
        """Lista investidores (fundos/anjos) cuja tese cobre um tema/setor.

        Use quando o usuário pergunta sobre captação privada, quem investe num
        setor, ou opções de financiamento equity num tema. Devolve nome, tese
        (resumo), estágio-alvo e site.

        Args:
            tema: palavra-chave ou tema (casa por token, tolerante a frase);
                  "" = todos
            limit: máximo a listar (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            invs = [
                v for v in kg_store.load_investidores()
                if _theme_match(tema, v.get("tese_themes", []) + v.get("setores", []))
            ]
        except Exception as e:
            return f"Erro ao listar investidores: {e}."
        if not invs:
            return f"Nenhum investidor encontrado{f' para tema={tema!r}' if tema else ''}."
        lines = [f"Encontrados {len(invs)} investidores (mostrando até {limit}):"]
        for v in invs[:limit]:
            estagio = ", ".join(v.get("estagio_alvo", [])[:3])
            lines.append(
                f"  {v.get('name', v['id'])[:45]} | tese:{(v.get('tese') or '')[:70]} "
                f"| estágio:{estagio} | {v.get('site', '')}"
            )
        return "\n".join(lines)

    @tool
    def oportunidades_por_tema(tema: str) -> str:
        """Panorama CROSS-DIMENSIONAL de um tema/setor: junta editais/desafios
        abertos, ICTs parceiras e investidores com tese no tema — as quatro
        dimensões do grafo num só lugar.

        Use para perguntas amplas de descoberta, ex.: "quais oportunidades em
        agronegócio?", "o que existe para deep tech em saúde?". Depois, aprofunde
        com get_edital, list_icts ou list_investidores conforme o interesse.

        Args:
            tema: palavra-chave ou tema; casa por token e tolera frase natural
                  (ex.: "agro", "saúde", "IA em saúde", "IA no agronegócio")
        """
        out: list[str] = [f"Panorama de oportunidades em '{tema}':"]
        # Eventos (editais/desafios/programas) — filtro de tema robusto na tool.
        try:
            abertos = service.list_editais(status="ABERTA", limit=200)
            editais = [e for e in abertos if _theme_match(tema, e.get("themes", []))]
        except Exception:
            editais = []
        out.append(f"\n📋 Editais/desafios abertos ({len(editais)}):")
        for e in editais[:10]:
            out.append(f"  ID:{e['id']} | {e['title'][:60]} | prazo:{e.get('deadline', '?')}")
        if not editais:
            out.append("  (nenhum aberto com esse tema)")
        # Entidades
        try:
            icts = [i for i in kg_store.load_icts() if _theme_match(tema, i.get("themes", []))]
        except Exception:
            icts = []
        out.append(f"\n🔬 ICTs parceiras ({len(icts)}):")
        for i in icts[:8]:
            out.append(f"  {i.get('name', i['id'])[:55]}")
        if not icts:
            out.append("  (nenhuma no tema)")
        try:
            invs = [
                v for v in kg_store.load_investidores()
                if _theme_match(tema, v.get("tese_themes", []) + v.get("setores", []))
            ]
        except Exception:
            invs = []
        out.append(f"\n💸 Investidores com tese no tema ({len(invs)}):")
        for v in invs[:8]:
            out.append(f"  {v.get('name', v['id'])[:45]} | estágio:{', '.join(v.get('estagio_alvo', [])[:2])}")
        if not invs:
            out.append("  (nenhum no tema)")
        return "\n".join(out)

    @tool
    def search_edital_trechos(
        edital_ids: list[str],
        query: str,
        k_por_edital: int = 3,
    ) -> str:
        """Recupera TRECHOS LITERAIS dos editais para detalhe fino ou comparação fundamentada.

        Use SÓ quando a pergunta exige o texto real — ex.: "compare a contrapartida
        exigida nestes editais", "o que o edital X exige de TRL no detalhe". Para
        panorama/triagem, use list_editais / get_edital / oportunidades_por_tema:
        o resumo basta e é mais barato.

        Localize PRIMEIRO os edital_ids (list_editais, oportunidades_por_tema,
        get_graph_neighbors) e passe-os aqui.

        Args:
            edital_ids: IDs já localizados (máx 5). Ex.: ["<id_a>", "<id_b>"]
            query: o aspecto a detalhar/comparar, PT-BR. Frases curtas funcionam melhor.
            k_por_edital: trechos por edital (default 3, máx 5).
        """
        ids = [e for e in (edital_ids or []) if e][:MAX_EDITAIS]
        if not ids:
            return (
                "Nenhum edital_id válido. Localize IDs com list_editais / "
                "oportunidades_por_tema antes."
            )
        k = max(1, min(int(k_por_edital), 5))

        blocos: list[str] = []
        for eid in ids:
            try:
                # 1 edital por vez → cada um garante representação. Numa união
                # ranqueada (edital_ids=[a,b,c] numa só chamada), um edital pode
                # dominar o top-k e sufocar os outros — ruim p/ comparação.
                # `db=None`: retrieve_chunks ignora o parâmetro e conecta sozinha.
                chunks = retrieve_chunks(None, [eid], query=query, k=k)
            except Exception as e:
                logger.warning("search_edital_trechos: falha em %s: %s", eid, e)
                blocos.append(f"### {eid}\n(erro ao recuperar: {e})")
                continue
            if not chunks:
                blocos.append(f"### {eid}\n(sem trecho relevante p/ a query)")
                continue
            # Cap por trecho: cada chunk é truncado antes da concatenação
            # (k chunks inteiros somam rápido). Cap total na sequência.
            for c in chunks:
                txt = c.get("text", "")
                if txt:
                    c["text"] = _cap(
                        txt, EXPLORE_CHUNK_CHAR_CAP,
                        tool_name="search_edital_trechos[chunk]",
                    )
            blocos.append(
                f"### {eid}\n" + format_chunks_for_prompt(chunks, edital_ids=[eid])
            )

        return _cap(
            "\n\n".join(blocos), EXPLORE_TRECHOS_CHAR_CAP,
            tool_name="search_edital_trechos",
        )

    return [list_editais, get_edital, find_analogues, get_graph_neighbors,
            find_ict_partners, list_icts, list_investidores, oportunidades_por_tema,
            search_edital_trechos]
