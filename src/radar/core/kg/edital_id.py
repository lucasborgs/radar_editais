"""
Identidade de edital cross-source — prefixo `{source}:{native_id}`.

A partir da Fase 1 multi-fonte (docs/domain/schema.md §12), o sistema deixa de assumir
FINEP-only. IDs nativos das fontes (FAPESP usa `18064`, FINEP usa `782`,
BNDES usa slugs) podem colidir entre si; o prefixo elimina ambiguidade.

O ID prefixado é compartilhado pelas tabelas relacionais e pelos artefatos
derivados. Este módulo contém apenas transformações puras, sem I/O ou cache.
"""
from __future__ import annotations

_SEP = ":"


def make_id(source: str, native_id: str | int) -> str:
    """Constrói o edital_id prefixado a partir de fonte + id nativo do portal.

    `source` deve ser a chave canônica registrada em docs/domain/schema.md §12.4
    (ex.: 'finep', 'fapesp', 'bndes'). `native_id` aceita int por conveniência
    de portais que retornam numérico — convertido pra str.
    """
    if not source or _SEP in source:
        raise ValueError(f"source inválido: {source!r}")
    return f"{source}{_SEP}{native_id}"


def parse_id(edital_id: str) -> tuple[str, str]:
    """Decompõe o edital_id prefixado em `(source, native_id)`.

    Levanta `ValueError` se faltar prefixo — contrato multi-fonte atual.
    """
    if not edital_id or _SEP not in edital_id:
        raise ValueError(
            f"edital_id sem prefixo de fonte: {edital_id!r}. "
            "Use make_id(source, native_id)."
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
    (ex.: `finep:589` → `finep-589`). É o esquema do vault gerado por
    `export_to_obsidian` e consumido por `get_graph`.
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
