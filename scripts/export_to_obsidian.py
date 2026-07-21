"""Exporta o catálogo gold (v3) para um vault Obsidian.

Lê editais e entidades via `entity_catalog` (tabelas gold: `entities` +
`entity_relationships`, migration 036) e gera notas Markdown com [[wikilinks]]
para visualização no Graph View do Obsidian. Uso PESSOAL — nenhum consumidor no
app (o antigo `GET /graph` não existe mais); o cron do ETL diário regenera o
vault a partir do gold.

Modelo v3 (spec docs/specs/v3-unified.md): não há mais nós `Conceito` nem
hipergrafo. Setores/tecnologias são COLUNAS do edital (não nós); os
relacionamentos (operado_por/subordinado_a/exige_parceria_com) vêm das listas de
nome que `entity_catalog` já resolve por card. As pastas de tema/tecnologia/
programa/ICT/investidor/mecanismo/fonte são derivadas por índice reverso sobre os
campos dos editais + os catálogos autônomos (`list_entity_catalog`).

Uso:
    python scripts/export_to_obsidian.py
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault --subfolder radar-editais
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

from radar.core.config import OBSIDIAN_VAULT_DIR
from radar.core.kg import entity_catalog

# folder → (emoji, rótulo singular, rótulo plural, tag YAML). Plurais em PT-BR são
# irregulares demais p/ derivar do singular. Pastas de tema/tecnologia/aplicação
# vêm das colunas do edital; programas/ICTs/investidores dos catálogos gold.
_ENTITY_FOLDERS: dict[str, tuple[str, str, str, str]] = {
    "temas": ("🏷️", "Tema", "Temas", "tema"),
    "tecnologias": ("🔧", "Tecnologia", "Tecnologias", "tecnologia"),
    "programas": ("📋", "Programa", "Programas", "programa"),
    "icts": ("🔬", "ICT", "ICTs", "ict"),
    "investidores": ("💼", "Investidor", "Investidores", "investidor"),
    "mecanismos": ("💰", "Mecanismo", "Mecanismos", "mecanismo"),
    "fontes": ("🏛️", "Fonte", "Fontes", "fonte-recurso"),
}
# Catálogos autônomos (entidades gold que aparecem mesmo sem edital ligado) e que
# carregam seus próprios temas → ponte no Graph View (investidor↔tema).
_CATALOG_TO_FOLDER = {"programas": "programas", "ict": "icts", "investidores": "investidores"}
_THEMED_FOLDERS = frozenset({"programas", "icts", "investidores"})

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

    # Temas (setores)
    if card.get("themes"):
        lines.append("## Temas\n")
        for t in card["themes"]:
            lines.append(f"- [[{subfolder}/temas/{_slugify(t)}|{t}]]")
        lines.append("")

    # Tecnologias (tecnologias_tags)
    if card.get("technologies"):
        lines.append("## Tecnologias\n")
        for t in card["technologies"]:
            lines.append(f"- [[{subfolder}/tecnologias/{_slugify(t)}|{t}]]")
        lines.append("")

    # Programas (subordinado_a)
    if card.get("programs"):
        lines.append("## Programas\n")
        for p in card["programs"]:
            lines.append(f"- 📋 [[{subfolder}/programas/{_slugify(p)}|{p}]]")
        lines.append("")

    # ICTs (exige_parceria_com)
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

    # Backbone (mecanismo/fonte/regime) como tabela
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
    if full and full.get("eligible_entities"):
        lines.append(f"| Público-alvo | {', '.join(full['eligible_entities'])} |")
    if full and full.get("exclusoes"):
        lines.append(f"| Exclusões | {', '.join(full['exclusoes'])} |")
    if full and full.get("value"):
        lines.append(f"| Valor | {full['value']} |")
    lines.append("")

    # Elegibilidade dura (constraints[], PR5) — avaliáveis contra o perfil,
    # distintas do texto residual de requisitos/exclusões.
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


def _entity_note(
    label: str, emoji: str, category_label: str, tag: str,
    editais: list[dict], subfolder: str, *, themes: list[str] | None = None,
) -> str:
    """Nota genérica para qualquer entidade de folder (tema/tecnologia/programa/
    ICT/investidor/mecanismo/fonte). `themes` (opcional) liga catálogos autônomos
    aos seus temas — a ponte investidor↔tema no Graph View."""
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(label)}"')
    lines.append("tags:")
    lines.append(f"  - {tag}")
    lines.append("---\n")
    lines.append(f"# {emoji} {category_label}: {label}\n")
    lines.append(f"**{len(editais)} editais** relacionados.\n")
    if themes:
        lines.append("## Temas\n")
        for t in themes:
            lines.append(f"- 🏷️ [[{subfolder}/temas/{_slugify(t)}|{t}]]")
        lines.append("")
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
# EXPORTAÇÃO
# =============================================================================

def run(vault_path: Path, subfolder: str = "radar-editais") -> None:
    vault = vault_path.resolve()
    base = vault / subfolder

    cards = entity_catalog.list_editais(limit=10_000)
    full_cards: dict[str, dict] = {}
    for c in cards:
        try:
            full_cards[c["id"]] = entity_catalog.get_edital(c["id"]) or {}
        except Exception:
            full_cards[c["id"]] = {}

    if not cards:
        print("Nenhum edital encontrado no catálogo gold.")
        return

    print(f"{len(cards)} editais carregados do catálogo gold.")

    # Índice reverso: folder → {nome: {"editais": [cards], "themes": set()}}.
    buckets: dict[str, dict[str, dict]] = {f: {} for f in _ENTITY_FOLDERS}

    def _add(folder: str, name: str, card: dict | None = None, themes=None) -> None:
        name = (name or "").strip()
        if not name:
            return
        b = buckets[folder].setdefault(name, {"editais": [], "themes": set()})
        if card is not None and card not in b["editais"]:
            b["editais"].append(card)
        if themes:
            b["themes"].update(themes)

    for c in cards:
        full = full_cards.get(c["id"], {})
        for t in c.get("themes", []):
            _add("temas", t, c)
        for t in c.get("technologies", []):
            _add("tecnologias", t, c)
        for p in c.get("programs", []):
            _add("programas", p, c)
        for i in full.get("icts", []):
            _add("icts", i, c)
        for inv in full.get("investidores", []):
            _add("investidores", inv, c)
        for m in (full.get("mechanism") or "").split(","):
            _add("mecanismos", m.strip(), c)
        for f in c.get("fonte_recurso", []):
            _add("fontes", f, c)

    # Catálogos autônomos: entidades gold sem edital ligado entram na pasta com
    # seus temas (ponte no Graph View). Falha de leitura não derruba o export.
    for catalog_key, folder in _CATALOG_TO_FOLDER.items():
        try:
            entities = entity_catalog.list_entity_catalog(catalog_key, limit=10_000)
        except Exception:
            entities = []
        for e in entities:
            _add(folder, e.get("name", ""), themes=e.get("themes"))

    # Cria pastas conhecidas e limpa notas de runs anteriores.
    pastas = ["editais", *_ENTITY_FOLDERS]
    for p in pastas:
        (base / p).mkdir(parents=True, exist_ok=True)
        for f in (base / p).glob("*.md"):
            f.unlink()

    # Notas de editais.
    for c in cards:
        content = _edital_note(c, full_cards.get(c["id"]), subfolder)
        (base / "editais" / f"{_edital_slug(c['id'])}.md").write_text(content, encoding="utf-8")
    print(f"  editais: {len(cards)} notas → {base}/editais/")

    # Notas de entidades + índice de nomes p/ cross-links.
    name_to_categories: dict[str, list[tuple[str, Path]]] = {}
    category_stats: list[tuple[str, str, str, int]] = []
    for folder, (emoji, singular, plural, tag) in _ENTITY_FOLDERS.items():
        entries = buckets[folder]
        for name, data in sorted(entries.items()):
            themes = sorted(data["themes"]) if folder in _THEMED_FOLDERS else None
            content = _entity_note(name, emoji, singular, tag, data["editais"], subfolder, themes=themes)
            fp = base / folder / f"{_slugify(name)}.md"
            fp.write_text(content, encoding="utf-8")
            name_to_categories.setdefault(name.lower(), []).append((folder, fp))
        if entries:
            category_stats.append((folder, emoji, plural, len(entries)))
        print(f"  {folder}: {len(entries)} notas → {base}/{folder}/")

    for c in cards:
        fp = base / "editais" / f"{_edital_slug(c['id'])}.md"
        name_to_categories.setdefault(c["title"].lower(), []).append(("editais", fp))

    # Wikilinks cross-source: mesmo nome em pastas diferentes (ex.: uma agência que
    # é fonte e opera um programa homônimo).
    xlinks = 0
    for _name_lower, entries in name_to_categories.items():
        if len(entries) < 2:
            continue
        for folder, fp in entries:
            others = sorted({(f, p) for f, p in entries if f != folder})
            if not others or not fp.exists():
                continue
            content = fp.read_text(encoding="utf-8")
            if "## Conexões cross-source" in content:
                continue
            links = [f"  - [[{p.relative_to(base).with_suffix('')}|{f}]]" for f, p in others]
            fp.write_text(
                content + "\n## Conexões cross-source\n" + "\n".join(links) + "\n",
                encoding="utf-8",
            )
            xlinks += 1
    if xlinks:
        print(f"  wikilinks cross-source: {xlinks} notas atualizadas")

    # HOME.
    stats = entity_catalog.get_stats()
    home = _home_note(
        stats.get("total_editais", len(cards)),
        stats.get("by_status", {}),
        category_stats,
        cards, subfolder,
    )
    (base / "HOME.md").write_text(home, encoding="utf-8")
    total = 1 + len(cards) + sum(len(b) for b in buckets.values())

    print(f"\n✓ {total} notas exportadas para: {base}")
    print("\nPróximos passos no Obsidian:")
    print(f"  1. Abra o vault em: {vault}")
    print(f"  2. Navegue para: {subfolder}/HOME")
    print("  3. Graph View (Ctrl+G): filtre por #edital ou por fonte")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exporta catálogo gold para Obsidian")
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
