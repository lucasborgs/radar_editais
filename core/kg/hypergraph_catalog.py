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
import logging
import re
import unicodedata

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
        # Link oficial (PR4) — para a lista já linkar para a página da fonte.
        "official_url": prov.get("url") or "",
    }
    if full:
        card.update({
            "objective": edital.get("description") or "",
            "mechanism": ", ".join(mechanism_display(m) for m in edital.get("mecanismo", [])),
            "eligible_entities": _publico_alvo(nodes),
            "key_requirements": list(edital.get("requisitos_texto", [])),
            "aplicacoes": _by_dim(nodes, "aplicacao"),
            "exclusoes": list(edital.get("exclusoes_texto", [])),
            "value": edital.get("valor"),
            "icts": _by_kind(nodes, "Ator", "ict"),
            "investidores": _by_kind(nodes, "Ator", "investidor"),
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
    return {
        "total_editais": total,
        "last_updated": "",
        "by_status": by_status,
        "n_themes": len(themes),
        "n_fontes": len(fontes),
        "n_programas": n_programas,
        "n_investidores": n_investidores,
        "total_oportunidades": total + n_programas + n_investidores,
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

    out: list[dict] = []
    for n in nodes:
        if not _is_wanted(n):
            continue
        nm = n.get("name", "")
        themes_list = sorted(edge_themes.get(n.get("id", ""), set()))
        if tema and not _theme_match(tema, themes_list):
            continue
        out.append({
            "id": nm.strip().lower(),  # id público da entidade (name_lower) — compat consumers
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
    """Merge editais + programas + investidores com campo type discriminator."""
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

    return result[:limit]
