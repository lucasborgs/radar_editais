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
import re
from datetime import datetime, timezone

from langchain_core.tools import BaseTool, tool

from core.kg import hypergraph_catalog, kg_store
from core.kg.migrate_v2 import migrate_to_v2


def _ensure_v2(graphs: dict[str, dict]) -> dict[str, dict]:
    """Normaliza cada subgrafo para o formato v2 (idempotente). Permite que as
    funções puras aceitem tanto grafos já-v2 (do kg_store) quanto v1 (fixtures de
    teste em memória) — as arestas passam a referenciar members por `id`."""
    return {fk: migrate_to_v2(g) for fk, g in graphs.items()}

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


# =============================================================================
# Entity resolution cross-source (resolução de entidade entre subgrafos)
# =============================================================================
# Cada subgrafo é um KA independente. Uma mesma entidade real (ex.: "UFSC")
# pode aparecer em múltiplos subgrafos como nós separados. Esta camada
# permite navegar entre subgrafos via nome + tipo como chave composta:
#   resolve_entity(index, "UFSC", "Ator") → todos os nós Ator "UFSC" no grafo
# Fiel ao Hyper-Extract: cada KA preserva sua identidade; a conexão é
# resolvida em tempo de query por matching de (type, name).

def build_entity_index(graphs: dict[str, dict]) -> dict[tuple[str, str], list[tuple[str, dict]]]:
    """Índice global (type, name_lower) → [(file_key, node)] para resolução cross-source.

    Varre todos os subgrafos e indexa cada nó por (type, name_lower). Permite
    descobrir em quais subgrafos uma entidade aparece e navegar entre KAs."""
    graphs = _ensure_v2(graphs)  # nós ganham id (usado p/ semear BFS cross-source)
    idx: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    for fk, g in graphs.items():
        for n in g.get("nodes", []):
            nm = (n.get("name") or "").strip().lower()
            tp = (n.get("type") or "").strip().lower()
            if nm and tp:
                key = (tp, nm)
                idx.setdefault(key, []).append((fk, n))
    return idx


def resolve_entity(
    idx: dict[tuple[str, str], list[tuple[str, dict]]],
    name: str, type_: str,
) -> list[tuple[str, dict]]:
    """Resolve (type, name) no índice global. Retorna [(file_key, node)] de todos
    os subgrafos onde esta entidade aparece. Vazio se não encontrada."""
    key = (type_.strip().lower(), name.strip().lower())
    return idx.get(key, [])


# =============================================================================
# Leitura nativa do hipergrado (get_node_neighborhood) — funções puras
# =============================================================================
# Separadas da tool (que só carrega o grafo via kg_store e delega) para serem
# testáveis com fixture em memória — os arquivos hypergraphs/ são gitignored e
# não existem na CI.

def _node_index(graph: dict) -> dict[str, dict]:
    """id → node de um subgrafo v2 (as arestas referenciam members por id)."""
    return {n["id"]: n for n in graph.get("nodes", []) if n.get("id")}


def _member_label(idx: dict[str, dict], member: str) -> str:
    """Rótulo de um membro de aresta: nome canônico + tipo (+kind quando houver),
    via índice (id → nó) do subgrafo (fallback = o id cru quando ausente).

    O kind entra no rótulo (`Ator/ict`, `Oportunidade/programa`) porque o TIPO v2
    sozinho não distingue ICT de Investidor (ambos Ator) — consumidores como o
    tier2 do OpportunityService discriminam por esse rótulo."""
    n = idx.get(member)
    if not n:
        return member
    name = n.get("name") or member
    t = n.get("type")
    if not t:
        return name
    k = n.get("kind")
    return f"{name} ({t}/{k})" if k else f"{name} ({t})"


def resolve_graph_nodes(
    graphs: dict[str, dict], node_name: str, *, cap: int = 3,
) -> list[tuple[str, dict]]:
    """Resolve `node_name` para (file_key, node) varrendo os subgrafos.

    Prioridade: nome exato → Edital por id/fonte → substring de nome. As arestas
    do hipergrado referenciam nós por nome lowercased, então toda comparação é
    case-insensitive. Cap evita inundar quando um tema aparece em muitos editais.
    """
    needle = (node_name or "").strip().lower()
    if not needle:
        return []
    id_tokens = set(re.findall(r"[a-z]*\d[\w-]*", needle))
    exact: list[tuple[str, dict]] = []
    by_id: list[tuple[str, dict]] = []
    partial: list[tuple[str, dict]] = []
    for fk, g in graphs.items():
        native = fk.split("__")[-1].lower()
        src = fk.split("__")[0].lower()
        for n in g.get("nodes", []):
            nm = (n.get("name") or "").strip().lower()
            if not nm:
                continue
            if nm == needle:
                exact.append((fk, n))
            elif needle in nm or (len(nm) >= 4 and nm in needle):
                partial.append((fk, n))
            if n.get("type") == "Oportunidade" and n.get("kind") == "edital":
                eid = str(n.get("edital_id") or native).lower()
                if eid in id_tokens or native in id_tokens or (src and src in needle and eid in needle):
                    by_id.append((fk, n))
    out: list[tuple[str, dict]] = []
    seen: set[tuple[str, str]] = set()
    for group in (exact, by_id, partial):
        for fk, n in group:
            key = (fk, n.get("name") or "")
            if key not in seen:
                seen.add(key)
                out.append((fk, n))
    return out[:cap]


# Cap de grau do BFS (guardrail PR3): pós-canonicalização, Conceitos populares
# ("saúde", "IA") viram super-nós de alto fan-in — sem o cap, um nó desses
# inunda o contexto da LLM. Expande no máximo N arestas POR NÓ da frontier,
# priorizando arestas que tocam Oportunidade/Ator (o mapa fica rico em
# entidades, não em keyphrases).
BFS_NODE_DEGREE_CAP = 20


def _edge_priority(e: dict, idx: dict[str, dict]) -> int:
    """0 = aresta com Oportunidade/Ator entre os membros (expande primeiro)."""
    for m in e.get("members", []):
        if idx.get(m, {}).get("type") in ("Oportunidade", "Ator"):
            return 0
    return 1


def _bfs_subgraph(
    graph: dict, idx: dict[str, dict], seed_id: str, depth: int, max_edges: int,
    degree_cap: int = BFS_NODE_DEGREE_CAP,
) -> tuple[list[dict], set[str]]:
    """BFS de arestas em UM subgrafo a partir de `seed_id` (id de nó v2).

    Retorna (collected_edges, visited_node_ids) — os ids de TODOS os nós
    alcançados durante a BFS (incluindo o seed), para continuar BFS em outros
    subgrafos (cross-source). Cada nó da frontier expande no máximo
    `degree_cap` arestas, priorizadas por tipo (ver _edge_priority)."""
    frontier = {seed_id}
    visited = set(frontier)
    seen_edges: set[int] = set()
    edges = graph.get("edges", [])
    collected: list[dict] = []
    for _ in range(depth):
        nxt: set[str] = set()
        candidates = [
            (i, e) for i, e in enumerate(edges)
            if i not in seen_edges and frontier.intersection(e.get("members", []))
        ]
        candidates.sort(key=lambda ie: _edge_priority(ie[1], idx))
        budget: dict[str, int] = dict.fromkeys(frontier, degree_cap)
        for i, e in candidates:
            mem = e.get("members", [])  # ids (v2)
            hit = [m for m in mem if m in frontier]
            if not any(budget.get(h, 0) > 0 for h in hit):
                continue  # todos os nós da frontier que a aresta toca já saturaram
            for h in hit:
                budget[h] = budget.get(h, 0) - 1
            seen_edges.add(i)
            collected.append(e)
            nxt.update(m for m in mem if m not in visited)
        visited |= nxt
        frontier = nxt
        if not frontier:
            break
    return collected[:max_edges], visited


def _format_edges(
    collected: list[dict], idx: dict[str, dict],
) -> list[str]:
    """Formata arestas coletadas para display."""
    lines: list[str] = []
    if collected:
        lines.append(f"  relações ({len(collected)}):")
        for e in collected:
            members = ", ".join(_member_label(idx, m) for m in e.get("members", []))
            desc = (e.get("description") or "")[:120]
            lines.append(
                f"    • {e.get('type', '?')}: {members}" + (f" — {desc}" if desc else "")
            )
    else:
        lines.append("  (sem relações nativas neste subgrafo)")
    return lines


def neighborhood(
    graphs: dict[str, dict], node_name: str, depth: int = 1, *,
    max_edges: int = 25, cross_source: bool = False,
    entity_index: dict[tuple[str, str], list[tuple[str, dict]]] | None = None,
) -> str:
    """Vizinhança N-ária de um nó: props de display + arestas nativas (BFS até
    `depth` saltos) + vizinhos rotulados por tipo. String para a tool.

    Quando `cross_source=True`, a BFS atravessa subgrafos: após esgotar as arestas
    no subgrafo atual, resolve a entidade em outros subgrafos (via `entity_index`)
    e continua a BFS neles. O índice é construído automaticamente se não fornecido.
    """
    depth = max(1, min(int(depth), 2))
    graphs = _ensure_v2(graphs)  # arestas por id; nós ganham id (seed da BFS)
    targets = resolve_graph_nodes(graphs, node_name)
    if not targets:
        return (
            f"Nenhum nó '{node_name}' no hipergrado. Tente o nome do edital, o id "
            "(ex.: '589') ou um tema/tecnologia."
        )

    if cross_source:
        eidx = entity_index if entity_index is not None else build_entity_index(graphs)
    else:
        eidx = None

    blocks: list[str] = []
    visited_graph_nodes: set[tuple[str, str]] = set()

    for fk, node in targets:
        graph = graphs.get(fk, {})
        idx = _node_index(graph)
        src = fk.split("__")[0]
        native = fk.split("__")[-1]
        node_type = node.get("type", "?")

        gk = (fk, node.get("id", ""))
        if gk in visited_graph_nodes:
            continue
        visited_graph_nodes.add(gk)

        node_kind = node.get("kind", "")
        head = f"### {node.get('name', '')} [{node_type}"
        head += f"/{node_kind}]" if node_kind else "]"
        lines = [f"{head} · fonte={src}"]
        if node_type == "Oportunidade" and node_kind == "edital":
            disp = []
            if node.get("prazo"):
                disp.append(f"prazo {node['prazo']}")
            if node.get("status"):
                disp.append(f"status {node['status']}")
            if node.get("valor"):
                disp.append(f"valor {node['valor']}")
            disp.append(f"id {node.get('edital_id') or native}")
            lines.append("  " + " | ".join(disp))
        if node.get("description"):
            lines.append(f"  {node['description'][:200]}")
        # Guardrail de travessia (KG v2): Mecanismo/Requisito/Exclusão deixaram de
        # ser nós — sem serializar as PROPRIEDADES aqui, a LLM perderia info que
        # via como vizinhos. mecanismo/elegibilidade continuam visíveis na tool.
        if node.get("mecanismo"):
            lines.append(f"  mecanismo: {', '.join(node['mecanismo'])}")
        if node.get("macro_temas"):
            lines.append(f"  macro-temas: {', '.join(node['macro_temas'])}")
        for label, key in (("requisitos", "requisitos_texto"), ("exclusões", "exclusoes_texto")):
            vals = node.get(key) or []
            if vals:
                lines.append(f"  {label}: " + "; ".join(str(v)[:80] for v in vals[:4]))

        # BFS no subgrafo atual
        collected, visited = _bfs_subgraph(
            graph, idx, node.get("id", ""), depth, max_edges,
        )
        lines.extend(_format_edges(collected, idx))

        # BFS cross-source: para cada nó alcançado na BFS, busca em outros
        # subgrafos e continua a BFS neles. Usa `visited` (todos os nós
        # visitados, não só a frontier) para maximizar conexões cross-source.
        if cross_source and eidx is not None and visited:
            cross_seen: set[tuple[str, str]] = set()
            # `visited` são ids de nós; resolve cada um p/ (type, name) e busca a
            # MESMA entidade nos outros subgrafos via índice global (type, name).
            for vn_id in visited:
                vn_node = idx.get(vn_id)
                if not vn_node or not vn_node.get("type"):
                    continue
                other_key = (
                    vn_node["type"].strip().lower(),
                    (vn_node.get("name") or "").strip().lower(),
                )
                for other_fk, other_node in eidx.get(other_key, []):
                    if other_fk == fk:
                        continue  # já processamos este subgrafo
                    ogk = (other_fk, vn_id)
                    if ogk in visited_graph_nodes or ogk in cross_seen:
                        continue
                    cross_seen.add(ogk)
                    other_graph = graphs.get(other_fk, {})
                    other_idx = _node_index(other_graph)
                    other_src = other_fk.split("__")[0]
                    olines = [
                        f"  ↳ [{vn_node['type']}] em {other_fk} (fonte={other_src}):",
                    ]
                    o_collected, _ = _bfs_subgraph(
                        other_graph, other_idx, other_node.get("id", ""),
                        depth, max_edges,
                    )
                    olines.extend(
                        line.replace("  ", "    ") for line in _format_edges(o_collected, other_idx)
                    )
                    lines.extend(olines)

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


logger = logging.getLogger(__name__)


def build_explore_tools() -> list[BaseTool]:
    """Constrói as tools de leitura do agente de explore.

    Tudo lê o HIPERGRADO (via core.kg.hypergraph_catalog / kg_store) — stateless,
    reusável entre turns/usuários. A relação fina do grafo é o get_node_neighborhood
    (substitui find_analogues/get_graph_neighbors do GraphService legacy).
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
            # delega o `tema` ao catálogo (substring-direto, frágil p/ frase).
            editais = hypergraph_catalog.list_editais(status=status or None, limit=500)
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
            card = hypergraph_catalog.get_edital(edital_id)
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
    def list_icts(tema: str = "", limit: int = 20) -> str:
        """Lista ICTs (institutos de C&T, ex.: unidades EMBRAPII) por tema/setor.

        Use quando o usuário pergunta QUEM pode executar/fazer parceria num
        tema, ou quer um panorama da capacidade instalada de pesquisa. ICTs não
        lançam editais — viabilizam projetos (parceria). Para ICTs ligadas a um
        edital específico, use get_node_neighborhood no edital.

        Args:
            tema: palavra-chave ou tema (casa por token, tolerante a frase);
                  "" = todas
            limit: máximo a listar (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            icts = hypergraph_catalog.list_entity_catalog("ict", tema=tema, limit=limit)
        except Exception as e:
            return f"Erro ao listar ICTs: {e}."
        if not icts:
            return f"Nenhuma ICT encontrada{f' para tema={tema!r}' if tema else ''}."
        lines = [f"Encontradas {len(icts)} ICTs (mostrando até {limit}):"]
        for i in icts[:limit]:
            themes = ", ".join(i.get("themes", [])[:3])[:60]
            contact = i.get("description", "")[:60]
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
            invs = hypergraph_catalog.list_entity_catalog("investidores", tema=tema, limit=limit)
        except Exception as e:
            return f"Erro ao listar investidores: {e}."
        if not invs:
            return f"Nenhum investidor encontrado{f' para tema={tema!r}' if tema else ''}."
        lines = [f"Encontrados {len(invs)} investidores (mostrando até {limit}):"]
        for v in invs[:limit]:
            lines.append(
                f"  {v.get('name', v['id'])[:45]} | temas:{', '.join(v.get('themes', [])[:3])[:60]}"
            )
        return "\n".join(lines)

    @tool
    def explore_opportunity(tema: str, top_k: int = 15) -> str:
        """Panorama completo de oportunidades num tema: editais, ICTs,
        investidores e programas — tudo que o ecossistema tem para o tema.
        Inclui travessia cross-source entre subgrafos (edital → ICT → temas
        → outros editais) e, se houver perfil da empresa, match por afinidade.

        Use como PRIMEIRA chamada para QUALQUER pergunta ampla de descoberta:
        "quais oportunidades em agronegócio?", "o que existe para IA em saúde?",
        "quero desenvolver um trocador de calor". Depois, aprofunde com
        get_edital, get_node_neighborhood, list_icts ou list_investidores.

        Args:
            tema: palavra-chave ou tema (casa por token, tolera frase natural)
            top_k: máximo de resultados por categoria (default 15)
        """
        top_k = max(1, min(int(top_k), 30))
        try:
            from core.services.opportunity_service import OpportunityService
            svc = OpportunityService()
            result = svc.explore(tema, top_k=top_k)
        except Exception as e:
            return f"Erro ao explorar oportunidades: {e}."

        out: list[str] = [f"Panorama de oportunidades em '{tema}':"]

        editais = result.get("editais", [])
        # Enriquece com status do catálogo (Tier 2/3 não carregam status)
        if editais:
            try:
                catalog = {e["title"].lower(): e.get("status", "?") for e in hypergraph_catalog.list_editais(limit=500)}
                for ed in editais:
                    nm = (ed.get("title") or ed.get("name", "")).strip().lower()
                    if "status" not in ed:
                        ed["status"] = catalog.get(nm, "?")
            except Exception:
                pass
        abertos = sum(1 for e in editais if e.get("status") == "ABERTA")
        info = f" ({abertos} {'aberto' if abertos == 1 else 'abertos'})" if abertos else ""
        out.append(f"\n📋 Editais/desafios ({len(editais)}){info}:")
        for e in editais[:10]:
            title = e.get("title", e.get("name", ""))
            status = e.get("status", "?")
            out.append(f"  ID:{e.get('id', '?')} | {title[:60]} | {status} | prazo:{e.get('deadline', '?')}")
        if not editais:
            out.append("  (nenhum edital com esse tema)")

        # Filtra pelo catálogo curado (BFS cross-source encontra itens de subgrafos
        # aninhados que não são oportunidades autônomas)
        try:
            curated_icts = {e["name"].lower() for e in hypergraph_catalog.list_entity_catalog("ict", limit=999)}
            icts = [i for i in result.get("icts", []) if (i.get("name") or "").strip().lower() in curated_icts]
        except Exception:
            icts = result.get("icts", [])
        out.append(f"\n🔬 ICTs parceiras ({len(icts)}):")
        for i in icts[:8]:
            out.append(f"  {i.get('name', i.get('id', ''))[:55]}")
        if not icts:
            out.append("  (nenhuma ICT no tema)")

        try:
            curated_invs = {e["name"].lower() for e in hypergraph_catalog.list_entity_catalog("investidores", limit=999)}
            investidores = [v for v in result.get("investidores", []) if (v.get("name") or "").strip().lower() in curated_invs]
        except Exception:
            investidores = result.get("investidores", [])
        out.append(f"\n💸 Investidores com tese no tema ({len(investidores)}):")
        for v in investidores[:8]:
            out.append(f"  {v.get('name', v.get('id', ''))[:45]}")
        if not investidores:
            out.append("  (nenhum investidor no tema)")

        try:
            curated_progs = {e["name"].lower() for e in hypergraph_catalog.list_entity_catalog("programas", limit=999)}
            programas = [p for p in result.get("programas", []) if (p.get("name") or "").strip().lower() in curated_progs]
        except Exception:
            programas = result.get("programas", [])
        out.append(f"\n📋 Programas ({len(programas)}):")
        for p in programas[:5]:
            out.append(f"  {p.get('name', p.get('id', ''))[:55]}")
        if not programas:
            out.append("  (nenhum programa no tema)")

        return "\n".join(out)

    @tool
    def get_node_neighborhood(node_name: str, depth: int = 1, cross_source: bool = False) -> str:
        """Lê o hipergrado N-ário direto: a vizinhança de um nó (edital, tema,
        tecnologia, aplicação, requisito, ICT, programa...).

        Use para perguntas FACTUAIS sobre um edital (prazo, status, valor — vêm
        como propriedades do nó Edital) E para perguntas SEMÂNTICAS (quais
        tecnologias/temas/requisitos/parcerias um edital cobre — vêm das arestas
        nativas, ex.: `abrange_tema`, `exige`, `parceria_com`). Resolve o nó pelo
        nome ou pelo id do edital e devolve props + as relações N-árias em que ele
        participa, com os vizinhos rotulados por tipo.

        Quando `cross_source=True`, a BFS atravessa subgrafos: após esgotar as
        arestas no subgrafo do edital, busca a mesma entidade em outros subgrafos
        (catálogos de ICT, programas, investidores) e continua a BFS neles. Útil
        para perguntas como "quais ICTs são parceiras deste edital e que temas elas
        dominam?" ou "que outros editais tocam os mesmos temas desta ICT?".

        Args:
            node_name: nome do nó ou id do edital (ex.: "FINEP 589", "589",
                       "espectroscopia NIR", "bioeconomia").
            depth: 1 = arestas diretas (default). 2 = inclui vizinhos-dos-vizinhos
                   (mais contexto, mais ruído).
            cross_source: se True, atravessa subgrafos via resolução de entidade
                          (default False).
        """
        try:
            graphs = kg_store.load_all_hypergraphs()
        except Exception as e:
            return f"Erro ao carregar o hipergrado: {e}."
        eidx = build_entity_index(graphs) if cross_source else None
        return neighborhood(graphs, node_name, depth=depth, cross_source=cross_source, entity_index=eidx)

    return [explore_opportunity, list_editais, get_edital, get_node_neighborhood,
            list_icts, list_investidores]


# =============================================================================
# Memória do ExploreAgent entre sessões (Fase 3A — exploration_log)
# =============================================================================
# Diferente das tools acima (stateless, leitura-only sobre o disco), estas
# precisam de workspace + db AUTENTICADO (RLS por workspace). Só o caminho
# autenticado do front-door tem isso; o /kg-explore público não registra estas
# tools nem carrega o bloco de decisões. Escrita idempotente (ON CONFLICT DO
# UPDATE) — última decisão por (workspace, edital) prevalece.

# Normalização da decisão: o agente pode mandar PT/EN; mapeamos para o domínio
# canônico do CHECK da tabela ('recommended' | 'discarded'). Variantes fora
# disto viram erro-como-string (sem escrita) para o agente corrigir.
_DECISION_CANON = {
    "recommended": "recommended", "recommend": "recommended",
    "recomendado": "recommended", "recomendar": "recommended", "recomendou": "recommended",
    "discarded": "discarded", "discard": "discarded", "reject": "discarded",
    "rejected": "discarded", "descartado": "discarded", "descartar": "discarded",
    "descartou": "discarded",
}

EXPLORATION_DECISIONS_LIMIT = 20


def load_recent_exploration_decisions(db, workspace_id: str, limit: int = EXPLORATION_DECISIONS_LIMIT) -> str:
    """Bloco de decisões anteriores do workspace para o prefixo do system prompt.

    Lê as `limit` decisões mais recentes (decided_at DESC) e as formata como
    memória do agente. Retorna "" quando não há histórico (ou em falha de DB —
    a memória é best-effort, nunca derruba o turno). RLS já restringe ao
    workspace; filtramos por workspace_id também por clareza/defesa.
    """
    try:
        res = (
            db.table("exploration_log")
            .select("edital_id, decision, reason, decided_at")
            .eq("workspace_id", workspace_id)
            .order("decided_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        logger.warning("load_recent_exploration_decisions falhou (ws=%s): %s", workspace_id, e)
        return ""

    if not rows:
        return ""

    lines = [
        "DECISÕES ANTERIORES DESTE WORKSPACE (sua memória de sessões passadas, "
        "mais recentes primeiro):",
    ]
    for r in rows:
        date = str(r.get("decided_at") or "")[:10]
        verb = "recomendou" if r.get("decision") == "recommended" else "descartou"
        reason = f" — {r['reason']}" if r.get("reason") else ""
        lines.append(f"  • {date}: {verb} {r.get('edital_id')}{reason}")
    lines.append(
        "Use esta memória para manter coerência entre sessões: referencie decisões "
        "passadas quando relevante e não contradiga uma sem um motivo novo e explícito."
    )
    return "\n".join(lines)


def build_exploration_log_tools(db, workspace_id: str) -> list[BaseTool]:
    """Tool de escrita da memória do ExploreAgent (Fase 3A).

    Captura `db` (Supabase autenticado, RLS) e `workspace_id` por closure — duas
    sessões concorrentes nunca compartilham handle/escopo por engano (mesmo
    princípio das demais factories). Só deve ser registrada quando há workspace
    autenticado.
    """

    @tool
    def log_exploration_decision(edital_id: str, decision: str, reason: str = "") -> str:
        """Registra que você RECOMENDOU ou DESCARTOU um edital para este usuário.

        Chame ao concluir que um edital é uma boa oportunidade (decision=
        "recommended") ou que não serve (decision="discarded"), para LEMBRAR
        disso em sessões futuras deste mesmo workspace. Revisitar o mesmo edital
        ATUALIZA a decisão (a última prevalece) — pode rechamar sem medo de
        duplicar.

        Args:
            edital_id: id do edital (ex.: "finep:773").
            decision: "recommended" (recomendou) ou "discarded" (descartou).
            reason: justificativa curta (1 frase) — aparecerá para você em
                    sessões futuras como contexto da decisão.
        """
        eid = (edital_id or "").strip()
        if not eid:
            return "Erro: edital_id vazio. Informe o id do edital (ex.: 'finep:773')."
        canon = _DECISION_CANON.get((decision or "").strip().lower())
        if canon is None:
            return (
                f"Erro: decision inválida ({decision!r}). Use 'recommended' (recomendou) "
                "ou 'discarded' (descartou)."
            )
        try:
            db.table("exploration_log").upsert(
                {
                    "workspace_id": workspace_id,
                    "edital_id": eid,
                    "decision": canon,
                    "reason": (reason or "").strip() or None,
                    # Set explícito do timestamp: no caminho de UPDATE (ON CONFLICT)
                    # o default da coluna não reaplica; passamos now() nos dois casos.
                    "decided_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="workspace_id,edital_id",
            ).execute()
        except Exception as e:
            logger.warning(
                "log_exploration_decision falhou (ws=%s, edital=%s): %s",
                workspace_id, eid, e,
            )
            return f"Erro ao registrar a decisão sobre {eid}: {e}."
        verb = "recomendado" if canon == "recommended" else "descartado"
        return f"Decisão registrada: {eid} marcado como {verb} (lembrarei em sessões futuras)."

    return [log_exploration_decision]
