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
# Leitura nativa do hipergrado (get_node_neighborhood) — funções puras
# =============================================================================
# Separadas da tool (que só carrega o grafo via kg_store e delega) para serem
# testáveis com fixture em memória — os arquivos hypergraphs/ são gitignored e
# não existem na CI.

def _node_index(graph: dict) -> dict[str, dict]:
    """name_lower → node de um subgrafo (último vence em colisão de nome)."""
    return {
        (n.get("name") or "").strip().lower(): n
        for n in graph.get("nodes", [])
        if n.get("name")
    }


def _member_label(idx: dict[str, dict], member: str) -> str:
    """Rótulo de um membro de aresta: nome canônico + tipo, via índice do subgrafo.

    As arestas referenciam membros lowercased; recupera o nome/tipo do nó
    (fallback = o membro cru quando o nó não está no subgrafo)."""
    n = idx.get((member or "").strip().lower())
    if not n:
        return member
    name = n.get("name") or member
    return f"{name} ({n['type']})" if n.get("type") else name


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
            if n.get("type") == "Edital":
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


def neighborhood(
    graphs: dict[str, dict], node_name: str, depth: int = 1, *, max_edges: int = 25,
) -> str:
    """Vizinhança N-ária de um nó: props de display + arestas nativas (BFS até
    `depth` saltos no subgrafo) + vizinhos rotulados por tipo. String para a tool."""
    depth = max(1, min(int(depth), 2))
    targets = resolve_graph_nodes(graphs, node_name)
    if not targets:
        return (
            f"Nenhum nó '{node_name}' no hipergrado. Tente o nome do edital, o id "
            "(ex.: '589') ou um tema/tecnologia."
        )

    blocks: list[str] = []
    for fk, node in targets:
        graph = graphs.get(fk, {})
        idx = _node_index(graph)
        src = fk.split("__")[0]
        native = fk.split("__")[-1]

        lines = [f"### {node.get('name', '')} [{node.get('type', '?')}] · fonte={src}"]
        if node.get("type") == "Edital":
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

        # BFS de arestas até `depth` saltos a partir do nó-semente no subgrafo.
        frontier = {(node.get("name") or "").strip().lower()}
        visited = set(frontier)
        seen_edges: set[int] = set()
        edges = graph.get("edges", [])
        collected: list[dict] = []
        for _ in range(depth):
            nxt: set[str] = set()
            for i, e in enumerate(edges):
                if i in seen_edges:
                    continue
                mem = [(m or "").strip().lower() for m in e.get("members", [])]
                if frontier.intersection(mem):
                    seen_edges.add(i)
                    collected.append(e)
                    nxt.update(m for m in mem if m not in visited)
            visited |= nxt
            frontier = nxt
            if not frontier:
                break

        if collected:
            lines.append(f"  relações ({len(collected)}):")
            for e in collected[:max_edges]:
                members = ", ".join(_member_label(idx, m) for m in e.get("members", []))
                desc = (e.get("description") or "")[:120]
                lines.append(
                    f"    • {e.get('type', '?')}: {members}" + (f" — {desc}" if desc else "")
                )
        else:
            lines.append("  (sem relações nativas neste subgrafo)")
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
            abertos = hypergraph_catalog.list_editais(status="ABERTA", limit=500)
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
    def get_node_neighborhood(node_name: str, depth: int = 1) -> str:
        """Lê o hipergrado N-ário direto: a vizinhança de um nó (edital, tema,
        tecnologia, aplicação, requisito, ICT, programa...).

        Use para perguntas FACTUAIS sobre um edital (prazo, status, valor — vêm
        como propriedades do nó Edital) E para perguntas SEMÂNTICAS (quais
        tecnologias/temas/requisitos/parcerias um edital cobre — vêm das arestas
        nativas, ex.: `abrange_tema`, `exige`, `parceria_com`). Resolve o nó pelo
        nome ou pelo id do edital e devolve props + as relações N-árias em que ele
        participa, com os vizinhos rotulados por tipo.

        Args:
            node_name: nome do nó ou id do edital (ex.: "FINEP 589", "589",
                       "espectroscopia NIR", "bioeconomia").
            depth: 1 = arestas diretas (default). 2 = inclui vizinhos-dos-vizinhos
                   (mais contexto, mais ruído).
        """
        try:
            graphs = kg_store.load_all_hypergraphs()
        except Exception as e:
            return f"Erro ao carregar o hipergrado: {e}."
        return neighborhood(graphs, node_name, depth=depth)

    return [list_editais, get_edital, get_node_neighborhood,
            list_icts, list_investidores, oportunidades_por_tema]


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
