"""Exporta o KG do spike (schema `kg_spike`) para um vault Obsidian.

Lê nodes/quality_nodes/edges/communities via `radar.core.kg.spike.graph_store`
e gera notas Markdown com [[wikilinks]] tipados — a topologia ESTRUTURA-CONSCIENTE
(nós substância + qualidade, arestas tipadas com peso, comunidades Louvain).

OBS: é um vault SEPARADO (`data/kg_spike_vault/`) — o export do gold
(`scripts/export_to_obsidian.py`, `data/hyper_extract_output_v2/vault/`) não é
tocado. Uso PESSOAL de diagnóstico; nenhum consumidor no app.

Uso:
    DATABASE_URL=... python scripts/export_to_obsidian_spike.py
    python scripts/export_to_obsidian_spike.py --vault ~/Documents/Obsidian/MeuVault
    python scripts/export_to_obsidian_spike.py --subfolder kg-spike
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path
from typing import Any

from radar.core.config import ROOT
from radar.core.kg.spike import graph_store

# kind/family → (emoji, rótulo singular, rótulo plural, pasta, tag YAML)
_FOLDER_BY_KIND: dict[str, tuple[str, str, str, str]] = {
    "edital": ("📜", "Edital", "Editais", "editais"),
    "ict": ("🔬", "ICT", "ICTs", "icts"),
    "investidor": ("💼", "Investidor", "Investidores", "investidores"),
    "programa": ("📋", "Programa", "Programas", "programas"),
    "agencia": ("🏛️", "Agência", "Agências", "agencias"),
}
_FOLDER_BY_FAMILY: dict[str, tuple[str, str, str, str]] = {
    "setor": ("🏷️", "Setor", "Setores", "setores"),
    "tecnologia": ("🔧", "Tecnologia", "Tecnologias", "tecnologias"),
    "estagio": ("🪜", "Estágio", "Estágios", "estagios"),
    "uf": ("📍", "UF", "UFs", "ufs"),
    "mecanismo": ("💰", "Mecanismo", "Mecanismos", "mecanismos"),
    "faixa_trl": ("🧪", "Faixa TRL", "Faixas TRL", "faixas-trl"),
}

_EDGE_LABELS = {
    "tem_setor": "atua no setor",
    "tem_tecnologia": "domina tecnologia",
    "busca_estagio": "busca estágio",
    "tem_uf": "UF",
    "usa_mecanismo": "usa mecanismo",
    "tem_trl_faixa": "TRL",
    "credenciada_por": "credenciada por",
    "operado_por": "operado por",
    "subordinado_a": "subordinado a",
    "similar_a": "similar a",
}


def _slugify(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s.lower())
    return re.sub(r"[-\s]+", "-", s.strip("-"))


def _safe_yaml(text: str) -> str:
    return text.replace('"', '\\"')


def _node_folder(node: dict[str, Any]) -> tuple[str, str, str]:
    """(pasta, emoji, rótulo) para um nó de substância."""
    kind = node.get("kind", "node")
    emoji, _s, _p, folder = _FOLDER_BY_KIND.get(kind, ("🧩", "Nó", "Nós", "nos"))
    return folder, emoji, kind


def _quality_folder(q: dict[str, Any]) -> tuple[str, str, str]:
    family = q.get("family", "qualidade")
    emoji, _s, _p, folder = _FOLDER_BY_FAMILY.get(family, ("🧩", "Qualidade", "Qualidades", "qualidades"))
    return folder, emoji, family


def _edge_label(etype: str) -> str:
    return _EDGE_LABELS.get(etype, etype.replace("_", " "))


def _node_note(node: dict[str, Any], outgoing: list[dict[str, Any]], incoming: list[dict[str, Any]], subfolder: str) -> str:
    folder, emoji, kind = _node_folder(node)
    title = node.get("name") or node["id"]
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(title)}"')
    lines.append(f"kind: {kind}")
    lines.append("tags:")
    lines.append("  - substancia")
    lines.append("---\n")
    lines.append(f"# {emoji} {title}\n")
    if node.get("description"):
        lines.append(f"> {node['description']}\n")
    lines.append(f"**id:** `{node['id']}`  \n**native_id:** `{node.get('native_id', '')}`\n")

    if outgoing:
        lines.append("## → Ligações (out)\n")
        for e in sorted(outgoing, key=lambda x: (x["type"], x["target_id"])):
            target = _wiki_link(e["target_id"], subfolder)
            lines.append(f"- {_edge_label(e['type'])} → {target}")
        lines.append("")
    if incoming:
        lines.append("## ← Ligações (in)\n")
        for e in sorted(incoming, key=lambda x: (x["type"], x["source_id"])):
            source = _wiki_link(e["source_id"], subfolder)
            lines.append(f"- {_edge_label(e['type'])} ← {source}")
        lines.append("")
    return "\n".join(lines)


def _quality_note(q: dict[str, Any], edges: list[dict[str, Any]], subfolder: str) -> str:
    family = q.get("family", "qualidade")
    _f, emoji, _t = _quality_folder(q)
    title = q.get("value") or q["id"]
    lines = ["---"]
    lines.append(f'title: "{_safe_yaml(title)}"')
    lines.append(f"family: {family}")
    lines.append("tags:")
    lines.append("  - qualidade")
    lines.append("---\n")
    lines.append(f"# {emoji} {family}: {title}\n")
    if edges:
        lines.append("## Entidades ligadas\n")
        for e in sorted(edges, key=lambda x: x["source_id"]):
            src = _wiki_link(e["source_id"], subfolder)
            lines.append(f"- {src} ({_edge_label(e['type'])})")
        lines.append("")
    return "\n".join(lines)


def _wiki_link(node_id: str, subfolder: str) -> str:
    """Wikilink resolvendo o destino via mapas de nós/qualidade (preenchidos no run)."""
    dest = _WIKI_TARGET.get(node_id)
    if dest:
        folder, emoji, label, path = dest
        return f"[[{subfolder}/{folder}/{_slugify(path)}|{emoji} {label}]]"
    return f"`{node_id}`"


def _community_note(cid: str, member_ids: list[str], nodes: list[dict[str, Any]], quality: list[dict[str, Any]], subfolder: str) -> str:
    by_id = {n["id"]: n for n in [*nodes, *quality]}
    lines = ["---"]
    lines.append(f'title: "Comunidade {cid}"')
    lines.append("tags:")
    lines.append("  - comunidade")
    lines.append("---\n")
    lines.append(f"# 🧭 Comunidade {cid}\n")
    lines.append(f"**{len(member_ids)} membros**\n")
    for mid in member_ids:
        n = by_id.get(mid)
        if n:
            kind = n.get("kind") or n.get("family") or "node"
            lines.append(f"- {_wiki_link(mid, subfolder)} ({kind})")
        else:
            lines.append(f"- `{mid}`")
    lines.append("")
    return "\n".join(lines)


_WIKI_TARGET: dict[str, tuple[str, str, str, str]] = {}


def run(vault_path: Path, subfolder: str = "kg-spike") -> None:
    nodes = graph_store.load_nodes()
    quality = graph_store.load_quality_nodes()
    edges = graph_store.load_edges()
    communities = graph_store.load_communities()

    if not nodes:
        print("kg_spike vazio — rode `python -m radar.core.kg.spike.ingest` primeiro.")
        return

    base = vault_path.resolve() / subfolder
    folders = {f[3] for f in _FOLDER_BY_KIND.values()} | {f[3] for f in _FOLDER_BY_FAMILY.values()}
    for folder in folders:
        (base / folder).mkdir(parents=True, exist_ok=True)
        for f in (base / folder).glob("*.md"):
            f.unlink()

    # Índice de destino para wikilinks: id → (pasta, emoji, label, filename).
    for n in nodes:
        folder, emoji, kind = _node_folder(n)
        label = n.get("name") or n["id"]
        _WIKI_TARGET[n["id"]] = (folder, emoji, label, _slugify(label))
    for q in quality:
        folder, emoji, family = _quality_folder(q)
        label = q.get("value") or q["id"]
        _WIKI_TARGET[q["id"]] = (folder, emoji, label, _slugify(label))

    out_edges: dict[str, list[dict[str, Any]]] = {}
    in_edges: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        out_edges.setdefault(e["source_id"], []).append(e)
        in_edges.setdefault(e["target_id"], []).append(e)

    n_notes = 0
    for n in nodes:
        folder, _emoji, _kind = _node_folder(n)
        path = base / folder / f"{_slugify(n.get('name') or n['id'])}.md"
        path.write_text(_node_note(n, out_edges.get(n["id"], []), in_edges.get(n["id"], []), subfolder), encoding="utf-8")
        n_notes += 1

    q_notes = 0
    for q in quality:
        folder, _emoji, _kind = _quality_folder(q)
        path = base / folder / f"{_slugify(q.get('value') or q['id'])}.md"
        path.write_text(_quality_note(q, in_edges.get(q["id"], []), subfolder), encoding="utf-8")
        q_notes += 1

    c_notes = 0
    cdir = base / "comunidades"
    cdir.mkdir(parents=True, exist_ok=True)
    for f in cdir.glob("*.md"):
        f.unlink()
    for cid, members in communities.items():
        (cdir / f"{_slugify(cid)}.md").write_text(
            _community_note(cid, members, nodes, quality, subfolder), encoding="utf-8"
        )
        c_notes += 1

    lines = ["---", "---\n", "# 🕸️ Radar KG — spike estrutura-consciente\n"]
    lines.append("## Resumo\n")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Substâncias | {n_notes} |")
    lines.append(f"| Qualidades | {q_notes} |")
    lines.append(f"| Arestas | {len(edges)} |")
    lines.append(f"| Comunidades | {c_notes} |")
    lines.append("")
    lines.append("## Navegação\n")
    for folder in sorted(set(f[3] for f in _FOLDER_BY_KIND.values()) | set(f[3] for f in _FOLDER_BY_FAMILY.values())):
        lines.append(f"- 📂 [[{subfolder}/{folder}/]] — {folder}")
    lines.append(f"- 🧭 [[{subfolder}/comunidades/]] — comunidades Louvain")
    lines.append("")
    (base / "HOME.md").write_text("\n".join(lines), encoding="utf-8")

    total = 1 + n_notes + q_notes + c_notes
    print(f"\n✓ {total} notas exportadas para: {base}")
    print("  - substâncias:", n_notes)
    print("  - qualidades:", q_notes)
    print("  - comunidades:", c_notes)
    print("\nPróximos passos no Obsidian:")
    print(f"  1. Abra o vault em: {vault_path.resolve()}")
    print(f"  2. Navegue para: {subfolder}/HOME")
    print("  3. Graph View (Ctrl+G) para ver a topologia (arestas tipadas + comunidades)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exporta o KG do spike para Obsidian")
    parser.add_argument(
        "--vault",
        default=str(ROOT / "data" / "kg_spike_vault"),
        help=f"Caminho do vault Obsidian (default: {ROOT / 'data' / 'kg_spike_vault'})",
    )
    parser.add_argument(
        "--subfolder", default="kg-spike",
        help="Subpasta dentro do vault (default: kg-spike)",
    )
    args = parser.parse_args()
    run(Path(args.vault).expanduser(), args.subfolder)
