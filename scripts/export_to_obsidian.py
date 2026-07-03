"""Exporta o hipergrado (hipergrafos N-ários) para um vault Obsidian.

Lê todos os editais via `hypergraph_catalog` e gera notas Markdown com
[[wikilinks]] para visualização no Graph View do Obsidian.

Uso:
    python scripts/export_to_obsidian.py
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault --subfolder radar-editais

Estrutura no vault:
    radar-editais/
    ├── HOME.md
    ├── editais/         → uma nota por edital
    ├── temas/           → uma nota por Tema
    ├── tecnologias/     → uma nota por Tecnologia
    ├── mecanismos/      → uma nota por Mecanismo
    └── fontes/          → uma nota por Fonte de recurso
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


def _edge_neighbors(
    ename: str,
    occurrences: list[tuple[str, dict]],
    all_graphs: dict[str, dict],
) -> set[str]:
    """Nomes (case original) dos outros membros de arestas onde `ename` (já
    lowercase, vindo do entity_idx) de fato participa, nos grafos onde a
    entidade ocorre. Ignora arestas em que `ename` não é membro."""
    neighbors: set[str] = set()
    for fk, _node in occurrences:
        g = all_graphs.get(fk)
        if not g:
            continue
        for edge in g.get("edges", []):
            members = [m for m in edge.get("members", []) if isinstance(m, str) and m.strip()]
            if ename not in {m.lower() for m in members}:
                continue
            for m in members:
                if m.lower() != ename:
                    neighbors.add(m)
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

    # Programas
    if card.get("programs"):
        lines.append("## Programas\n")
        for p in card["programs"]:
            lines.append(f"- 📋 [[{subfolder}/programas/{_slugify(p)}|{p}]]")
        lines.append("")

    # ICTs (do full card → raw graph)
    if full and full.get("icts"):
        lines.append("## ICTs\n")
        for i in full["icts"]:
            lines.append(f"- 🔬 [[{subfolder}/icts/{_slugify(i)}|{i}]]")
        lines.append("")

    # Investidores (do full card → raw graph)
    if full and full.get("investidores"):
        lines.append("## Investidores\n")
        for inv in full["investidores"]:
            lines.append(f"- 💼 [[{subfolder}/investidores/{_slugify(inv)}|{inv}]]")
        lines.append("")

    # Mecanismo e Fontes como tags inline
    lines.append("## Informações\n")
    lines.append("| Campo | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Status | {emoji} {status} |")
    if deadline:
        lines.append(f"| Prazo | {deadline} |")
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
    if full and full.get("eligible_entities"):
        lines.append(f"| Público-alvo | {', '.join(full['eligible_entities'])} |")
    if full and full.get("exclusoes"):
        lines.append(f"| Exclusões | {', '.join(full['exclusoes'])} |")
    if full and full.get("value"):
        lines.append(f"| Valor | {full['value']} |")
    lines.append("")

    # Requisitos
    if full and full.get("key_requirements"):
        reqs = [r for r in full["key_requirements"] if r]
        if reqs:
            lines.append("## Requisitos\n")
            for r in reqs:
                lines.append(f"- {r}")
            lines.append("")

    # Aplicações
    if full and full.get("aplicacoes"):
        lines.append("## Aplicações\n")
        for a in full["aplicacoes"]:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines)


def _tema_note(label: str, editais: list[dict], subfolder: str) -> str:
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append("  - tema")
    lines.append("---\n")
    lines.append(f"# 🏷️ Tema: {label}\n")
    lines.append(f"**{len(editais)} editais** relacionados.\n")
    lines.append("## Editais\n")
    for c in editais:
        emoji = _status_emoji(c["status"])
        slug = _edital_slug(c["id"])
        lines.append(f"- {emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
    lines.append("")
    return "\n".join(lines)


def _tecnologia_note(label: str, editais: list[dict], subfolder: str) -> str:
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append("  - tecnologia")
    lines.append("---\n")
    lines.append(f"# 🔧 Tecnologia: {label}\n")
    lines.append(f"**{len(editais)} editais** relacionados.\n")
    lines.append("## Editais\n")
    for c in editais:
        emoji = _status_emoji(c["status"])
        slug = _edital_slug(c["id"])
        lines.append(f"- {emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
    lines.append("")
    return "\n".join(lines)


def _mecanismo_note(label: str, editais: list[dict], subfolder: str) -> str:
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append("  - mecanismo")
    lines.append("---\n")
    lines.append(f"# 💰 Mecanismo: {label}\n")
    lines.append(f"**{len(editais)} editais** com este mecanismo.\n")
    lines.append("## Editais\n")
    for c in editais:
        emoji = _status_emoji(c["status"])
        slug = _edital_slug(c["id"])
        lines.append(f"- {emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
    lines.append("")
    return "\n".join(lines)


def _fonte_note(label: str, editais: list[dict], subfolder: str) -> str:
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append("  - fonte-recurso")
    lines.append("---\n")
    lines.append(f"# 💰 Fonte: {label}\n")
    lines.append(f"**{len(editais)} editais** financiados.\n")
    lines.append("## Editais\n")
    for c in editais:
        emoji = _status_emoji(c["status"])
        slug = _edital_slug(c["id"])
        lines.append(f"- {emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
    lines.append("")
    return "\n".join(lines)


def _programa_note(label: str, editais: list[dict], subfolder: str) -> str:
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append("  - programa")
    lines.append("---\n")
    lines.append(f"# 📋 Programa: {label}\n")
    lines.append(f"**{len(editais)} editais** vinculados.\n")
    lines.append("## Editais\n")
    for c in editais:
        emoji = _status_emoji(c["status"])
        slug = _edital_slug(c["id"])
        lines.append(f"- {emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
    lines.append("")
    return "\n".join(lines)


def _ict_note(label: str, editais: list[dict], subfolder: str) -> str:
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append("  - ict")
    lines.append("---\n")
    lines.append(f"# 🔬 ICT: {label}\n")
    lines.append(f"**{len(editais)} editais** com participação.\n")
    lines.append("## Editais\n")
    for c in editais:
        emoji = _status_emoji(c["status"])
        slug = _edital_slug(c["id"])
        lines.append(f"- {emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
    lines.append("")
    return "\n".join(lines)


def _investidor_note(label: str, editais: list[dict], subfolder: str) -> str:
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append("  - investidor")
    lines.append("---\n")
    lines.append(f"# 💼 Investidor: {label}\n")
    lines.append(f"**{len(editais)} editais** com participação.\n")
    lines.append("## Editais\n")
    for c in editais:
        emoji = _status_emoji(c["status"])
        slug = _edital_slug(c["id"])
        lines.append(f"- {emoji} [[{subfolder}/editais/{slug}|{c['title']}]]")
    lines.append("")
    return "\n".join(lines)


def _home_note(
    total: int, by_status: dict, n_temas: int, n_tecnologias: int,
    n_mecanismos: int, n_fontes: int, n_programas: int, n_icts: int,
    n_investidores: int, editais: list[dict], subfolder: str,
) -> str:
    lines = ["---"]
    lines.append("---\n")
    lines.append("# 📡 Radar de Editais\n")
    lines.append("## Resumo\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Total de editais | {total} |")
    for s, count in sorted(by_status.items()):
        emoji = _status_emoji(s)
        lines.append(f"| {emoji} {s} | {count} |")
    lines.append(f"| Temas | {n_temas} |")
    lines.append(f"| Tecnologias | {n_tecnologias} |")
    lines.append(f"| Mecanismos | {n_mecanismos} |")
    lines.append(f"| Fontes | {n_fontes} |")
    lines.append(f"| Programas | {n_programas} |")
    lines.append(f"| ICTs | {n_icts} |")
    lines.append(f"| Investidores | {n_investidores} |")
    lines.append("")
    lines.append("## Navegação\n")
    lines.append(f"- 📂 [[{subfolder}/editais/]] — todos os editais")
    lines.append(f"- 🏷️ [[{subfolder}/temas/]] — por tema")
    lines.append(f"- 🔧 [[{subfolder}/tecnologias/]] — por tecnologia")
    lines.append(f"- 💰 [[{subfolder}/mecanismos/]] — por mecanismo")
    lines.append(f"- 💰 [[{subfolder}/fontes/]] — por fonte de recurso")
    lines.append(f"- 📋 [[{subfolder}/programas/]] — por programa")
    lines.append(f"- 🔬 [[{subfolder}/icts/]] — ICTs")
    lines.append(f"- 💼 [[{subfolder}/investidores/]] — investidores")
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
# EXPORTAÇÃO
# =============================================================================

def run(vault_path: Path, subfolder: str = "radar-editais") -> None:
    vault = vault_path.resolve()
    base = vault / subfolder

    # Carrega catálogo
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

    # Carrega hipergrafos brutos para extrair ICT/Investidor/Programa
    all_graphs = load_all_hypergraphs()
    card_to_graph: dict[str, dict] = {}
    for c in cards:
        src, _, native = c["id"].partition(":")
        fk = f"{src}__{native}"
        card_to_graph[c["id"]] = all_graphs.get(fk, {})

    # Prepara índices
    temas: dict[str, list[dict]] = {}
    tecnologias: dict[str, list[dict]] = {}
    mecanismos: dict[str, list[dict]] = {}
    fontes: dict[str, list[dict]] = {}
    programas: dict[str, list[dict]] = {}
    icts: dict[str, list[dict]] = {}
    investidores: dict[str, list[dict]] = {}

    for c in cards:
        for t in c.get("themes", []):
            temas.setdefault(t, []).append(c)
        for t in c.get("technologies", []):
            tecnologias.setdefault(t, []).append(c)
        for f in c.get("fonte_recurso", []):
            fontes.setdefault(f, []).append(c)
        full = full_cards.get(c["id"], {})
        mechanic = full.get("mechanism", "")
        for m in mechanic.split(","):
            m = m.strip()
            if m:
                mecanismos.setdefault(m, []).append(c)
        for p in c.get("programs", []):
            programas.setdefault(p, []).append(c)
        g = card_to_graph.get(c["id"], {})
        for node in g.get("nodes", []):
            nt = node.get("type")
            nm = node.get("name")
            if nt == "ICT" and nm:
                icts.setdefault(nm, []).append(c)
            elif nt == "Investidor" and nm:
                investidores.setdefault(nm, []).append(c)

    # ── Catálogo — nós de todos os tipos não cobertos por editais ──
    def _ensure_node(name: str, nt: str, cat_index: dict, all_index: dict):
        """Adiciona nó ao índice correto se ainda não existir."""
        index_map = {
            "ICT": ("icts", icts),
            "Investidor": ("investidores", investidores),
            "Programa": ("programas", programas),
            "Tema": ("temas", temas),
            "Tecnologia": ("tecnologias", tecnologias),
            "Mecanismo": ("mecanismos", mecanismos),
            "Fonte": ("fontes", fontes),
            "Aplicação": None,  # não exportado ainda
        }
        entry = index_map.get(nt)
        if entry is None:
            return
        _dir, idx = entry
        if name not in idx:
            idx[name] = []

    for fk, g in all_graphs.items():
        if fk.startswith(("finep__", "fapesp__", "fapesc__")):
            continue  # já processado via editais
        for node in g.get("nodes", []):
            nm = (node.get("name") or "").strip()
            nt = node.get("type")
            if nm and nt:
                _ensure_node(nm, nt, None, None)

    # Cria pastas
    pastas = {"editais", "temas", "tecnologias", "mecanismos", "fontes",
              "programas", "icts", "investidores"}
    for p in pastas:
        (base / p).mkdir(parents=True, exist_ok=True)

    # Remove notas antigas
    for p in pastas:
        folder = base / p
        for f in folder.glob("*.md"):
            f.unlink()

    # Notas de editais
    for c in cards:
        slug = _edital_slug(c["id"])
        full = full_cards.get(c["id"])
        content = _edital_note(c, full, subfolder)
        (base / "editais" / f"{slug}.md").write_text(content, encoding="utf-8")
    print(f"  editais: {len(cards)} notas → {base}/editais/")

    # Notas de temas
    for label, editais_list in temas.items():
        content = _tema_note(label, editais_list, subfolder)
        (base / "temas" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  temas: {len(temas)} notas → {base}/temas/")

    # Notas de tecnologias
    for label, editais_list in tecnologias.items():
        content = _tecnologia_note(label, editais_list, subfolder)
        (base / "tecnologias" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  tecnologias: {len(tecnologias)} notas → {base}/tecnologias/")

    # Notas de mecanismos
    for label, editais_list in mecanismos.items():
        content = _mecanismo_note(label, editais_list, subfolder)
        (base / "mecanismos" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  mecanismos: {len(mecanismos)} notas → {base}/mecanismos/")

    # Notas de fontes
    for label, editais_list in fontes.items():
        content = _fonte_note(label, editais_list, subfolder)
        (base / "fontes" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  fontes: {len(fontes)} notas → {base}/fontes/")

    # Notas de programas
    for label, editais_list in programas.items():
        content = _programa_note(label, editais_list, subfolder)
        (base / "programas" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  programas: {len(programas)} notas → {base}/programas/")

    # Notas de ICTs
    for label, editais_list in icts.items():
        content = _ict_note(label, editais_list, subfolder)
        (base / "icts" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  icts: {len(icts)} notas → {base}/icts/")

    # Notas de investidores
    for label, editais_list in investidores.items():
        content = _investidor_note(label, editais_list, subfolder)
        (base / "investidores" / f"{_slugify(label)}.md").write_text(content, encoding="utf-8")
    print(f"  investidores: {len(investidores)} notas → {base}/investidores/")

    # ── Wikilinks cross-source: entidades mesmo nome, tipo diferente ──
    t_dir = {"tema": "temas", "tecnologia": "tecnologias", "mecanismo": "mecanismos",
             "fonte": "fontes", "programa": "programas", "ict": "icts",
             "investidor": "investidores", "edital": "editais"}
    vault_path: dict[tuple[str, str], Path] = {}
    for cat_list, cat_dir in [(temas, "temas"), (tecnologias, "tecnologias"),
                               (mecanismos, "mecanismos"), (fontes, "fontes"),
                               (programas, "programas"), (icts, "icts"),
                               (investidores, "investidores")]:
        t_lower = {v: k for k, v in t_dir.items()}[cat_dir]
        for label in cat_list:
            vault_path[(t_lower, label.lower())] = base / cat_dir / f"{_slugify(label)}.md"

    entity_idx = build_entity_index(all_graphs)
    by_name: dict[str, list[dict]] = {}
    for (etype, ename) in entity_idx:
        if etype not in t_dir:
            continue
        fp = vault_path.get((etype, ename.lower()))
        if fp and fp.exists():
            by_name.setdefault(ename.lower(), []).append({"type": etype, "path": fp})

    xlinks = 0
    for _ename_lower, entries in by_name.items():
        if len(entries) < 2:
            continue
        for entry in entries:
            others = [e for e in entries if e["type"] != entry["type"]]
            if not others:
                continue
            content = entry["path"].read_text(encoding="utf-8")
            if "## Conexões cross-source" in content:
                continue
            links = []
            for other in sorted(others, key=lambda x: x["type"]):
                rel = other["path"].relative_to(base)
                links.append(f"  - [[{rel.with_suffix('')}|{other['type']}]]")
            content += "\n## Conexões cross-source\n" + "\n".join(links) + "\n"
            entry["path"].write_text(content, encoding="utf-8")
            xlinks += 1

    # ── Wikilinks via arestas ── qualquer entidade com nota própria (edital ou
    # catálogo puro, ex. investidores/ICTs curados via hypergraphs/*.json) pode
    # ganhar links a partir das arestas do(s) grafo(s) onde aparece — sem isso,
    # investidor/ICT sem participação em edital fica órfão do tema que financia.
    edge_links = 0
    name_to_path: dict[str, Path] = {}
    for (_etype, ename), fp in vault_path.items():
        name_to_path.setdefault(ename.lower(), fp)
    for (etype, ename), occurrences in entity_idx.items():
        fp = vault_path.get((etype, ename.lower()))
        if not fp or not fp.exists():
            continue
        content = fp.read_text(encoding="utf-8")
        if "## Conexões via arestas" in content:
            continue
        neighbors = _edge_neighbors(ename, occurrences, all_graphs)
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
    home = _home_note(
        stats.get("total_editais", len(cards)),
        stats.get("by_status", {}),
        len(temas), len(tecnologias), len(mecanismos), len(fontes),
        len(programas), len(icts), len(investidores),
        cards, subfolder,
    )
    (base / "HOME.md").write_text(home, encoding="utf-8")
    total = (1 + len(cards) + len(temas) + len(tecnologias) + len(mecanismos)
             + len(fontes) + len(programas) + len(icts) + len(investidores))

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
