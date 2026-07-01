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
