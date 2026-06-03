"""
Exporta o Knowledge Graph FINEP para um vault Obsidian.

Cada nó vira uma nota Markdown com frontmatter YAML e [[wikilinks]].
O Graph View nativo do Obsidian mostra as conexões automaticamente.

Estrutura de pastas gerada no vault:
  finep/
  ├── editais/       → uma nota por edital
  ├── temas/         → uma nota por tema
  ├── fontes/        → uma nota por fonte de recurso
  ├── publicos/      → uma nota por público-alvo
  ├── anos/          → uma nota por ano de publicação (dimensão longitudinal)
  ├── mecanismos/    → uma nota por mecanismo financeiro (§5.1 WIKI.md)
  ├── subprogramas/  → uma nota por subprograma / fundo setorial (§5.6 WIKI.md)
  └── trl/           → uma nota por faixa TRL (§5.8 WIKI.md)

Uso:
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault --subfolder radar-editais
"""
import argparse
import json
from pathlib import Path

from config import FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR, OBSIDIAN_VAULT_DIR
from core import wiki_schema
from core.edital_id import id_to_slug, source_of

# Multi-fonte (§12 WIKI.md): o export consome o índice unificado (todas as fontes)
# e deriva a fonte de cada edital do próprio id prefixado. Sem hardcode de fonte.

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"  # vigentes por padrão; use --historico para todos
FACTS_DIR = FINEP_PDFS_DIR


# =============================================================================
# UTILIDADES
# =============================================================================

def slugify(text: str) -> str:
    """Wrapper para core.wiki_schema.slugify (§6.3 WIKI.md)."""
    return wiki_schema.slugify(text)


# Slug colon-free para nome de nota/wikilink (Obsidian proíbe `:`). Centralizado
# em core.edital_id (id_to_slug/slug_to_id) — get_graph faz o caminho inverso.
_edital_slug = id_to_slug


def _status_emoji(status: str) -> str:
    return wiki_schema.status_info(status)["emoji"]


def _status_tag(status: str) -> str:
    return wiki_schema.status_info(status)["tag"]


def safe_yaml_str(text: str) -> str:
    """Escapa string para YAML (aspas duplas)."""
    return text.replace('"', '\\"')


def edital_note(edital: dict, facts_by_id: dict, subfolder: str = "radar-editais") -> str:
    """Gera nota Markdown para um edital, incluindo dados do card quando disponíveis."""
    eid = edital["id"]
    title = edital.get("title", "")
    status = edital.get("status", "Desconhecido")
    emoji = _status_emoji(status)
    tag = _status_tag(status)
    deadline = edital.get("deadline", "") or ""
    pub_date = edital.get("pub_date", "") or ""
    link = edital.get("link", "")
    n_pdfs = edital.get("n_pdfs", 0)
    n_facts = edital.get("n_facts", 0)

    themes = edital.get("themes", [])
    publicos = edital.get("publico_alvo", [])
    subprogramas = edital.get("subprogramas", [])
    pub_year = edital.get("pub_year", wiki_schema.parse_pub_year(pub_date))

    # Campos do card (gerados por LLM)
    objective = edital.get("objective")
    mechanism = edital.get("mechanism")
    eligible_entities = edital.get("eligible_entities") or []
    value_range = edital.get("value_range") or {}
    trl_range = edital.get("trl_range") or {}
    required_certifications = edital.get("required_certifications") or []
    counterpart = edital.get("counterpart_required", False)
    key_requirements = edital.get("key_requirements") or []
    key_facts = edital.get("key_facts") or []

    # Frontmatter YAML
    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(title)}"')
    lines.append(f"chamada_id: {eid}")
    lines.append(f"status: {status}")
    if deadline:
        lines.append(f"deadline: {deadline}")
    if pub_date:
        lines.append(f"pub_date: {pub_date}")
    if mechanism:
        lines.append(f"mechanism: {mechanism}")
    if trl_range.get("min") or trl_range.get("max"):
        lines.append(f"trl_min: {trl_range.get('min', '')}")
        lines.append(f"trl_max: {trl_range.get('max', '')}")
    lines.append(f"n_pdfs: {n_pdfs}")
    lines.append(f"n_facts: {n_facts}")
    if link:
        lines.append(f"link: {link}")
    trl_faixa_keys = wiki_schema.trl_range_to_faixas(trl_range.get("min"), trl_range.get("max"))
    lines.append("tags:")
    # Fonte real do edital vem do id prefixado (`finep:589` → `finep`), NÃO do
    # campo `source` do card — esse guarda como a wiki page foi gerada
    # (`etl_process`/`metadata_only`), não a agência.
    lines.append(f"  - {source_of(eid)}")
    lines.append("  - edital")
    lines.append(f"  - {tag}")
    if mechanism:
        lines.append(f"  - mecanismo/{mechanism}")
    for theme in themes:
        lines.append(f"  - tema/{slugify(theme)}")
    for sp in subprogramas:
        lines.append(f"  - subprograma/{slugify(sp)}")
    for fk in trl_faixa_keys:
        lines.append(f"  - trl/{fk}")
    lines.append(f"  - ano/{pub_year}")
    lines.append("---")
    lines.append("")

    lines.append(f"# {emoji} {title}")
    lines.append("")

    # Objetivo (do card)
    if objective:
        lines.append(f"> {objective}")
        lines.append("")

    # Links de relacionamentos → Graph View
    if themes:
        lines.append("## Temas")
        for t in themes:
            lines.append(f"- [[{subfolder}/temas/{slugify(t)}|{t}]]")
        lines.append("")

    # Fonte = agência/instituição de origem do edital, derivada do `source` do
    # id prefixado (`finep:589` → FINEP). Todo edital pertence a exatamente uma —
    # é o eixo de agrupamento multi-fonte do grafo. NÃO usa `fonte_recurso` (quem
    # paga: FNDCT/Petrobras/BNDES), que é outro eixo e frequentemente vazio.
    src = source_of(eid)
    lines.append("## Fonte")
    lines.append(f"- [[{subfolder}/fontes/{src}|{src.upper()}]]")
    lines.append("")

    if publicos:
        lines.append("## Público-Alvo")
        for p in publicos:
            lines.append(f"- [[{subfolder}/publicos/{slugify(p)}|{p}]]")
        lines.append("")

    if subprogramas:
        lines.append("## Subprograma")
        for sp in subprogramas:
            lines.append(f"- [[{subfolder}/subprogramas/{slugify(sp)}|{sp}]]")
        lines.append("")

    # mechanism, trl_faixa e ano são tags (frontmatter), não nós/wikilinks — §6.1.1

    # Informações básicas
    lines.append("## Informações")
    lines.append("")
    lines.append("| Campo | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Status | {emoji} {status} |")
    if deadline:
        lines.append(f"| Prazo de envio | {deadline} |")
    if pub_date:
        lines.append(f"| Publicação | {pub_date} |")
    if mechanism:
        lines.append(f"| Mecanismo | {wiki_schema.mechanism_label(mechanism)} |")
    if value_range.get("min_brl") or value_range.get("max_brl"):
        v_min = f"R$ {value_range['min_brl']:,.0f}" if value_range.get("min_brl") else "—"
        v_max = f"R$ {value_range['max_brl']:,.0f}" if value_range.get("max_brl") else "—"
        lines.append(f"| Valor do projeto | {v_min} a {v_max} |")
    if trl_range.get("min") or trl_range.get("max"):
        lines.append(f"| TRL aceito | {trl_range.get('min', '?')} a {trl_range.get('max', '?')} |")
    if counterpart:
        lines.append("| Contrapartida | Obrigatória |")
    if eligible_entities:
        lines.append(f"| Entidades elegíveis | {', '.join(eligible_entities)} |")
    if required_certifications:
        lines.append(f"| Certificações exigidas | {', '.join(required_certifications)} |")
    lines.append(f"| PDFs disponíveis | {n_pdfs} |")
    lines.append(f"| Fatos extraídos | {n_facts} |")
    if link:
        lines.append(f"| Link | [{link}]({link}) |")
    lines.append("")

    # Requisitos principais (do card)
    if key_requirements:
        lines.append("## Requisitos Principais")
        lines.append("")
        for req in key_requirements:
            lines.append(f"- {req}")
        lines.append("")

    # Fatos-chave (do card)
    if key_facts:
        lines.append("## Fatos-chave")
        lines.append("")
        for fact in key_facts:
            lines.append(f"- {fact}")
        lines.append("")

    # Fatos atômicos completos (dos PDFs)
    edital_facts = facts_by_id.get(eid, [])
    if edital_facts:
        lines.append("## Fatos Extraídos dos PDFs")
        lines.append("")
        by_section: dict[str, list[str]] = {}
        for fact in edital_facts:
            if fact.get("status") == "superseded":
                continue
            sec = fact.get("section_type", "OUTRO")
            by_section.setdefault(sec, []).append(fact.get("text", ""))

        for section, fact_texts in sorted(by_section.items()):
            lines.append(f"### {section}")
            for text in fact_texts:
                lines.append(f"- {text}")
            lines.append("")

    return "\n".join(lines)


def tema_note(tema_label: str, editais_ids: list[str], edital_by_id: dict, subfolder: str = "radar-editais") -> str:
    """Gera nota Markdown para um tema."""
    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(tema_label)}"')
    lines.append("tags:")
    lines.append("  - tema")
    lines.append("---")
    lines.append("")
    lines.append(f"# 🏷️ Tema: {tema_label}")
    lines.append("")
    lines.append(f"**{len(editais_ids)} editais** relacionados a este tema.")
    lines.append("")
    lines.append("## Editais")
    lines.append("")

    for eid in dict.fromkeys(editais_ids):
        edital = edital_by_id.get(eid, {})
        title = edital.get("title", f"Edital {eid}")
        status = edital.get("status", "")
        emoji = _status_emoji(status)
        lines.append(f"- {emoji} [[{subfolder}/editais/{_edital_slug(eid)}|{title}]]")

    lines.append("")
    return "\n".join(lines)


def fonte_note(fonte_label: str, editais_ids: list[str], edital_by_id: dict, subfolder: str = "radar-editais") -> str:
    """Gera nota Markdown para uma fonte de recurso."""
    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(fonte_label)}"')
    lines.append("tags:")
    lines.append("  - fonte-recurso")
    lines.append("---")
    lines.append("")
    lines.append(f"# 💰 Fonte: {fonte_label}")
    lines.append("")
    lines.append(f"**{len(editais_ids)} editais** financiados por esta fonte.")
    lines.append("")
    lines.append("## Editais")
    lines.append("")

    for eid in dict.fromkeys(editais_ids):
        edital = edital_by_id.get(eid, {})
        title = edital.get("title", f"Edital {eid}")
        status = edital.get("status", "")
        emoji = _status_emoji(status)
        lines.append(f"- {emoji} [[{subfolder}/editais/{_edital_slug(eid)}|{title}]]")

    lines.append("")
    return "\n".join(lines)


def publico_note(publico_label: str, editais_ids: list[str], edital_by_id: dict, subfolder: str = "radar-editais") -> str:
    """Gera nota Markdown para um público-alvo."""
    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(publico_label)}"')
    lines.append("tags:")
    lines.append("  - publico-alvo")
    lines.append("---")
    lines.append("")
    lines.append(f"# 👥 Público: {publico_label}")
    lines.append("")
    lines.append(f"**{len(editais_ids)} editais** aceitam este perfil de proponente.")
    lines.append("")
    lines.append("## Editais")
    lines.append("")

    for eid in dict.fromkeys(editais_ids):
        edital = edital_by_id.get(eid, {})
        title = edital.get("title", f"Edital {eid}")
        status = edital.get("status", "")
        emoji = _status_emoji(status)
        lines.append(f"- {emoji} [[{subfolder}/editais/{_edital_slug(eid)}|{title}]]")

    lines.append("")
    return "\n".join(lines)


def subprograma_note(sp_label: str, editais_ids: list[str], edital_by_id: dict, subfolder: str = "radar-editais") -> str:
    """Gera nota Markdown para um subprograma / fundo setorial (§5.6 WIKI.md)."""
    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(sp_label)}"')
    lines.append("tags:")
    lines.append("  - subprograma")
    lines.append("---")
    lines.append("")
    lines.append(f"# 🏛️ Subprograma: {sp_label}")
    lines.append("")
    lines.append(f"**{len(editais_ids)} editais** vinculados a este subprograma.")
    lines.append("")
    lines.append("## Editais")
    lines.append("")
    for eid in dict.fromkeys(editais_ids):
        edital = edital_by_id.get(eid, {})
        title = edital.get("title", f"Edital {eid}")
        status = edital.get("status", "")
        emoji = _status_emoji(status)
        lines.append(f"- {emoji} [[{subfolder}/editais/{_edital_slug(eid)}|{title}]]")
    lines.append("")
    return "\n".join(lines)


def home_note(index: dict, subfolder: str = "radar-editais") -> str:
    """Gera nota de índice (HOME do vault)."""
    summary = index.get("summary", {})
    by_status = summary.get("by_status", {})
    total = index.get("total_editais", 0)
    updated = index.get("last_updated", "")

    lines = ["---"]
    lines.append("tags:")
    lines.append("  - home")
    lines.append("---")
    lines.append("")
    lines.append("# 📡 Radar de Editais")
    lines.append("")
    lines.append(f"> Última atualização: **{updated}**")
    lines.append("")
    lines.append("## Resumo")
    lines.append("")
    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    lines.append(f"| Total de editais | {total} |")
    for status, count in sorted(by_status.items()):
        emoji = _status_emoji(status)
        lines.append(f"| {emoji} {status} | {count} |")
    lines.append(f"| Temas únicos | {summary.get('n_themes', 0)} |")
    lines.append(f"| Fontes de recurso | {summary.get('n_fontes', 0)} |")
    lines.append(f"| Públicos-alvo | {summary.get('n_publico_alvo', 0)} |")
    lines.append(f"| Subprogramas | {summary.get('n_subprogramas', 0)} |")
    lines.append(f"| Anos cobertos | {summary.get('n_anos', 0)} |")
    lines.append("")
    lines.append("## Navegação")
    lines.append("")
    lines.append(f"- 📂 [[{subfolder}/editais/]] — todos os editais")
    lines.append(f"- 🏷️ [[{subfolder}/temas/]] — por tema")
    lines.append(f"- 💰 [[{subfolder}/fontes/]] — por fonte de recurso")
    lines.append(f"- 👥 [[{subfolder}/publicos/]] — por público-alvo")
    lines.append(f"- 🏛️ [[{subfolder}/subprogramas/]] — por subprograma / fundo setorial")
    lines.append("")
    lines.append("## Editais Abertos")
    lines.append("")

    for edital in index.get("editais", []):
        if edital.get("status") == "ABERTA":
            eid = edital["id"]
            title = edital.get("title", "")
            deadline = edital.get("deadline", "")
            lines.append(f"- 🟢 [[{subfolder}/editais/{_edital_slug(eid)}|{title}]] — prazo: {deadline}")

    lines.append("")
    return "\n".join(lines)


# =============================================================================
# EXPORTAÇÃO
# =============================================================================

def export(vault_path: Path, subfolder: str = "radar-editais") -> None:
    """Exporta todo o knowledge graph para o vault Obsidian."""
    if not INDEX_FILE.exists():
        print(f"index.json não encontrado: {INDEX_FILE}")
        print("Execute: python pipeline/build_knowledge_graph.py")
        return

    index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    index_entries = {e["id"]: e for e in index.get("editais", [])}

    # Enriquece com dados do card (synthesized fields) quando disponível.
    # Índice é autoridade sobre inherited fields (§4.1 WIKI.md) — sobrescreve
    # o que está congelado no card. Card é autoridade sobre synthesized fields
    # (§4.2). Derived: subprogramas, n_pdfs, n_facts vêm do índice.
    from core.edital_id import wiki_page_path
    inherited_keys = wiki_schema.wiki_page_fields()["inherited"]  # global, agnóstico à fonte
    overridable_keys = list(inherited_keys) + ["subprogramas", "n_pdfs", "n_facts"]
    editais = []
    for eid, entry in index_entries.items():
        card_file = wiki_page_path(eid)
        merged = entry.copy()
        if card_file.exists():
            try:
                card = json.loads(card_file.read_text(encoding="utf-8"))
                # Card fornece synthesized fields; índice sobrescreve inherited
                merged = {**card, **{k: entry[k] for k in overridable_keys if k in entry}}
            except Exception:
                pass
        editais.append(merged)

    edital_by_id = {e["id"]: e for e in editais}

    # Base do vault — limpa notas antigas antes de re-exportar
    base = vault_path / subfolder
    # mechanism/ano/trl_faixa são tags, não nós (§6.1.1) — sem subpasta.
    expected_subfolders = {"editais", "temas", "fontes", "publicos", "subprogramas"}

    # Detecta aninhamento: se `base/<subfolder>` já existe, significa que um export
    # anterior rodou com `--vault` apontando para dentro de `base`, criando estrutura
    # duplicada. Avisa antes de prosseguir (não deleta automaticamente).
    nested = base / subfolder
    if nested.is_dir():
        print(f"⚠️  Encontrei pasta aninhada stale: {nested}")
        print("    Provavelmente de um export anterior com --vault errado.")
        print(f"    Remova manualmente: rm -rf {nested}")

    for subfld in expected_subfolders:
        folder = base / subfld
        if folder.exists():
            for f in folder.glob("*.md"):
                f.unlink()
        folder.mkdir(parents=True, exist_ok=True)

    # Carrega fatos
    facts_by_id: dict[str, list[dict]] = {}
    if FACTS_DIR.exists():
        for facts_file in FACTS_DIR.glob("*.json"):
            eid = facts_file.stem
            try:
                data = json.loads(facts_file.read_text(encoding="utf-8"))
                facts_by_id[eid] = data.get("facts", [])
            except Exception:
                pass

    # HOME
    home_path = base / "HOME.md"
    home_path.write_text(home_note(index, subfolder), encoding="utf-8")
    print(f"  HOME: {home_path}")

    # Notas de editais
    n_editais = 0
    for edital in editais:
        eid = edital["id"]
        content = edital_note(edital, facts_by_id, subfolder)
        note_path = base / "editais" / f"{_edital_slug(eid)}.md"
        note_path.write_text(content, encoding="utf-8")
        n_editais += 1

    print(f"  Editais: {n_editais} notas → {base}/editais/")

    # Notas de temas
    themes_index = index.get("themes_index", {})
    n_temas = 0
    for tema_label, editais_ids in themes_index.items():
        content = tema_note(tema_label, editais_ids, edital_by_id, subfolder)
        slug = slugify(tema_label)
        note_path = base / "temas" / f"{slug}.md"
        note_path.write_text(content, encoding="utf-8")
        n_temas += 1

    print(f"  Temas: {n_temas} notas → {base}/temas/")

    # Notas de fonte = agência/instituição (uma por `source`). Derivado do id
    # prefixado, não de `fonte_recurso` — assim todo edital (qualquer fonte)
    # pertence a exatamente um nó-agência e nunca fica órfão no grafo.
    agencia_index: dict[str, list[str]] = {}
    for e in editais:
        agencia_index.setdefault(source_of(e["id"]), []).append(e["id"])
    n_fontes = 0
    for src, editais_ids in agencia_index.items():
        content = fonte_note(src.upper(), editais_ids, edital_by_id, subfolder)
        note_path = base / "fontes" / f"{src}.md"
        note_path.write_text(content, encoding="utf-8")
        n_fontes += 1

    print(f"  Fontes (agências): {n_fontes} notas → {base}/fontes/")

    # Notas de público-alvo
    publico_index = index.get("publico_index", {})
    n_publicos = 0
    for pub_label, editais_ids in publico_index.items():
        content = publico_note(pub_label, editais_ids, edital_by_id, subfolder)
        slug = slugify(pub_label)
        note_path = base / "publicos" / f"{slug}.md"
        note_path.write_text(content, encoding="utf-8")
        n_publicos += 1

    print(f"  Públicos: {n_publicos} notas → {base}/publicos/")

    # Notas de subprograma (vem do índice, populado pelo build_knowledge_graph)
    subprograma_index = index.get("subprograma_index", {})
    n_sub = 0
    for sp_label, editais_ids in subprograma_index.items():
        content = subprograma_note(sp_label, editais_ids, edital_by_id, subfolder)
        slug = slugify(sp_label)
        note_path = base / "subprogramas" / f"{slug}.md"
        note_path.write_text(content, encoding="utf-8")
        n_sub += 1

    print(f"  Subprogramas: {n_sub} notas → {base}/subprogramas/")

    total = n_editais + n_temas + n_fontes + n_publicos + n_sub + 1
    print(f"\n✓ {total} notas exportadas para: {base}")
    print("\nPróximos passos no Obsidian:")
    print(f"  1. Abra o vault em: {vault_path}")
    print(f"  2. Acesse a nota HOME em: {subfolder}/HOME")
    print("  3. Clique no ícone de grafo (canto superior direito) ou Ctrl+G")
    print("  4. No Graph View, filtre por #edital (ou por #finep / #fapesp por fonte)")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exporta Knowledge Graph para Obsidian")
    parser.add_argument(
        "--vault",
        default=str(OBSIDIAN_VAULT_DIR),
        help=f"Caminho do vault Obsidian (default: vault unificado no projeto — {OBSIDIAN_VAULT_DIR})",
    )
    parser.add_argument(
        "--subfolder",
        default="radar-editais",
        help="Subpasta dentro do vault (default: radar-editais)",
    )
    parser.add_argument(
        "--historico",
        action="store_true",
        help="Exporta todos os editais (incluindo encerrados)",
    )
    args = parser.parse_args()

    if args.historico:
        INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index_historico.json"  # noqa: F841 — rebinds module global

    vault = Path(args.vault).expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)

    modo = "histórico (todos)" if args.historico else "vigentes"
    print(f"Exportando para: {vault / args.subfolder}  [{modo}]")
    print("-" * 50)
    export(vault, args.subfolder)
