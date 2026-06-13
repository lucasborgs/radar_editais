"""
Exporta o Knowledge Graph FINEP para um vault Obsidian.

Cada nó vira uma nota Markdown com frontmatter YAML e [[wikilinks]].
O Graph View nativo do Obsidian mostra as conexões automaticamente.

Estrutura de pastas gerada no vault:
  finep/
  ├── editais/       → uma nota por edital
  ├── icts/          → uma nota por ICT (unidade EMBRAPII; quadrante entidade)
  ├── temas/         → uma nota por tema (nó compartilhado evento↔entidade)
  ├── fontes/        → uma nota por fonte de recurso
  ├── publicos/      → uma nota por público-alvo
  ├── anos/          → uma nota por ano de publicação (dimensão longitudinal)
  ├── mecanismos/    → uma nota por mecanismo financeiro (§5.1 WIKI.md)
  ├── subprogramas/  → uma nota por subprograma / fundo setorial (§5.6 WIKI.md)
  └── trl/           → uma nota por faixa TRL (§5.8 WIKI.md)

Os ICTs (icts.json, do build_ict_graph) ligam-se aos MESMOS nós de tema dos
editais — no Graph View isso conecta o quadrante de entidades (quem executa) ao
de eventos (oportunidades), via o eixo de tema compartilhado.

Uso:
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault
    python scripts/export_to_obsidian.py --vault ~/Documents/Obsidian/MeuVault --subfolder radar-editais
"""
import argparse
import json
from pathlib import Path

from config import FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR, OBSIDIAN_VAULT_DIR
from core.kg import wiki_schema
from core.kg.edital_id import id_to_slug, source_of

# Multi-fonte (§12 WIKI.md): o export consome o índice unificado (todas as fontes)
# e deriva a fonte de cada edital do próprio id prefixado. Sem hardcode de fonte.

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"  # vigentes por padrão; use --historico para todos
ICTS_FILE = KNOWLEDGE_GRAPH_DIR / "icts.json"     # quadrante entidade (build_ict_graph); opcional
FACTS_DIR = FINEP_PDFS_DIR


# =============================================================================
# UTILIDADES
# =============================================================================

def slugify(text: str) -> str:
    """Wrapper para core.kg.wiki_schema.slugify (§6.3 WIKI.md)."""
    return wiki_schema.slugify(text)


# Slug colon-free para nome de nota/wikilink (Obsidian proíbe `:`). Centralizado
# em core.kg.edital_id (id_to_slug/slug_to_id) — get_graph faz o caminho inverso.
_edital_slug = id_to_slug


def _event_folder(opportunity_type: str | None) -> str:
    """Pasta do vault para um evento (multi-quadrante), derivada do schema (§6.1).

    `opportunity_type` (edital|desafio|programa) coincide com a chave do node_type;
    o folder vem de wiki_schema.node_types() — schema autoritativo, sem duplicação.
    Default `editais` (FINEP/FAPESP e qualquer tipo desconhecido).
    """
    nt = wiki_schema.node_types().get(opportunity_type or "edital", {})
    return nt.get("folder", "editais")


def _event_folder_for(eid: str, edital_by_id: dict) -> str:
    """Pasta do evento `eid` resolvida via edital_by_id (cross-links nas notas-ponte)."""
    return _event_folder(edital_by_id.get(eid, {}).get("opportunity_type"))


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


def ict_note(ict: dict, subfolder: str = "radar-editais") -> str:
    """Gera nota Markdown para um ICT (unidade EMBRAPII — quadrante entidade).

    O ICT liga-se aos MESMOS nós de tema dos editais (wikilinks + tags `tema/…`):
    é por aí que o Graph View conecta quem-executa (entidade) a quais-oportunidades
    (evento). Sem prazo/status — ICT não é um evento datado, é um parceiro estável.
    """
    iid = ict["id"]
    name = ict.get("name", iid)
    themes = ict.get("themes", []) or []
    areas = ict.get("areas_raw", []) or []
    about = (ict.get("summary") or ict.get("about") or "").strip()
    contact = ict.get("contact") or {}
    address = ict.get("address", "") or ""
    url = ict.get("url", "") or ""

    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(name)}"')
    lines.append(f"ict_id: {iid}")
    if url:
        lines.append(f"link: {url}")
    lines.append("tags:")
    lines.append(f"  - {source_of(iid)}")   # embrapii
    lines.append("  - ict")
    for theme in themes:
        lines.append(f"  - tema/{slugify(theme)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# 🔬 ICT: {name}")
    lines.append("")

    if about:
        # `about` pode ser longo (texto cru da unidade); trunca pra um resumo legível.
        snippet = about if len(about) <= 600 else about[:600].rstrip() + "…"
        lines.append(f"> {snippet}")
        lines.append("")

    # Fonte = agência da entidade (EMBRAPII), análogo ao nó-fonte dos editais —
    # dá um nó-pai que agrupa todas as unidades (ponto 3 do feedback de grafo).
    src = source_of(iid)
    lines.append("## Fonte")
    lines.append(f"- [[{subfolder}/fontes/{src}|{src.upper()}]]")
    lines.append("")

    # Temas → nós compartilhados com os editais (a ponte evento↔entidade)
    if themes:
        lines.append("## Temas de atuação")
        for t in themes:
            lines.append(f"- [[{subfolder}/temas/{slugify(t)}|{t}]]")
        lines.append("")

    if areas:
        lines.append("## Áreas de expertise")
        for a in areas:
            lines.append(f"- {a}")
        lines.append("")

    # Contato/endereço (do bloco ACF do WordPress EMBRAPII)
    rows: list[tuple[str, str]] = []
    if contact.get("responsavel"):
        rows.append(("Responsável", contact["responsavel"]))
    if contact.get("email"):
        rows.append(("E-mail", contact["email"]))
    if contact.get("telefone"):
        rows.append(("Telefone", contact["telefone"]))
    if contact.get("site"):
        rows.append(("Site", contact["site"]))
    if address:
        rows.append(("Endereço", address))
    if url:
        rows.append(("Página EMBRAPII", f"[{url}]({url})"))
    if rows:
        lines.append("## Contato")
        lines.append("")
        lines.append("| Campo | Valor |")
        lines.append("|---|---|")
        for label, val in rows:
            lines.append(f"| {label} | {val} |")
        lines.append("")

    return "\n".join(lines)


def fonte_entidade_note(label: str, icts: list[dict], subfolder: str = "radar-editais") -> str:
    """Nó-fonte de uma agência de entidades (ex.: EMBRAPII) — agrupa as unidades
    sob um nó-pai, análogo aos nós-fonte dos editais (ponto 3 do feedback de grafo)."""
    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(label)}"')
    lines.append("tags:")
    lines.append("  - fonte-recurso")
    lines.append("---")
    lines.append("")
    lines.append(f"# 💰 Fonte: {label}")
    lines.append("")
    lines.append(f"**{len(icts)} ICTs** credenciadas por esta agência.")
    lines.append("")
    lines.append("## ICTs")
    lines.append("")
    for ict in icts:
        name = ict.get("name", ict["id"])
        lines.append(f"- 🔬 [[{subfolder}/icts/{_edital_slug(ict['id'])}|{name}]]")
    lines.append("")
    return "\n".join(lines)


def tema_note(
    tema_label: str,
    editais_ids: list[str],
    edital_by_id: dict,
    subfolder: str = "radar-editais",
    icts_for_theme: list[dict] | None = None,
) -> str:
    """Gera nota Markdown para um tema.

    O tema é o nó-ponte entre eventos (editais) e entidades (ICTs): lista ambos,
    para que a navegação e o Graph View mostrem os dois quadrantes em torno dele.
    """
    icts_for_theme = icts_for_theme or []
    lines = ["---"]
    lines.append(f'title: "{safe_yaml_str(tema_label)}"')
    lines.append("tags:")
    lines.append("  - tema")
    lines.append("---")
    lines.append("")
    lines.append(f"# 🏷️ Tema: {tema_label}")
    lines.append("")
    lines.append(f"**{len(editais_ids)} editais** e **{len(icts_for_theme)} ICTs** relacionados a este tema.")
    lines.append("")
    lines.append("## Editais")
    lines.append("")

    for eid in dict.fromkeys(editais_ids):
        edital = edital_by_id.get(eid, {})
        title = edital.get("title", f"Edital {eid}")
        status = edital.get("status", "")
        emoji = _status_emoji(status)
        folder = _event_folder_for(eid, edital_by_id)
        lines.append(f"- {emoji} [[{subfolder}/{folder}/{_edital_slug(eid)}|{title}]]")

    lines.append("")

    if icts_for_theme:
        lines.append("## ICTs parceiras")
        lines.append("")
        for ict in icts_for_theme:
            name = ict.get("name", ict["id"])
            lines.append(f"- 🔬 [[{subfolder}/icts/{_edital_slug(ict['id'])}|{name}]]")
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
        folder = _event_folder_for(eid, edital_by_id)
        lines.append(f"- {emoji} [[{subfolder}/{folder}/{_edital_slug(eid)}|{title}]]")

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
        folder = _event_folder_for(eid, edital_by_id)
        lines.append(f"- {emoji} [[{subfolder}/{folder}/{_edital_slug(eid)}|{title}]]")

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
        folder = _event_folder_for(eid, edital_by_id)
        lines.append(f"- {emoji} [[{subfolder}/{folder}/{_edital_slug(eid)}|{title}]]")
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
    lines.append(f"- 🎯 [[{subfolder}/desafios/]] — desafios (open innovation)")
    lines.append(f"- 🚀 [[{subfolder}/programas/]] — programas (aceleração)")
    lines.append(f"- 🔬 [[{subfolder}/icts/]] — ICTs parceiras (unidades EMBRAPII)")
    lines.append(f"- 🏷️ [[{subfolder}/temas/]] — por tema (ponte editais↔ICTs)")
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
            folder = _event_folder(edital.get("opportunity_type"))
            lines.append(f"- 🟢 [[{subfolder}/{folder}/{_edital_slug(eid)}|{title}]] — prazo: {deadline}")

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
    from core.kg.edital_id import wiki_page_path
    inherited_keys = wiki_schema.wiki_page_fields()["inherited"]  # global, agnóstico à fonte
    # opportunity_type é inherited (vem da Descoberta → entry do índice; a wiki page
    # sintetizada não o carrega) — índice é autoridade, então entra no override.
    overridable_keys = list(inherited_keys) + ["subprogramas", "n_pdfs", "n_facts", "opportunity_type"]
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

    # ICTs (quadrante entidade) — opcional: só exporta se build_ict_graph rodou.
    icts: list[dict] = []
    icts_by_theme: dict[str, list[dict]] = {}
    if ICTS_FILE.exists():
        try:
            icts_doc = json.loads(ICTS_FILE.read_text(encoding="utf-8"))
            icts = icts_doc.get("icts", []) if isinstance(icts_doc, dict) else (icts_doc or [])
            for ict in icts:
                for theme in ict.get("themes", []) or []:
                    icts_by_theme.setdefault(theme, []).append(ict)
        except Exception:
            icts = []

    # Base do vault — limpa notas antigas antes de re-exportar
    base = vault_path / subfolder
    # mechanism/ano/trl_faixa são tags, não nós (§6.1.1) — sem subpasta.
    expected_subfolders = {"editais", "desafios", "programas", "icts", "temas", "fontes", "publicos", "subprogramas"}

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

    # Notas de eventos (edital/desafio/programa) — roteadas por opportunity_type.
    by_folder: dict[str, int] = {}
    for edital in editais:
        eid = edital["id"]
        folder = _event_folder(edital.get("opportunity_type"))
        content = edital_note(edital, facts_by_id, subfolder)
        note_path = base / folder / f"{_edital_slug(eid)}.md"
        note_path.write_text(content, encoding="utf-8")
        by_folder[folder] = by_folder.get(folder, 0) + 1

    for folder, n in sorted(by_folder.items()):
        print(f"  {folder}: {n} notas → {base}/{folder}/")
    n_editais = sum(by_folder.values())

    # Notas de ICTs (quadrante entidade)
    n_icts = 0
    for ict in icts:
        content = ict_note(ict, subfolder)
        note_path = base / "icts" / f"{_edital_slug(ict['id'])}.md"
        note_path.write_text(content, encoding="utf-8")
        n_icts += 1
    if icts:
        print(f"  ICTs: {n_icts} notas → {base}/icts/")

    # Notas de temas — nó-ponte: lista editais E ICTs do tema. A união das chaves
    # garante que um tema com ICTs mas sem edital (ou vice-versa) ainda vire nota.
    themes_index = index.get("themes_index", {})
    n_temas = 0
    all_theme_labels = dict.fromkeys([*themes_index.keys(), *icts_by_theme.keys()])
    for tema_label in all_theme_labels:
        editais_ids = themes_index.get(tema_label, [])
        content = tema_note(
            tema_label, editais_ids, edital_by_id, subfolder,
            icts_for_theme=icts_by_theme.get(tema_label, []),
        )
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

    # Nós-fonte das entidades (agrupa ICTs sob o nó-pai da agência, ex.: EMBRAPII).
    icts_by_source: dict[str, list[dict]] = {}
    for ict in icts:
        icts_by_source.setdefault(source_of(ict["id"]), []).append(ict)
    for src, src_icts in icts_by_source.items():
        content = fonte_entidade_note(src.upper(), src_icts, subfolder)
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

    total = n_editais + n_icts + n_temas + n_fontes + n_publicos + n_sub + 1
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
