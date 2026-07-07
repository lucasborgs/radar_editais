"""Catálogo de editais lido do HIPERGRADO (substitui o index.json/wiki).

Sprint 3 Fase D: o catálogo (stats, lista, card) deixa de ler `index.json`/wiki
e passa a derivar os campos de display dos nós do hipergrado. Cada subgrafo de
edital (`file_key` com '__') é UM edital, então os campos saem dos nós tipados do
próprio subgrafo — não precisa traversar aresta para o card (a relação fina é o
get_node_neighborhood). Campos sem equivalente no grafo (link, value_range
estruturado, key_facts) ficam ausentes; o frontend já os renderiza condicionalmente.

Single source para os consumidores de catálogo: routers/catalog, writing,
applications e as tools de explore. Id no formato legado `source:native`
(ex.: "finep:589"), com round-trip para o `file_key` `source__native`.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import unicodedata

from config import KNOWLEDGE_GRAPH_DIR
from core.kg import kg_store
from core.kg.migrate_v2 import migrate_to_v2

logger = logging.getLogger(__name__)

_DEADLINE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%y", "%d/%m/%y")

_PT_MONTHS: dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_PT_DATE_RE = re.compile(r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", re.I)


def _deburr(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", s.lower())


_FONTE_CANONICAL: dict[str, str] = {
    "finep": "FINEP",
    "financiadoradeestudoseprojetos": "FINEP",
    "financiadoradeestudoseprojetosfinep": "FINEP",
    "finepfndct": "FINEP",
    "mctifinepfndct": "FINEP",
    "fnep": "FINEP",
    "fnct": "FINEP",
    "fndctfundossetoriais": "FNDCT",
    "fundonacionaldedesenvolvimentocientificoetecnologico": "FNDCT",
    "fundonacionaldedesenvolvimentocientificoetecnologicofndct": "FNDCT",
    "fundonacionaldedesenvolvimentocientificoetecnologicofndctsubvencaoeconomica": "FNDCT",
    "fndct": "FNDCT",
    "fapesp": "FAPESP",
    "fapesc": "FAPESC",
    "funcitec": "FAPESC",
    "bndes": "BNDES",
    "sistemabndes": "BNDES",
    "bancodenacionaldedesenvolvimentoeconomicoesocial": "BNDES",
    "bancodenacionaldedesenvolvimentoeconomicoesocialbndes": "BNDES",
    "bndespar": "BNDES",
    "cnpq": "CNPq",
    "capes": "CAPES",
    "sebrae": "Sebrae",
    "anp": "ANP",
    "anvisa": "ANVISA",
    "bb": "Banco do Brasil",
    "bancodobrasil": "Banco do Brasil",
    "bnb": "Banco do Nordeste",
    "bancodonordeste": "Banco do Nordeste",
    "bancodonordestedobrasilsa": "Banco do Nordeste",
    "embrapii": "EMBRAPII",
    "mcti": "MCTI",
    "ministeriodacienciaetecnologiaeinovacao": "MCTI",
    "ministeriodacienciaetecnologiaeinnovacoes": "MCTI",
    "ministeriodacienciaetecnologiaeinnovacao": "MCTI",
    "mdic": "MDIC",
}


def _normalize_source_name(raw: str) -> str:
    """Normaliza um nome de fonte para o canônico, ou retorna o original."""
    key = _deburr(raw)
    return _FONTE_CANONICAL.get(key, raw)


def _parse_deadline(raw: str | None) -> datetime.date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in _DEADLINE_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = _PT_DATE_RE.match(s)
    if m:
        day, month_pt, year = m.groups()
        month = _PT_MONTHS.get(month_pt.lower())
        if month:
            try:
                return datetime.date(int(year), month, int(day))
            except ValueError:
                pass
    return None


def _status(edital: dict) -> str:
    """ABERTA/ENCERRADA derivado do prazo (mais confiável que o status do LLM);
    fallback ao status extraído; senão Desconhecido."""
    d = _parse_deadline(edital.get("prazo"))
    if d:
        return "ABERTA" if d >= datetime.date.today() else "ENCERRADA"
    raw = (edital.get("status") or "").strip().lower()
    if raw.startswith("abert"):
        return "ABERTA"
    if raw.startswith(("encerr", "fechad", "conclu", "expir")):
        return "ENCERRADA"
    return "Desconhecido"


def _by_dim(nodes: list[dict], dim: str, *, include_entidade_v1: bool = False) -> list[str]:
    """Nomes dos Conceitos de uma dimensão (tema/tecnologia/aplicacao). Ex-Entidade
    (origem=entidade_v1) fica de FORA por default — em v1 eram nós Entidade (público-
    alvo), não Tema; incluí-los poluiria os temas do card."""
    out = []
    for n in nodes:
        if n.get("type") != "Conceito" or n.get("dim") != dim:
            continue
        if not include_entidade_v1 and n.get("origem") == "entidade_v1":
            continue
        if n.get("name"):
            out.append(n["name"])
    return out


def _by_kind(nodes: list[dict], v2type: str, kind: str) -> list[str]:
    """Nomes dos nós de um (type, kind) v2 — ex.: Ator/ict, Oportunidade/programa."""
    return [n["name"] for n in nodes if n.get("type") == v2type and n.get("kind") == kind and n.get("name")]


def _publico_alvo(nodes: list[dict]) -> list[str]:
    """Ex-nós Entidade (público-alvo elegível): Conceito marcado origem=entidade_v1."""
    return [n["name"] for n in nodes if n.get("origem") == "entidade_v1" and n.get("name")]


def edital_card(file_key: str, graph: dict, *, full: bool = False) -> dict | None:
    """Deriva o card de um subgrafo de edital. `full` adiciona os campos de detalhe
    (objetivo, mecanismo, elegíveis, requisitos) — a lista usa o resumo.

    v2: o edital é `Oportunidade(kind=edital)`; temas/tecnologias/aplicações são
    `Conceito(dim=…)`; mecanismo/requisitos/exclusões vêm das PROPRIEDADES do nó
    edital (foldadas na consolidação); ICTs/investidores são `Ator(kind=…)`."""
    from core.skills import mechanism_display

    nodes = graph.get("nodes", [])
    edital = next(
        (n for n in nodes if n.get("type") == "Oportunidade" and n.get("kind") == "edital"),
        None,
    )
    if edital is None:
        return None
    source, _, native = file_key.partition("__")
    # Proveniência determinística (D4/D14/PR4): URL oficial + PDFs + data de coleta,
    # encanada do bronze por fora do LLM. Bloco por-arquivo (1 arquivo = 1 edital).
    prov = graph.get("proveniencia") or {}
    # `fonte` de recurso: campo do nó edital (Fonte deixou de ser nó — D4).
    raw_fonte = [edital["fonte"]] if edital.get("fonte") else []
    fonte = sorted(set(
        _normalize_source_name(f)
        for item in raw_fonte
        for f in item.split(",")
        if f.strip()
    ))

    card: dict = {
        "id": f"{source}:{native}",
        "source": source,
        "title": edital.get("name", ""),
        "status": _status(edital),
        "deadline": edital.get("prazo") or "",
        "themes": _by_dim(nodes, "tema"),
        "technologies": _by_dim(nodes, "tecnologia"),
        "programs": _by_kind(nodes, "Oportunidade", "programa"),
        "publico_alvo": _publico_alvo(nodes),
        "fonte_recurso": fonte,
        "opportunity_type": "edital",
        "official_url": prov.get("url") or "",
        "aperture": edital.get("aperture") or "",
        "macro_temas": list(edital.get("macro_temas", [])),
    }
    if full:
        card.update({
            # Backbone v2 exposto p/ a ficha (PR8): kind/aperture/macro_temas
            # vinham do nó Oportunidade mas não eram surfaceados no card.
            "kind": edital.get("kind", "edital"),
            "aperture": edital.get("aperture") or "",
            "macro_temas": list(edital.get("macro_temas", [])),
            "objective": edital.get("description") or "",
            "mecanismo": list(edital.get("mecanismo", [])),
            "mechanism": ", ".join(mechanism_display(m) for m in edital.get("mecanismo", [])),
            "eligible_entities": _publico_alvo(nodes),
            "key_requirements": list(edital.get("requisitos_texto", [])),
            "aplicacoes": _by_dim(nodes, "aplicacao"),
            "exclusoes": list(edital.get("exclusoes_texto", [])),
            "value": edital.get("valor"),
            "icts": _by_kind(nodes, "Ator", "ict"),
            "investidores": _by_kind(nodes, "Ator", "investidor"),
            # Constraints de elegibilidade dura tipadas (PR5) — a avaliação
            # sat/unsat depende do perfil (match-time); aqui vai a lista crua
            # p/ a ficha (PR8) renderizar como chips.
            "constraints": list(edital.get("constraints", [])),
            # Proveniência completa (PR4/D14): link oficial + PDFs + coleta.
            "document_urls": list(prov.get("urls_documentos") or []),
            "collected_at": prov.get("coletado_em") or "",
        })
    return card


def _edital_graphs() -> list[tuple[str, dict]]:
    """(file_key, graph) só dos editais (file_key com '__'). Catálogos ficam fora."""
    return [
        (fk, g) for fk, g in kg_store.load_all_hypergraphs().items() if "__" in fk
    ]


def list_editais(
    status: str | None = None, tema: str | None = None, limit: int = 200,
) -> list[dict]:
    cards = [c for fk, g in _edital_graphs() if (c := edital_card(fk, g)) is not None]
    if status:
        cards = [c for c in cards if c["status"].upper() == status.upper()]
    if tema:
        tl = tema.lower()
        cards = [
            c for c in cards
            if any(tl in t.lower() for t in c.get("themes", []))
            or any(tl in t.lower() for t in c.get("technologies", []))
            or any(tl in t.lower() for t in c.get("programs", []))
        ]
    # Abertos primeiro, depois por título (ordenação estável e previsível).
    cards.sort(key=lambda c: (c["status"] != "ABERTA", c["title"].lower()))
    return cards[:limit]


def _resolve_file_key(edital_id: str, graphs: dict[str, dict]) -> str | None:
    """Resolve um id de catálogo para o file_key do subgrafo. Aceita
    "finep:589", "finep__589", "589" (sufixo nativo)."""
    eid = (edital_id or "").strip()
    if not eid:
        return None
    # Só o PRIMEIRO ':' separa fonte do nativo (simétrico a partition("__") no
    # edital_card) — um nativo pode legitimamente conter ':'.
    direct = eid.replace(":", "__", 1)
    if direct in graphs:
        return direct
    if eid in graphs:
        return eid
    # sufixo nativo puro (ex.: "589") → casa o file_key cujo nativo bate. Nativos
    # são únicos por-fonte, não globalmente: se colidir entre fontes, é ambíguo →
    # None (o caller deve passar "fonte:nativo").
    matches = [fk for fk in graphs if "__" in fk and fk.split("__", 1)[1] == eid]
    return matches[0] if len(matches) == 1 else None


def get_edital(edital_id: str) -> dict | None:
    graphs = kg_store.load_all_hypergraphs()
    fk = _resolve_file_key(edital_id, graphs)
    if fk is None:
        return None
    return edital_card(fk, graphs[fk], full=True)


# ── Ficha unificada por Oportunidade (PR8, D1) ───────────────────────────────
# A unidade de resultado é SEMPRE a Oportunidade (D1). A ficha resolve as três
# naturezas de oportunidade sob um payload único: editais (subgrafo `__`), e os
# curados programa/investimento (nós de catálogo, url/facetas por-nó — PR4.1).
# O Ator (fundo/ICT) nunca é ficha direta; aparece como relacionado da oferta.

# Catálogos que carregam Oportunidades curadas (ICT é Ator, não vira ficha).
_CURATED_CATALOGS = ("programas", "investidores")


def _related_atores(node_id: str, nodes_by_id: dict[str, dict], edges: list[dict],
                    kind: str) -> list[str]:
    """Nomes dos Atores de um `kind` ligados a `node_id` por qualquer aresta."""
    out: list[str] = []
    for e in edges:
        members = e.get("members", [])
        if node_id not in members:
            continue
        for m in members:
            nd = nodes_by_id.get(m)
            if nd and nd is not nodes_by_id.get(node_id) \
                    and nd.get("type") == "Ator" and nd.get("kind") == kind \
                    and nd.get("name"):
                out.append(nd["name"])
    return sorted(set(out))


def _concepts_via(node_ids: set[str], nodes_by_id: dict[str, dict],
                  edges: list[dict], dim: str | None = None) -> list[str]:
    """Nomes dos Conceitos ligados a qualquer id de `node_ids` por aresta.
    Conceitos de curados penduram no Ator (aresta `viabiliza`), não na oferta —
    daí a travessia offer → ator → conceito ser feita pelo caller via node_ids."""
    out: list[str] = []
    for e in edges:
        members = e.get("members", [])
        if not (node_ids & set(members)):
            continue
        for m in members:
            nd = nodes_by_id.get(m)
            if nd and _is_content(nd) and (dim is None or nd.get("dim") == dim) \
                    and nd.get("name"):
                out.append(nd["name"])
    return sorted(set(out))


def _curated_card(node: dict, graph: dict, public_id: str) -> dict:
    """Ficha de uma Oportunidade curada (programa/investimento) a partir do nó +
    vizinhança. Facetas (url, estágio, ticket, mecanismo) são propriedades do
    próprio nó (PR4.1, URL por-nó); Conceitos/Atores vêm das arestas."""
    from core.skills import mechanism_display

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    nodes_by_id = {n["id"]: n for n in nodes if n.get("id")}
    nid = node.get("id", "")

    # Investimento: o fundo é o Ator ligado por `pertence_a`; os Conceitos que o
    # fundo viabiliza são a afinidade da oferta. Junta oferta+fundo p/ a travessia.
    fund_ids = {
        m for e in edges if nid in e.get("members", []) and e.get("type") == "pertence_a"
        for m in e.get("members", [])
        if (nd := nodes_by_id.get(m)) and nd.get("type") == "Ator"
    }
    concept_scope = {nid} | fund_ids
    related_investidores = _related_atores(nid, nodes_by_id, edges, "investidor")
    related_icts = _related_atores(nid, nodes_by_id, edges, "ict")

    return {
        "id": public_id,
        "source": (graph.get("proveniencia") or {}).get("fonte") or "curadoria",
        "kind": node.get("kind", ""),
        "aperture": node.get("aperture") or "",
        "title": node.get("name", ""),
        "status": "Desconhecido",
        "deadline": node.get("prazo") or "",
        "objective": node.get("description") or "",
        "mecanismo": list(node.get("mecanismo", [])),
        "mechanism": ", ".join(mechanism_display(m) for m in node.get("mecanismo", [])),
        "macro_temas": list(node.get("macro_temas", [])),
        "themes": _concepts_via(concept_scope, nodes_by_id, edges, "tema"),
        "technologies": _concepts_via(concept_scope, nodes_by_id, edges, "tecnologia"),
        "aplicacoes": _concepts_via(concept_scope, nodes_by_id, edges, "aplicacao"),
        "programs": [],
        "publico_alvo": [],
        "eligible_entities": [],
        "key_requirements": list(node.get("requisitos_texto", [])),
        "exclusoes": list(node.get("exclusoes_texto", [])),
        "constraints": list(node.get("constraints", [])),
        "value": node.get("ticket_range") or node.get("valor"),
        "estagio_alvo": node.get("estagio_alvo") or "",
        "ticket_range": node.get("ticket_range") or "",
        "lead_follow": node.get("lead_follow") or "",
        "icts": related_icts,
        "investidores": related_investidores,
        "fonte_recurso": [],
        "opportunity_type": node.get("kind", ""),
        "official_url": node.get("url") or "",
        "document_urls": list(node.get("urls_documentos") or []),
        "collected_at": (graph.get("proveniencia") or {}).get("coletado_em") or "",
    }


def _resolve_curated_node(opp_id: str, graphs: dict[str, dict]) -> tuple[dict, dict, str] | None:
    """Resolve um id de ficha curada → (nó-oferta, grafo, public_id). Aceita:
    node-id v2 direto (`op:…`/`ator:…`), e os ids da lista `programa:{name}` /
    `investidor:{name}` / `investimento:{name}`. D1: para um fundo, devolve a
    OFERTA de investimento (`Oportunidade`), não o Ator."""
    eid = (opp_id or "").strip()
    if not eid:
        return None
    prefix, _, rest = eid.partition(":")
    rest_l = rest.strip().lower()
    # Os ids da lista/entity_id são `{kind}:{sufixo}`, onde `sufixo` é o node-id
    # SEM o prefixo de tipo — ex.: `programa:centelha` ↔ `op:centelha`;
    # `investidor:indicator-capital` ↔ fundo `ator:indicator-capital` (→ sua
    # oferta). Reconstruímos esses node-ids como candidatos de lookup direto.
    op_cand = {f"op:{rest}", f"op:{rest}-investimento"}
    ator_cand = f"ator:{rest}"

    for cat in _CURATED_CATALOGS:
        g = graphs.get(cat)
        if not g:
            continue
        g = migrate_to_v2(g)
        nodes = g.get("nodes", [])
        edges = g.get("edges", [])
        by_id = {n["id"]: n for n in nodes if n.get("id")}

        # 1. node-id v2 direto (op:/ator:) OU reconstruído do id da lista.
        node = by_id.get(eid)
        if node is None and prefix in ("investidor", "investimento"):
            node = by_id.get(ator_cand) or next(
                (by_id[c] for c in op_cand if c in by_id), None)
        elif node is None and prefix == "programa":
            node = by_id.get(f"op:{rest}")
        if node is not None:
            if node.get("type") == "Ator":  # D1: Ator → sua oferta de investimento
                node = _offer_of_ator(node.get("id", ""), nodes, edges) or node
            if node.get("type") == "Oportunidade":
                return node, g, eid
            continue

        # 2. fallback por nome (id da lista era `{prefix}:{name_lower}`).
        if prefix == "programa":
            for n in nodes:
                if n.get("kind") == "programa" and (n.get("name") or "").strip().lower() == rest_l:
                    return n, g, eid
        elif prefix in ("investidor", "investimento"):
            for n in nodes:
                if n.get("kind") == "investimento" and (n.get("name") or "").strip().lower() == rest_l:
                    return n, g, eid
            for n in nodes:
                if n.get("type") == "Ator" and n.get("kind") == "investidor" \
                        and (n.get("name") or "").strip().lower() == rest_l:
                    off = _offer_of_ator(n.get("id", ""), nodes, edges)
                    if off is not None:
                        return off, g, eid
    return None


def _offer_of_ator(ator_id: str, nodes: list[dict], edges: list[dict]) -> dict | None:
    """A Oportunidade(investimento) ligada a um Ator(investidor) via `pertence_a`."""
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    for e in edges:
        members = e.get("members", [])
        if e.get("type") == "pertence_a" and ator_id in members:
            for m in members:
                nd = by_id.get(m)
                if nd and nd.get("type") == "Oportunidade":
                    return nd
    return None


def get_opportunity(opp_id: str) -> dict | None:
    """Ficha unificada (D1): resolve edital OU curado (programa/investimento).
    Superset de `get_edital` — a ficha do frontend (PR8) chama esta."""
    graphs = kg_store.load_all_hypergraphs()
    fk = _resolve_file_key(opp_id, graphs)
    if fk is not None:
        return edital_card(fk, graphs[fk], full=True)
    resolved = _resolve_curated_node(opp_id, graphs)
    if resolved is None:
        return None
    node, graph, public_id = resolved
    return _curated_card(node, graph, public_id)


def investment_offer(oportunidade_id: str, graphs: dict[str, dict] | None = None) -> tuple[dict, dict] | None:
    """`(offer_node, catalog_graph)` da `Oportunidade(kind=investimento)` identificada
    por entity_id (`investidor:x`) ou node-id (`op:…`), ou None. Usado pelo veredito
    de ofertas de investimento (PR8.1) para extrair o sub-grafo a serializar."""
    if graphs is None:
        graphs = kg_store.load_all_hypergraphs()
    resolved = _resolve_curated_node(oportunidade_id, graphs)
    if resolved is None:
        return None
    node, graph, _ = resolved
    if node.get("kind") != "investimento":
        return None
    return node, graph


def programa_node(oportunidade_id: str, graphs: dict[str, dict] | None = None) -> tuple[dict, dict] | None:
    """`(node, catalog_graph)` da `Oportunidade(kind=programa)` identificada por
    entity_id (`programa:x`) ou node-id (`op:…`), ou None. Espelha `investment_offer`
    p/ o outro kind de Oportunidade curada — usado pelo veredito de programa
    (KG v2 resíduos PR-A) para extrair o sub-grafo a serializar."""
    if graphs is None:
        graphs = kg_store.load_all_hypergraphs()
    resolved = _resolve_curated_node(oportunidade_id, graphs)
    if resolved is None:
        return None
    node, graph, _ = resolved
    if node.get("kind") != "programa":
        return None
    return node, graph


def investment_offers_by_fund(graphs: dict[str, dict] | None = None) -> dict[str, dict]:
    """{nome_do_fundo(lower): facetas da OFERTA de investimento} — para o card do
    radar (D1): um fundo casa como Ator, mas o que o card apresenta é a sua oferta
    `Oportunidade(kind=investimento)` (ticket/estágio/URL), ligada por `pertence_a`."""
    if graphs is None:
        graphs = kg_store.load_all_hypergraphs()
    g = migrate_to_v2(graphs.get("investidores") or {})
    nodes = g.get("nodes", [])
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    out: dict[str, dict] = {}
    for e in g.get("edges", []):
        if e.get("type") != "pertence_a":
            continue
        members = [by_id.get(m) for m in e.get("members", [])]
        offer = next((n for n in members if n and n.get("kind") == "investimento"), None)
        fund = next((n for n in members if n and n.get("kind") == "investidor"), None)
        if offer and fund and fund.get("name"):
            out[fund["name"].strip().lower()] = {
                "offer_name": offer.get("name", ""),
                "official_url": offer.get("url") or "",
                "estagio_alvo": list(offer.get("estagio_alvo") or []),
                "ticket_range": offer.get("ticket_range"),
            }
    return out


def get_stats() -> dict:
    graphs = _edital_graphs()
    total = 0
    by_status: dict[str, int] = {}
    themes: set[str] = set()
    fontes: set[str] = set()
    for fk, g in graphs:
        c = edital_card(fk, g)
        if c is None:  # subgrafo '__' sem nó Edital (ETL parcial) — não conta
            continue
        total += 1  # total = cards válidos, p/ bater com a soma de by_status
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        themes.update(c["themes"])
        fontes.update(c["fonte_recurso"])
    all_graphs = kg_store.load_all_hypergraphs()
    n_programas = len(list_entity_catalog("programas", graphs=all_graphs, limit=999))
    n_investidores = len(list_entity_catalog("investidores", graphs=all_graphs, limit=999))
    n_icts = len(list_entity_catalog("ict", graphs=all_graphs, limit=999))
    return {
        "total_editais": total,
        "last_updated": "",
        "by_status": by_status,
        "n_themes": len(themes),
        "n_fontes": len(fontes),
        "n_programas": n_programas,
        "n_investidores": n_investidores,
        "n_icts": n_icts,
        "total_oportunidades": total + n_programas + n_investidores + n_icts,
    }


def _theme_match(needle: str, themes: list[str]) -> bool:
    """Casa uma query de tema contra uma lista de temas. Token-bidirecional."""
    if not (needle or "").strip():
        return True
    blob = " ".join(t or "" for t in themes).lower()
    n = needle.strip().lower()
    if n in blob:
        return True
    ntoks = [t for t in re.findall(r"\w+", n) if len(t) >= 4 and t not in {
        "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
        "para", "por", "com", "sem", "ou", "the", "of", "for", "and", "que",
        "uma", "um", "sobre", "como",
    }]
    ttoks = [t for t in re.findall(r"\w+", blob) if len(t) >= 4]
    return any(nt in tt or tt in nt for nt in ntoks for tt in ttoks)


# catalog_key → (tipo v2, kind). Ex.: ICTs são Ator(kind=ict).
_CATALOG_TYPE_MAP: dict[str, tuple[str, str]] = {
    "ict": ("Ator", "ict"),
    "investidores": ("Ator", "investidor"),
    "programas": ("Oportunidade", "programa"),
}


def _curated_ict_names() -> set[str]:
    """Nomes canônicos das ICTs curadas (fonte de verdade). Lê do arquivo
    `curated_icts.json`. Retorna vazio se o arquivo não existir ou falhar
    (degradação silenciosa)."""
    p = KNOWLEDGE_GRAPH_DIR / "curated_icts.json"
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()))
    except Exception:
        logger.exception("erro ao ler curated_icts.json")
        return set()


def _is_content(n: dict) -> bool:
    """Nó de conteúdo temático (Conceito real, não ex-Entidade)."""
    return n.get("type") == "Conceito" and n.get("origem") != "entidade_v1"


def list_entity_catalog(
    catalog_key: str, *,
    tema: str = "",
    limit: int = 50,
    graphs: dict[str, dict] | None = None,
) -> list[dict]:
    """Lista entidades de um catálogo (ict, investidores, programas) filtrando
    por tema. Lê do catalog hypergraph (ex.: `ict.json`). Retorna dicts com
    `id`, `name`, `description`, `themes` e `type` — compatível com o que
    `list_icts`/`list_investidores` esperam.

    Fonte única para tools e consumers: substitui o acesso direto a
    `kg_store.load_icts()` / `.load_investidores()` / `.load_programas()`."""
    wanted = _CATALOG_TYPE_MAP.get(catalog_key)
    if wanted is None:
        return []
    wanted_type, wanted_kind = wanted
    if graphs is None:
        graphs = kg_store.load_all_hypergraphs()
    g = graphs.get(catalog_key)
    if not g:
        return []
    g = migrate_to_v2(g)  # v2: tipos consolidados + arestas por id (idempotente)
    nodes = g.get("nodes", [])
    edges = g.get("edges", [])

    node_by_id: dict[str, dict] = {n["id"]: n for n in nodes if n.get("id")}

    def _is_wanted(n: dict) -> bool:
        return n.get("type") == wanted_type and n.get("kind") == wanted_kind

    # Índice de arestas: id da entidade → temas conectados via arestas nativas
    edge_themes: dict[str, set[str]] = {}
    for e in edges:
        members = e.get("members", [])  # ids (v2)
        thematic = [
            n["name"] for m in members
            if (n := node_by_id.get(m)) and _is_content(n)
        ]
        for m in members:
            nd = node_by_id.get(m)
            if nd and _is_wanted(nd):
                edge_themes.setdefault(m, set()).update(thematic)

    curated_ict = _curated_ict_names() if catalog_key == "ict" else None
    out: list[dict] = []
    for n in nodes:
        if not _is_wanted(n):
            continue
        nm = n.get("name", "").strip()
        if curated_ict is not None and curated_ict and nm not in curated_ict:
            continue
        themes_list = sorted(edge_themes.get(n.get("id", ""), set()))
        if tema and not _theme_match(tema, themes_list):
            continue
        out.append({
            "id": nm.lower(),  # id público da entidade (name_lower) — compat consumers
            "name": nm,
            "description": n.get("description") or "",
            "themes": themes_list,
            "type": wanted_type,
        })
        if len(out) >= limit:
            break
    return out


def list_opportunities(
    tipo: str | None = None, limit: int = 200,
) -> list[dict]:
    """Merge editais + programas + investidores + ICTs com campo type discriminator."""
    result: list[dict] = []
    graphs = kg_store.load_all_hypergraphs()

    if tipo is None or tipo == "edital":
        for c in list_editais(limit=limit):
            result.append({
                "id": c["id"],
                "title": c["title"],
                "type": "edital",
                "themes": c.get("themes", []),
                "status": c.get("status", "Desconhecido"),
                "deadline": c.get("deadline", ""),
                "fonte_recurso": c.get("fonte_recurso", []),
                "aperture": c.get("aperture", ""),
                "macro_temas": c.get("macro_temas", []),
            })

    if tipo is None or tipo == "programa":
        for e in list_entity_catalog("programas", graphs=graphs, limit=limit):
            result.append({
                "id": f"programa:{e['id']}",
                "title": e["name"],
                "type": "programa",
                "themes": e.get("themes", []),
                "description": e.get("description", ""),
            })

    if tipo is None or tipo == "investidor":
        for e in list_entity_catalog("investidores", graphs=graphs, limit=limit):
            result.append({
                "id": f"investidor:{e['id']}",
                "title": e["name"],
                "type": "investidor",
                "themes": e.get("themes", []),
                "description": e.get("description", ""),
            })

    if tipo is None or tipo == "ict":
        for e in list_entity_catalog("ict", graphs=graphs, limit=limit):
            result.append({
                "id": f"ict:{e['id']}",
                "title": e["name"],
                "type": "ict",
                "themes": e.get("themes", []),
                "description": e.get("description", ""),
            })

    return result[:limit]
