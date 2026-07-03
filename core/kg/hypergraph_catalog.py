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


def _names(nodes: list[dict], *types: str) -> list[str]:
    wanted = set(types)
    return [n["name"] for n in nodes if n.get("type") in wanted and n.get("name")]


def edital_card(file_key: str, graph: dict, *, full: bool = False) -> dict | None:
    """Deriva o card de um subgrafo de edital. `full` adiciona os campos de detalhe
    (objetivo, mecanismo, elegíveis, requisitos) — a lista usa o resumo."""
    nodes = graph.get("nodes", [])
    edital = next((n for n in nodes if n.get("type") == "Edital"), None)
    if edital is None:
        return None
    source, _, native = file_key.partition("__")
    raw_fonte = [edital["fonte"]] if edital.get("fonte") else _names(nodes, "Fonte")
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
        "themes": _names(nodes, "Tema"),
        "technologies": _names(nodes, "Tecnologia"),
        "programs": _names(nodes, "Programa"),
        "publico_alvo": _names(nodes, "Entidade"),
        "fonte_recurso": fonte,
        "opportunity_type": "edital",
    }
    if full:
        card.update({
            "objective": edital.get("description") or "",
            "mechanism": ", ".join(_names(nodes, "Mecanismo")),
            "eligible_entities": _names(nodes, "Entidade"),
            "key_requirements": [
                (n.get("description") or n.get("name"))
                for n in nodes if n.get("type") == "Requisito"
            ],
            "aplicacoes": _names(nodes, "Aplicação"),
            "exclusoes": _names(nodes, "Exclusão"),
            "value": edital.get("valor"),
            "icts": _names(nodes, "ICT"),
            "investidores": _names(nodes, "Investidor"),
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


_CATALOG_TYPE_MAP: dict[str, str] = {
    "ict": "ICT",
    "investidores": "Investidor",
    "programas": "Programa",
}


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
    wanted_type = _CATALOG_TYPE_MAP.get(catalog_key)
    if wanted_type is None:
        return []
    if graphs is None:
        graphs = kg_store.load_all_hypergraphs()
    g = graphs.get(catalog_key)
    if not g:
        return []
    nodes = g.get("nodes", [])
    edges = g.get("edges", [])

    node_by_lower: dict[str, dict] = {}
    for n in nodes:
        nm = (n.get("name") or "").strip().lower()
        if nm:
            node_by_lower[nm] = n

    # Índice de arestas: name_lower → temas conectados via arestas nativas
    edge_themes: dict[str, set[str]] = {}
    for e in edges:
        members = [(m or "").strip().lower() for m in e.get("members", [])]
        thematic = [
            n["name"] for m in members
            if (n := node_by_lower.get(m)) and n.get("type") in ("Tema", "Tecnologia", "Aplicação")
        ]
        for m in members:
            nd = node_by_lower.get(m)
            if nd and nd.get("type") == wanted_type:
                edge_themes.setdefault(m, set()).update(thematic)

    out: list[dict] = []
    for n in nodes:
        if n.get("type") != wanted_type:
            continue
        nm = n.get("name", "")
        nm_lower = nm.strip().lower()
        themes_list = sorted(edge_themes.get(nm_lower, set()))
        if tema and not _theme_match(tema, themes_list):
            continue
        out.append({
            "id": nm_lower,
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
