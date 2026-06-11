"""
Identidade de edital cross-source — prefixo `{source}:{native_id}`.

A partir da Fase 1 multi-fonte (WIKI.md §12), o sistema deixa de assumir
FINEP-only. IDs nativos das fontes (FAPESP usa `18064`, FINEP usa `782`,
BNDES usa slugs) podem colidir entre si; o prefixo elimina ambiguidade.

Layout filesystem correspondente: `data/knowledge_graph/wiki/{source}/{id}.json`
(subfolder por fonte). `:` em filename é problema em Windows e em alguns
tooling — usamos subfolder, não literal.

Tabelas SQL armazenam o ID prefixado completo em `edital_id text` (4 tabelas:
writing_sessions, edital_chunks, application_log, pipeline_errors). Sem FK
ríspida — o KG é file-based.

Função pura: sem I/O, sem cache. `wiki_page_path` deriva path; chamadores
fazem `.exists()` por conta.
"""
from __future__ import annotations

from pathlib import Path

from config import KG_WIKI_DIR

_SEP = ":"


def make_id(source: str, native_id: str | int) -> str:
    """Constrói o edital_id prefixado a partir de fonte + id nativo do portal.

    `source` deve ser a chave canônica registrada em WIKI.md §12.4
    (ex.: 'finep', 'fapesp', 'bndes'). `native_id` aceita int por conveniência
    de portais que retornam numérico — convertido pra str.
    """
    if not source or _SEP in source:
        raise ValueError(f"source inválido: {source!r}")
    return f"{source}{_SEP}{native_id}"


def parse_id(edital_id: str) -> tuple[str, str]:
    """Decompõe o edital_id prefixado em `(source, native_id)`.

    Levanta `ValueError` se faltar prefixo — hard cut intencional pós-Fase 1.
    Código que ainda chama com ID nu indica que migration não rodou ou
    call site escapou do refator (ver Épico B do plano multi-fonte).
    """
    if not edital_id or _SEP not in edital_id:
        raise ValueError(
            f"edital_id sem prefixo de fonte: {edital_id!r}. "
            f"Use make_id(source, native_id) ou rode scripts/migrate_existing_ids.py."
        )
    source, native = edital_id.split(_SEP, 1)
    return source, native


def source_of(edital_id: str) -> str:
    return parse_id(edital_id)[0]


def native_id_of(edital_id: str) -> str:
    return parse_id(edital_id)[1]


def id_to_slug(edital_id: str) -> str:
    """edital_id prefixado → slug seguro para nome de nota/wikilink Obsidian.

    O Obsidian proíbe `:` em nomes de nota, então o vault usa `{source}-{native}`
    (ex.: `finep:589` → `finep-589`). É o esquema do grafo (export_to_obsidian +
    get_graph); difere do layout das wiki pages (`{source}/{native}.json`).
    """
    return edital_id.replace(_SEP, "-")


def slug_to_id(slug: str) -> str:
    """Inverso de `id_to_slug`: troca só o PRIMEIRO `-` por `:`.

    Slugs de fonte não contêm `-` (finep, fapesp, bndes...), então o primeiro
    `-` é sempre o separador fonte↔native; o native pode conter `-`
    (ex.: `bndes-funtec-2026` → `bndes:funtec-2026`). Um slug sem `-` (id nativo
    legado, sem fonte) volta inalterado.
    """
    return slug.replace("-", _SEP, 1)


def wiki_page_path(edital_id: str) -> Path:
    """Caminho da wiki page no filesystem: `KG_WIKI_DIR/{source}/{native}.json`.

    Não verifica existência — caller faz `.exists()`. Útil tanto pra leitura
    quanto pra escrita (`.parent.mkdir(parents=True, exist_ok=True)` antes
    de write).
    """
    source, native = parse_id(edital_id)
    return KG_WIKI_DIR / source / f"{native}.json"


def iter_wiki_pages(source: str | None = None) -> list[Path]:
    """Lista paths de wiki pages — todas ou de uma fonte específica.

    Substitui o `KG_WIKI_DIR.glob("*.json")` legado (era flat); hoje é
    recursive porque há subfolder por fonte. Ignora dotfiles (cache).
    """
    if not KG_WIKI_DIR.exists():
        return []
    base = KG_WIKI_DIR / source if source else KG_WIKI_DIR
    if not base.exists():
        return []
    return [p for p in base.rglob("*.json") if not p.name.startswith(".")]
