"""Tools de leitura do agente de explore (v3 — leem SÓ de SQL via entity_catalog).

Substituem o pipeline onde o catálogo inteiro era injetado no prompt e as arestas
semânticas do hipergrado. As tools de mapeamento do ecossistema seguem a §8 da
spec v3-unified:

  • list_editais        — catálogo por status/tema
  • get_edital          — ficha completa de UM edital
  • list_icts           — ICTs por tema (capacidade de P&D)
  • list_investidores   — fundos com tese num tema
  • get_investidor      — ficha estruturada completa de um fundo nomeado
  • explore_opportunity — panorama de um tema (editais + ICTs + investidores + programas)
  • search_entities     — §8.1 busca SEMÂNTICA sobre entities.embedding (por kind)
  • related_by_tags     — §8.2 entidades que compartilham tecnologias_tags (join GIN)
  • get_node_neighborhood — §8.2 vizinhança ESTRUTURAL (arestas entity_relationships)

Princípios: stateless (chat público, sem session/RLS); erro-como-string (mesmo
padrão de writing_tools); zero lógica de negócio própria — tudo delega a
`radar.core.kg.entity_catalog`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.tools import BaseTool, tool

from radar.core.kg import entity_catalog

logger = logging.getLogger(__name__)


def _format_provenance(provenance: dict) -> str:
    """Formata o dict público de proveniência para o payload textual do agente."""
    lines: list[str] = []
    for path, info in provenance.items():
        state = info.get("state", "unknown")
        citations = info.get("citations", [])
        if citations:
            for c in citations:
                doc = c.get("document") or "?"
                page = c.get("page")
                quote = (c.get("quote") or "")[:80]
                page_str = f", p. {page}" if page is not None else ""
                lines.append(
                    f"[PROVENANCE:{path}] state={state} | {doc}{page_str} — "
                    f"\"{quote}\""
                )
        else:
            lines.append(f"[PROVENANCE:{path}] state={state} (sem citação verificável)")
    return "\n".join(lines)


def build_explore_tools() -> list[BaseTool]:
    """Constrói as tools de leitura do agente de explore (lêem entity_catalog/SQL,
    stateless, reusáveis entre turns/usuários)."""

    @tool
    def list_editais(status: str = "", tema: str = "", limit: int = 20) -> str:
        """Lista editais do catálogo filtrando por status e/ou tema.

        Use quando o usuário pergunta quais editais estão abertos, quais tocam um
        tema, ou quer um panorama. Cada item vem com id, título, status, prazo e
        temas — use get_edital depois para detalhes (objetivo, requisitos, valores).

        Args:
            status: "ABERTA" | "ENCERRADA" | "" (vazio = todos)
            tema: palavra-chave/tema (casa por token, tolerante a frase); "" = sem filtro
            limit: máximo de editais (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            editais = entity_catalog.list_editais(status=status or None, tema=tema or None, limit=limit)
        except Exception as e:
            return f"Erro ao listar editais: {e}."
        if not editais:
            fd = []
            if status:
                fd.append(f"status={status}")
            if tema:
                fd.append(f"tema='{tema}'")
            return "Nenhum edital encontrado" + (" com " + ", ".join(fd) if fd else "") + "."
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
        """Retorna detalhes completos de um edital específico.

        Use quando o usuário nomeia um edital por ID, ou após list_editais para
        aprofundar. Devolve objetivo, mecanismo, elegíveis, valor, requisitos-chave.

        Args:
            edital_id: identificador do edital (ex.: "finep:589").
        """
        try:
            card = entity_catalog.get_edital(edital_id)
        except Exception as e:
            return f"Erro ao buscar edital {edital_id}: {e}."
        if not card:
            return (
                f"Edital {edital_id} não encontrado no catálogo. "
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
            parts.append(f"Elegíveis: {', '.join(ents) if isinstance(ents, list) else ents}")
        if card.get("value"):
            parts.append(f"Valor: {card['value']}")
        if card.get("themes"):
            parts.append(f"Temas: {', '.join(card['themes'][:6])}")
        for req in (card.get("key_requirements") or [])[:5]:
            parts.append(f"  • {req}")
        pv = card.get("provenance")
        if pv:
            parts.append(_format_provenance(pv))
        return "\n".join(parts)

    @tool
    def list_icts(tema: str = "", limit: int = 20) -> str:
        """Lista ICTs (institutos de C&T, ex.: unidades EMBRAPII) por tema/setor.

        Use quando o usuário pergunta QUEM pode executar/fazer parceria num tema,
        ou quer um panorama da capacidade instalada de P&D. ICTs não lançam
        editais — viabilizam projetos. Para ICTs ligadas a um edital específico,
        use get_node_neighborhood no edital.

        Args:
            tema: palavra-chave/tema; "" = todas
            limit: máximo (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            icts = entity_catalog.list_entity_catalog("ict", tema=tema, limit=limit)
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
        setor, ou equity num tema. Devolve nome e temas da tese.

        Args:
            tema: palavra-chave/tema; "" = todos
            limit: máximo (default 20, max 50)
        """
        limit = max(1, min(int(limit), 50))
        try:
            invs = entity_catalog.list_entity_catalog("investidores", tema=tema, limit=limit)
        except Exception as e:
            return f"Erro ao listar investidores: {e}."
        if not invs:
            return f"Nenhum investidor encontrado{f' para tema={tema!r}' if tema else ''}."
        lines = [f"Encontrados {len(invs)} investidores (mostrando até {limit}):"]
        for v in invs[:limit]:
            lines.append(f"  {v.get('name', v['id'])[:45]} | temas:{', '.join(v.get('themes', [])[:3])[:60]}")
        return "\n".join(lines)

    @tool
    def get_investidor(investidor_id: str) -> str:
        """Retorna a ficha estruturada completa de um investidor específico.

        Use quando o usuário nomeia um fundo ou pergunta por sua tese,
        verticais, setores, estágio, ticket ou portfólio. Prefira o ID retornado
        por list_investidores/search_entities (ex.: investidor:barn-invest).

        Args:
            investidor_id: identificador canônico do investidor.
        """
        try:
            card = entity_catalog.get_investidor(investidor_id)
        except Exception as e:
            return f"Erro ao buscar investidor {investidor_id}: {e}."
        if not card:
            return (
                f"Investidor {investidor_id} não encontrado. Use list_investidores "
                "ou search_entities para resolver o ID."
            )
        parts = [
            f"Investidor {card.get('id', investidor_id)} — {card.get('name', '(sem nome)')}",
            f"Tese: {card.get('tese') or 'não informada'}",
        ]
        for label, key in (
            ("Verticais/setores", "setores"),
            ("Temas", "tese_themes"),
            ("Estágio-alvo", "estagio_alvo"),
            ("Portfólio", "portfolio"),
        ):
            values = card.get(key) or []
            if values:
                parts.append(f"{label}: {', '.join(str(v) for v in values)}")
        ticket = card.get("ticket_range") or {}
        if ticket.get("min_brl") is not None or ticket.get("max_brl") is not None:
            parts.append(
                f"Ticket (BRL): {ticket.get('min_brl', '?')} a {ticket.get('max_brl', '?')}"
            )
        if card.get("site"):
            parts.append(f"Fonte oficial: {card['site']}")
        if card.get("verificado_em"):
            parts.append(f"Verificado em: {card['verificado_em']}")
        pv = card.get("provenance")
        if pv:
            parts.append(_format_provenance(pv))
        return "\n".join(parts)

    @tool
    def explore_opportunity(tema: str, top_k: int = 15) -> str:
        """Panorama de oportunidades num tema: editais + ICTs + investidores +
        programas — o que o ecossistema tem para o tema.

        Use como PRIMEIRA chamada para QUALQUER pergunta ampla de descoberta
        ("quais oportunidades em agronegócio?", "o que existe para IA em saúde?").
        Depois, aprofunde com get_edital, get_node_neighborhood, list_icts,
        list_investidores ou search_entities (busca semântica).

        Args:
            tema: palavra-chave/tema (casa por token, tolera frase natural)
            top_k: máximo por categoria (default 15, max 30)
        """
        top_k = max(1, min(int(top_k), 30))
        try:
            editais = entity_catalog.list_editais(tema=tema, limit=top_k)
            icts = entity_catalog.list_entity_catalog("ict", tema=tema, limit=top_k)
            invs = entity_catalog.list_entity_catalog("investidores", tema=tema, limit=top_k)
            progs = entity_catalog.list_entity_catalog("programas", tema=tema, limit=top_k)
        except Exception as e:
            return f"Erro ao explorar oportunidades: {e}."

        out: list[str] = [f"Panorama de oportunidades em '{tema}':"]
        abertos = sum(1 for e in editais if e.get("status") == "ABERTA")
        info = f" ({abertos} {'aberto' if abertos == 1 else 'abertos'})" if abertos else ""
        out.append(f"\n📋 Editais/desafios ({len(editais)}){info}:")
        for e in editais[:10]:
            out.append(f"  ID:{e.get('id', '?')} | {e.get('title', '')[:60]} | {e.get('status', '?')} | prazo:{e.get('deadline', '?')}")
        if not editais:
            out.append("  (nenhum edital com esse tema)")
        out.append(f"\n🔬 ICTs parceiras ({len(icts)}):")
        for i in icts[:8]:
            out.append(f"  {i.get('name', i.get('id', ''))[:55]}")
        if not icts:
            out.append("  (nenhuma ICT no tema)")
        out.append(f"\n💸 Investidores com tese no tema ({len(invs)}):")
        for v in invs[:8]:
            out.append(f"  {v.get('name', v.get('id', ''))[:45]}")
        if not invs:
            out.append("  (nenhum investidor no tema)")
        out.append(f"\n📋 Programas ({len(progs)}):")
        for p in progs[:5]:
            out.append(f"  {p.get('name', p.get('id', ''))[:55]}")
        if not progs:
            out.append("  (nenhum programa no tema)")
        return "\n".join(out)

    @tool
    def search_entities(query: str, kind: str = "", top_k: int = 10) -> str:
        """Busca SEMÂNTICA no ecossistema (editais, ICTs, investidores, programas,
        agências) por significado, não palavra-chave — usa o embedding da descrição.

        Use quando a pergunta é sobre CAPACIDADE/ATUAÇÃO ("quais atores atuam em
        visão computacional?", "quem trabalha com hidrogênio verde?") ou quando os
        filtros de tema não bastam. Filtre por `kind` para restringir o tipo.

        Args:
            query: descrição em linguagem natural do que procura.
            kind: "" (todos) | "edital" | "programa" | "investidor" | "ict" | "agencia".
            top_k: máximo de resultados (default 10, max 25).
        """
        top_k = max(1, min(int(top_k), 25))
        try:
            res = entity_catalog.search_entities(query, kind=kind or None, k=top_k)
        except Exception as e:
            return f"Erro na busca semântica: {e}."
        if not res:
            return f"Nada encontrado para {query!r}{f' (kind={kind})' if kind else ''}."
        lines = [f"Encontrados {len(res)} resultados para {query!r}:"]
        for r in res:
            desc = (r.get("description") or "")[:70]
            lines.append(f"  [{r.get('kind', '?')}] {r.get('name', '')[:50]} (ID:{r.get('id', '?')}) — {desc}")
        return "\n".join(lines)

    @tool
    def related_by_tags(entity_id: str, kind: str = "", top_k: int = 10) -> str:
        """Entidades que COMPARTILHAM tecnologias/temas com uma entidade dada
        (ex.: "editais parecidos com o finep:589"). É a aresta semântica implícita:
        join por tags em comum, rankeado por nº de tags compartilhadas.

        Args:
            entity_id: id ou nome da entidade de referência (ex.: "finep:589").
            kind: "" (todos) | "edital" | "programa" | "investidor" | "ict".
            top_k: máximo (default 10, max 25).
        """
        top_k = max(1, min(int(top_k), 25))
        try:
            res = entity_catalog.related_by_tags(entity_id, kind=kind or None, limit=top_k)
        except Exception as e:
            return f"Erro ao buscar relacionados: {e}."
        if not res:
            return f"Nada compartilha tags com {entity_id!r}{f' (kind={kind})' if kind else ''}."
        lines = [f"Relacionados a {entity_id!r} por tags:"]
        for r in res:
            shared = ", ".join(r.get("shared_tags", [])[:4])
            lines.append(f"  [{r.get('kind', '?')}] {r.get('name', '')[:50]} (ID:{r.get('id', '?')}) — tags: {shared}")
        return "\n".join(lines)

    @tool
    def get_node_neighborhood(entity_id: str, depth: int = 1) -> str:
        """Vizinhança ESTRUTURAL de uma entidade: as arestas determinísticas do
        catálogo (operado_por, subordinado_a, exige_parceria_com, credenciada_por).

        Use para saber QUEM opera um edital, a qual PROGRAMA ele pertence, que ICT
        ele exige, ou quais unidades uma agência credencia. Resolve o nó por id ou
        nome. Para relações SEMÂNTICAS (temas/tecnologias em comum), use
        related_by_tags ou search_entities.

        Args:
            entity_id: id ou nome (ex.: "finep:589", "FINEP", "Programa Centelha").
            depth: 1 = arestas diretas (default); 2 = inclui vizinhos-dos-vizinhos.
        """
        depth = max(1, min(int(depth), 2))
        try:
            return entity_catalog.get_node_neighborhood(entity_id, depth=depth)
        except Exception as e:
            return f"Erro ao ler a vizinhança: {e}."

    return [explore_opportunity, list_editais, get_edital, search_entities,
            related_by_tags, get_node_neighborhood, list_icts, list_investidores,
            get_investidor]


# =============================================================================
# Memória do ExploreAgent entre sessões (Fase 3A — exploration_log)
# =============================================================================
# Diferente das tools acima (stateless, leitura-only), estas precisam de
# workspace + db AUTENTICADO (RLS por workspace). Só o caminho autenticado do
# front-door as registra. Escrita idempotente (ON CONFLICT DO UPDATE) — última
# decisão por (workspace, edital) prevalece.

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
    a memória é best-effort, nunca derruba o turno)."""
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
        d = str(r.get("decided_at") or "")[:10]
        verb = "recomendou" if r.get("decision") == "recommended" else "descartou"
        reason = f" — {r['reason']}" if r.get("reason") else ""
        lines.append(f"  • {d}: {verb} {r.get('edital_id')}{reason}")
    lines.append(
        "Use esta memória para manter coerência entre sessões: referencie decisões "
        "passadas quando relevante e não contradiga uma sem um motivo novo e explícito."
    )
    return "\n".join(lines)


def build_exploration_log_tools(db, workspace_id: str) -> list[BaseTool]:
    """Tool de escrita da memória do ExploreAgent (Fase 3A). Captura `db`
    (Supabase autenticado, RLS) e `workspace_id` por closure."""

    @tool
    def log_exploration_decision(edital_id: str, decision: str, reason: str = "") -> str:
        """Registra que você RECOMENDOU ou DESCARTOU um edital para este usuário.

        Chame ao concluir que um edital é boa oportunidade (decision="recommended")
        ou que não serve (decision="discarded"), para LEMBRAR disso em sessões
        futuras deste workspace. Revisitar o mesmo edital ATUALIZA a decisão.

        Args:
            edital_id: id do edital (ex.: "finep:773").
            decision: "recommended" (recomendou) ou "discarded" (descartou).
            reason: justificativa curta (1 frase).
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
