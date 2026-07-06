"""Exporta o hipergrado (hipergrafos N-ários) para um vault Obsidian.

Lê todos os editais via `hypergraph_catalog` e gera notas Markdown com
[[wikilinks]] para visualização no Graph View do Obsidian.

Schema v2 (WIKI.md §6.4): 3 tipos de nó — Oportunidade (kind=edital/programa/
investimento/...), Ator (kind=ict/investidor/agencia/fap/corporate/aceleradora),
Conceito (dim=tema/tecnologia/aplicacao). Mecanismo e Fonte deixaram de ser nós
(viraram propriedades da Oportunidade) — continuam exportados como notas, só
que a partir das propriedades, não de nós do grafo.

Uso:
    python scripts/export_to_obsidian.py
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault --subfolder radar-editais

Estrutura no vault (pastas de Ator/Conceito/Oportunidade só existem se houver
dados, mecanismos/fontes/editais sempre presentes):
    radar-editais/
    ├── HOME.md
    ├── editais/         → uma nota por edital (kind=edital)
    ├── temas/, tecnologias/, aplicacoes/     → Conceito por dim
    ├── icts/, investidores/, agencias/, faps/, corporates/, aceleradoras/  → Ator por kind
    ├── programas/, investimentos/, ...       → Oportunidade por kind (exceto edital)
    ├── mecanismos/      → propriedade mecanismo[] da Oportunidade
    └── fontes/          → propriedade fonte da Oportunidade (proveniência)
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

from config import OBSIDIAN_VAULT_DIR
from core.kg import hypergraph_catalog
from core.kg.kg_store import load_all_hypergraphs
from core.llm.agent_tools.explore_tools import build_entity_index

# =============================================================================
# SCHEMA v2 — mapa (type, kind/dim) → (pasta, emoji, rótulo)
# =============================================================================

# (kind/dim) → (pasta, emoji, rótulo singular, rótulo plural, tag) — plurais em
# PT-BR são irregulares demais p/ derivar automaticamente do singular.
_ATOR_FOLDERS = {
    "ict": ("icts", "🔬", "ICT", "ICTs"),
    "investidor": ("investidores", "💼", "Investidor", "Investidores"),
    "agencia": ("agencias", "🏛️", "Agência", "Agências"),
    "fap": ("faps", "🏦", "FAP", "FAPs"),
    "corporate": ("corporates", "🏢", "Corporate", "Corporates"),
    "aceleradora": ("aceleradoras", "🚀", "Aceleradora", "Aceleradoras"),
}
_CONCEITO_FOLDERS = {
    "tema": ("temas", "🏷️", "Tema", "Temas"),
    "tecnologia": ("tecnologias", "🔧", "Tecnologia", "Tecnologias"),
    "aplicacao": ("aplicacoes", "🎯", "Aplicação", "Aplicações"),
}
# kind=edital tem nota própria (_edital_note) — as demais Oportunidade entram aqui.
_OPORTUNIDADE_FOLDERS = {
    "programa": ("programas", "📋", "Programa", "Programas"),
    "investimento": ("investimentos", "💰", "Investimento", "Investimentos"),
    "desafio": ("desafios", "🎲", "Desafio", "Desafios"),
    "aceleracao": ("aceleracoes", "🚀", "Aceleração", "Acelerações"),
    "incubacao": ("incubacoes", "🐣", "Incubação", "Incubações"),
    "parceria_pd": ("parcerias-pd", "🤝", "Parceria P&D", "Parcerias P&D"),
}

# (type, kind/dim) → (pasta, emoji, singular, plural) — usado para bucketizar
# nós do grafo. A chave `kind_or_dim` também serve de tag YAML da nota.
_FOLDER_META: dict[tuple[str, str], tuple[str, str, str, str]] = {}
for _kind, _meta in _ATOR_FOLDERS.items():
    _FOLDER_META[("Ator", _kind)] = _meta
for _dim, _meta in _CONCEITO_FOLDERS.items():
    _FOLDER_META[("Conceito", _dim)] = _meta
for _kind, _meta in _OPORTUNIDADE_FOLDERS.items():
    _FOLDER_META[("Oportunidade", _kind)] = _meta

_ALL_NODE_FOLDERS = {folder for folder, _, _, _ in _FOLDER_META.values()}

_CONSTRAINT_LABELS = {
    "porte": "Porte", "sede_uf": "Sede (UF)", "faturamento": "Faturamento",
    "trl": "TRL", "forma_juridica": "Forma jurídica", "parceria": "Parceria exigida",
}
_OP_LABELS = {"in": "∈", "not_in": "∉", "lte": "≤", "gte": "≥", "exige": "requer"}
_APERTURE_LABELS = {
    "prazo": "🗓️ Prazo definido", "continua": "🔁 Contínua",
    "recorrente": "🔂 Recorrente", "fechada": "🔒 Fechada",
}


# =============================================================================
# UTILIDADES
# =============================================================================

def _slugify(text: str) -> str:
    """Slug para nome de arquivo/wikilink (sem acentos, sem espaços)."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s.lower())
    return re.sub(r"[-\s]+", "-", s).strip("-")


def _edital_slug(eid: str) -> str:
    """finep:739 → finep_739 (filename-safe)."""
    return eid.replace(":", "_")


def _safe_yaml(text: str) -> str:
    return text.replace('"', '\\"')


def _status_emoji(status: str) -> str:
    return {"ABERTA": "🟢", "ENCERRADA": "🔴", "Desconhecido": "⚪"}.get(status, "⚪")


def _constraint_line(c: dict) -> str:
    tipo = c.get("tipo", "?")
    op = c.get("op", "?")
    valor = c.get("valor")
    label = _CONSTRAINT_LABELS.get(tipo, tipo)
    op_label = _OP_LABELS.get(op, op)
    valor_s = ", ".join(str(v) for v in valor) if isinstance(valor, list) else str(valor)
    return f"- **{label}** {op_label} {valor_s}"


def _edge_neighbors(
    node_id: str,
    occurrences: list[tuple[str, dict]],
    all_graphs: dict[str, dict],
) -> set[str]:
    """Nomes dos nós vizinhos (resolvidos por id via arestas v2, cujos `members`
    são ids, não nomes) nos subgrafos onde a entidade (já com `node_id` conhecido
    por ocorrência) de fato participa de alguma aresta."""
    neighbors: set[str] = set()
    for fk, node in occurrences:
        g = all_graphs.get(fk)
        if not g:
            continue
        nid = node.get("id") or node_id
        node_idx = {n["id"]: n for n in g.get("nodes", []) if n.get("id")}
        for edge in g.get("edges", []):
            members = edge.get("members", [])
            if nid not in members:
                continue
            for m in members:
                if m == nid:
                    continue
                other = node_idx.get(m)
                if other and other.get("name"):
                    neighbors.add(other["name"])
    return neighbors


# =============================================================================
# GERADORES DE NOTAS
# =============================================================================

def _edital_note(card: dict, full: dict | None, subfolder: str) -> str:
    eid = card["id"]
    title = card["title"]
    status = card["status"]
    emoji = _status_emoji(status)
    deadline = card.get("deadline", "")

    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(title)}"')
    lines.append(f"edital_id: {eid}")
    lines.append(f"status: {status}")
    if deadline:
        lines.append(f"deadline: {deadline}")
    lines.append("tags:")
    lines.append("  - edital")
    lines.append("---\n")

    lines.append(f"# {emoji} {title}\n")

    if full and full.get("objective"):
        lines.append(f"> {full['objective']}\n")

    # Temas
    if card.get("themes"):
        lines.append("## Temas\n")
        for t in card["themes"]:
            lines.append(f"- [[{subfolder}/temas/{_slugify(t)}|{t}]]")
        lines.append("")

    # Tecnologias
    if card.get("technologies"):
        lines.append("## Tecnologias\n")
        for t in card["technologies"]:
            lines.append(f"- [[{subfolder}/tecnologias/{_slugify(t)}|{t}]]")
        lines.append("")

    # Aplicações (v2: Conceito/aplicacao — agora nota própria, não só texto)
    if full and full.get("aplicacoes"):
        lines.append("## Aplicações\n")
        for a in full["aplicacoes"]:
            lines.append(f"- 🎯 [[{subfolder}/aplicacoes/{_slugify(a)}|{a}]]")
        lines.append("")

    # Programas
    if card.get("programs"):
        lines.append("## Programas\n")
        for p in card["programs"]:
            lines.append(f"- 📋 [[{subfolder}/programas/{_slugify(p)}|{p}]]")
        lines.append("")

    # ICTs / Investidores (do full card → raw graph, Ator/kind)
    if full and full.get("icts"):
        lines.append("## ICTs\n")
        for i in full["icts"]:
            lines.append(f"- 🔬 [[{subfolder}/icts/{_slugify(i)}|{i}]]")
        lines.append("")

    if full and full.get("investidores"):
        lines.append("## Investidores\n")
        for inv in full["investidores"]:
            lines.append(f"- 💼 [[{subfolder}/investidores/{_slugify(inv)}|{inv}]]")
        lines.append("")

    # Mecanismo, Fonte, e backbone v2 (kind/aperture/macro_temas) como tabela
    lines.append("## Informações\n")
    lines.append("| Campo | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Status | {emoji} {status} |")
    if deadline:
        lines.append(f"| Prazo | {deadline} |")
    aperture = card.get("aperture") or (full or {}).get("aperture")
    if aperture:
        lines.append(f"| Regime | {_APERTURE_LABELS.get(aperture, aperture)} |")
    if full and full.get("mechanism"):
        mecanismos = [m.strip() for m in full["mechanism"].split(",") if m.strip()]
        mec_links = " | ".join(
            f"[[{subfolder}/mecanismos/{_slugify(m)}|{m}]]" for m in mecanismos
        )
        lines.append(f"| Mecanismo | {mec_links} |")
    if card.get("fonte_recurso"):
        fonte_links = " | ".join(
            f"[[{subfolder}/fontes/{_slugify(f)}|{f}]]" for f in card["fonte_recurso"]
        )
        lines.append(f"| Fonte | {fonte_links} |")
    macro_temas = card.get("macro_temas") or (full or {}).get("macro_temas")
    if macro_temas:
        lines.append(f"| Macro-temas | {', '.join(macro_temas)} |")
    if full and full.get("eligible_entities"):
        lines.append(f"| Público-alvo | {', '.join(full['eligible_entities'])} |")
    if full and full.get("exclusoes"):
        lines.append(f"| Exclusões | {', '.join(full['exclusoes'])} |")
    if full and full.get("value"):
        lines.append(f"| Valor | {full['value']} |")
    lines.append("")

    # Elegibilidade dura (constraints[], PR5) — avaliáveis contra o perfil,
    # distintas do texto residual de requisitos/exclusões abaixo.
    if full and full.get("constraints"):
        lines.append("## Elegibilidade (constraints)\n")
        for c in full["constraints"]:
            lines.append(_constraint_line(c))
        lines.append("")

    # Requisitos (texto residual, só informa — não é gate)
    if full and full.get("key_requirements"):
        reqs = [r for r in full["key_requirements"] if r]
        if reqs:
            lines.append("## Requisitos\n")
            for r in reqs:
                lines.append(f"- {r}")
            lines.append("")

    return "\n".join(lines)


def _entity_note(label: str, emoji: str, category_label: str, tag: str, editais: list[dict], subfolder: str) -> str:
    """Nota genérica para qualquer Ator/Conceito/Oportunidade(kind≠edital),
    e também para Mecanismo/Fonte (propriedades, sem nó próprio no grafo)."""
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append(f"  - {tag}")
    lines.append("---\n")
    lines.append(f"# {emoji} {category_label}: {label}\n")
    lines.append(f"**{len(editais)} editais** relacionados.\n")
    if editais:
        lines.append("## Editais\n")
        for c in editais:
            st_emoji = _status_emoji(c["status"])
            slug = _edital_slug(c["id"])
            lines.append(f"- {st_emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
        lines.append("")
    return "\n".join(lines)


def _home_note(
    total: int, by_status: dict,
    category_stats: list[tuple[str, str, str, int]],
    editais: list[dict], subfolder: str,
) -> str:
    """`category_stats`: [(folder, emoji, plural_label, count), ...] já sem zeros."""
    lines = ["---", "---\n"]
    lines.append("# 📡 Radar de Editais\n")
    lines.append("## Resumo\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Total de editais | {total} |")
    for s, count in sorted(by_status.items()):
        emoji = _status_emoji(s)
        lines.append(f"| {emoji} {s} | {count} |")
    for _folder, _emoji, plural_label, count in category_stats:
        lines.append(f"| {plural_label} | {count} |")
    lines.append("")
    lines.append("## Navegação\n")
    lines.append(f"- 📂 [[{subfolder}/editais/]] — todos os editais")
    for folder, emoji, plural_label, _count in category_stats:
        lines.append(f"- {emoji} [[{subfolder}/{folder}/]] — {plural_label.lower()}")
    lines.append("")
    lines.append("## Editais Abertos\n")
    for c in editais:
        if c["status"] == "ABERTA":
            slug = _edital_slug(c["id"])
            deadline = c.get("deadline", "—")
            lines.append(f"- 🟢 [[{subfolder}/editais/{slug}|{c['title']}]] — prazo: {deadline}")
    lines.append("")
    return "\n".join(lines)


# =============================================================================
# BUCKETIZAÇÃO — um único passe sobre TODOS os subgrafos (editais + catálogos)
# =============================================================================

def _bucket_key(node: dict) -> tuple[str, str] | None:
    """(type, kind/dim) do nó se ele tem pasta própria no vault, senão None."""
    t = node.get("type")
    if t == "Conceito":
        if node.get("origem") == "entidade_v1":
            return None  # ex-público-alvo, não é Conceito de conteúdo
        dim = node.get("dim")
        return ("Conceito", dim) if ("Conceito", dim) in _FOLDER_META else None
    if t == "Ator":
        kind = node.get("kind")
        return ("Ator", kind) if ("Ator", kind) in _FOLDER_META else None
    if t == "Oportunidade":
        kind = node.get("kind")
        if kind == "edital":
            return None  # tem nota própria (_edital_note)
        return ("Oportunidade", kind) if ("Oportunidade", kind) in _FOLDER_META else None
    return None


# =============================================================================
# EXPORTAÇÃO
# =============================================================================

def run(vault_path: Path, subfolder: str = "radar-editais") -> None:
    vault = vault_path.resolve()
    base = vault / subfolder

    cards = hypergraph_catalog.list_editais()
    full_cards: dict[str, dict] = {}
    for c in cards:
        try:
            full_cards[c["id"]] = hypergraph_catalog.get_edital(c["id"]) or {}
        except Exception:
            full_cards[c["id"]] = {}

    if not cards:
        print("Nenhum edital encontrado no catálogo.")
        return

    print(f"{len(cards)} editais carregados do hipergrado.")

    all_graphs = load_all_hypergraphs()
    fk_to_card: dict[str, dict] = {}
    for c in cards:
        src, _, native = c["id"].partition(":")
        fk_to_card[f"{src}__{native}"] = c

    # Mecanismos e Fontes: propriedades da Oportunidade (v2), não nós — seguem
    # derivadas do card/full, igual antes.
    mecanismos: dict[str, list[dict]] = {}
    fontes: dict[str, list[dict]] = {}
    for c in cards:
        for f in c.get("fonte_recurso", []):
            fontes.setdefault(f, []).append(c)
        full = full_cards.get(c["id"], {})
        for m in (full.get("mechanism") or "").split(","):
            m = m.strip()
            if m:
                mecanismos.setdefault(m, []).append(c)

    # Bucketização genérica: um passe sobre TODOS os subgrafos (editais dão o
    # backlink "## Editais"; catálogos puros — ict.json/investidores.json/
    # programas.json — preenchem entidades sem edital associado, hoje
    # descartadas pelo script antigo por comparar contra type-strings v1).
    buckets: dict[tuple[str, str], dict[str, list[dict]]] = {}
    for fk, g in all_graphs.items():
        card = fk_to_card.get(fk)  # None para grafos de catálogo puro
        for node in g.get("nodes", []):
            key = _bucket_key(node)
            if key is None:
                continue
            name = (node.get("name") or "").strip()
            if not name:
                continue
            lst = buckets.setdefault(key, {}).setdefault(name, [])
            if card is not None and card not in lst:
                lst.append(card)

    # Cria pastas (todas as conhecidas, mesmo sem dado no momento — cleanup
    # remove notas velhas de execuções anteriores em qualquer uma delas).
    pastas = {"editais", "mecanismos", "fontes"} | _ALL_NODE_FOLDERS
    for p in pastas:
        (base / p).mkdir(parents=True, exist_ok=True)
    for p in pastas:
        for f in (base / p).glob("*.md"):
            f.unlink()

    # Notas de editais
    for c in cards:
        slug = _edital_slug(c["id"])
        full = full_cards.get(c["id"])
        content = _edital_note(c, full, subfolder)
        (base / "editais" / f"{slug}.md").write_text(content, encoding="utf-8")
    print(f"  editais: {len(cards)} notas → {base}/editais/")

    # Notas de mecanismos e fontes (propriedades)
    for label, editais_list in mecanismos.items():
        content = _entity_note(label, "💰", "Mecanismo", "mecanismo", editais_list, subfolder)
        (base / "mecanismos" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  mecanismos: {len(mecanismos)} notas → {base}/mecanismos/")

    for label, editais_list in fontes.items():
        content = _entity_note(label, "💰", "Fonte", "fonte-recurso", editais_list, subfolder)
        (base / "fontes" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  fontes: {len(fontes)} notas → {base}/fontes/")

    # Notas de todos os buckets Ator/Conceito/Oportunidade(kind≠edital)
    # name_lower → Path — usado depois p/ resolver vizinhos e cross-links.
    name_to_path: dict[str, Path] = {}
    by_name_categories: dict[str, list[tuple[str, Path]]] = {}
    category_stats: list[tuple[str, str, str, int]] = []
    for (v2type, kind_or_dim), entities in sorted(buckets.items(), key=lambda kv: _FOLDER_META[kv[0]][0]):
        folder, emoji, singular, plural = _FOLDER_META[(v2type, kind_or_dim)]
        for name, editais_list in entities.items():
            content = _entity_note(name, emoji, singular, kind_or_dim, editais_list, subfolder)
            fp = base / folder / f"{_slugify(name)}.md"
            fp.write_text(content, encoding="utf-8")
            name_to_path.setdefault(name.lower(), fp)
            by_name_categories.setdefault(name.lower(), []).append((folder, fp))
        category_stats.append((folder, emoji, plural, len(entities)))
        print(f"  {folder}: {len(entities)} notas → {base}/{folder}/")

    # Também registra editais/mecanismos/fontes no índice de nomes p/ os
    # dois passes de wikilink abaixo.
    for c in cards:
        fp = base / "editais" / f"{_edital_slug(c['id'])}.md"
        name_to_path.setdefault(c["title"].lower(), fp)
        by_name_categories.setdefault(c["title"].lower(), []).append(("editais", fp))
    for label in mecanismos:
        fp = base / "mecanismos" / f"{_slugify(label)}.md"
        by_name_categories.setdefault(label.lower(), []).append(("mecanismos", fp))
    for label in fontes:
        fp = base / "fontes" / f"{_slugify(label)}.md"
        by_name_categories.setdefault(label.lower(), []).append(("fontes", fp))

    # ── Wikilinks cross-source: mesmo nome, categorias (pastas) diferentes ──
    xlinks = 0
    for _name_lower, entries in by_name_categories.items():
        if len(entries) < 2:
            continue
        for folder, fp in entries:
            others = [(f, p) for f, p in entries if f != folder]
            if not others or not fp.exists():
                continue
            content = fp.read_text(encoding="utf-8")
            if "## Conexões cross-source" in content:
                continue
            links = []
            for other_folder, other_fp in sorted(set(others)):
                rel = other_fp.relative_to(base)
                links.append(f"  - [[{rel.with_suffix('')}|{other_folder}]]")
            content += "\n## Conexões cross-source\n" + "\n".join(links) + "\n"
            fp.write_text(content, encoding="utf-8")
            xlinks += 1

    # ── Wikilinks via arestas nativas (v2: members são ids, resolvidos por nó) ──
    entity_idx = build_entity_index(all_graphs)
    edge_links = 0
    for (_etype, ename), occurrences in entity_idx.items():
        fp = name_to_path.get(ename.lower())
        if not fp or not fp.exists():
            continue
        content = fp.read_text(encoding="utf-8")
        if "## Conexões via arestas" in content:
            continue
        seed_id = next((n.get("id") for _fk, n in occurrences if n.get("id")), "")
        neighbors = _edge_neighbors(seed_id, occurrences, all_graphs)
        if not neighbors:
            continue
        nlinks = []
        for n in sorted(neighbors, key=str.lower):
            nfp = name_to_path.get(n.lower())
            if nfp:
                rel = nfp.relative_to(base)
                nlinks.append(f"  - [[{rel.with_suffix('')}]]")
        if nlinks:
            content += "\n## Conexões via arestas\n" + "\n".join(nlinks) + "\n"
            fp.write_text(content, encoding="utf-8")
            edge_links += 1

    total_xlinks = xlinks + edge_links
    if total_xlinks:
        print(f"  wikilinks cross-source: {total_xlinks} notas atualizadas")

    # HOME
    stats = hypergraph_catalog.get_stats()
    all_category_stats = [
        ("mecanismos", "💰", "Mecanismos", len(mecanismos)),
        ("fontes", "💰", "Fontes", len(fontes)),
        *category_stats,
    ]
    home = _home_note(
        stats.get("total_editais", len(cards)),
        stats.get("by_status", {}),
        [cs for cs in all_category_stats if cs[3]],
        cards, subfolder,
    )
    (base / "HOME.md").write_text(home, encoding="utf-8")
    total = 1 + len(cards) + len(mecanismos) + len(fontes) + sum(len(e) for e in buckets.values())

    print(f"\n✓ {total} notas exportadas para: {base}")
    print("\nPróximos passos no Obsidian:")
    print(f"  1. Abra o vault em: {vault}")
    print(f"  2. Navegue para: {subfolder}/HOME")
    print("  3. Graph View (Ctrl+G): filtre por #edital ou por fonte (#finep, #fapesp)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exporta hipergrado para Obsidian")
    parser.add_argument(
        "--vault",
        default=str(OBSIDIAN_VAULT_DIR),
        help=f"Caminho do vault Obsidian (default: {OBSIDIAN_VAULT_DIR})",
    )
    parser.add_argument(
        "--subfolder", default="radar-editais",
        help="Subpasta dentro do vault (default: radar-editais)",
    )
    args = parser.parse_args()
    run(Path(args.vault).expanduser(), args.subfolder)
