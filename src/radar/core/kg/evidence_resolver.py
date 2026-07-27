"""Radar Data Trust 01 — Resolvedor de evidência (RT01-T03).

Resolve um `quote` (substring textual) para coordenadas do Documento
Canônico/silver, produzindo um `EvidenceRef` (radar.domain.provenance) válido
conforme o contrato do T01, sem jamais escolher silenciosamente uma ocorrência
ambígua como se fosse a única.

Módulo PURO: nenhum I/O de rede, banco ou disco. Os blocos silver chegam por
parâmetro (`blocks: list[dict]`), no formato já emitido por
`gold._read_silver_blocks` — um dict por linha de
`tests/fixtures/gold_equivalence/silver/structured_docs/<source>/<stem>.jsonl`:
`{"idx": int, "doc": str, "page": int | None, "section_path": list[str],
"kind": str, "text": str, ...}`. Chaves extras (`document_metadata` etc.) são
ignoradas.

Regra do prefixo comum não ambíguo (spec §5.2, "nunca escolher silenciosamente
a primeira ocorrência"):

- **0 ocorrências** → `unresolved`: nenhuma coordenada, quote preservado.
- **docs diferentes** entre os candidatos → `unresolved`: mesmo pensamento —
  não há prefixo comum sequer no nível do documento.
- **mesmo doc, páginas diferentes** (ou alguma página ausente) → `document_only`:
  o documento é uma âncora segura, a página não é.
- **mesmo doc + mesma página, blocos diferentes** → `exact` com `page`, mas
  SEM `block_idx`/`section_path` — a coordenada mais fina que o prefixo comum
  garante sem ambiguidade é a página. Exceção: se essa "mesma página" for
  `None` uniformemente (HTML sem unidade paginada, blocos diferentes), não há
  coordenada real nenhuma para ancorar — cai para `document_only` em vez de
  produzir um `exact` sem coordenada (que violaria o invariante do T01).
- **mesmo doc + mesma página + mesmo bloco** (== 1 candidato, ou N candidatos
  todos apontando ao mesmo `(doc, page, idx)` — repetição do trecho dentro do
  próprio bloco) → `exact` completo, com `page`/`block_idx`/`section_path`.

Casamento textual: substring EXATA após normalização determinística de
whitespace (colapsar runs de espaço/quebra em espaço único + strip). Nenhuma
outra normalização (sem case-fold, sem remoção de acento, sem regex
heurística) — ver `_normalize_ws`. `EvidenceRef.quote` sempre preserva o texto
original do chamador, nunca a forma normalizada.

Limitação v1 documentada (não mascarada): um `quote` que atravessa fronteira
de blocos (started em um bloco, terminado no seguinte) NÃO é resolvido nesta
versão — cada bloco é casado isoladamente, então esse caso produz 0
candidatos e cai em `unresolved`. Resolver merges de blocos adjacentes fica
para uma iteração futura, se a spec exigir.

Hash: o chamador informa `silver_source_hash`/`canonical_content_hash` já
prefixados com o algoritmo real (ex. `"md5:<hex>"`, pois tanto
`structurer._source_hash` quanto `source_docs.canonical_hash` são md5). Este
módulo NUNCA re-hasheia conteúdo nem fabrica um hash ausente: se o chamador
não informar nenhum dos dois, o resultado expõe isso explicitamente
(`missing_hash=True`) e `evidence_ref` fica `None` mesmo que a resolução
textual tenha sido bem-sucedida — `EvidenceRef` sem hash não é construível
(invariante do T01).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from radar.domain.provenance import EvidenceRef, LocatorQuality

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    """Colapsa runs de whitespace (espaço/tab/quebra) em um único espaço e
    remove espaço nas pontas. Única normalização permitida (spec: "apenas
    whitespace"). Não faz case-fold nem remove acentos."""
    return _WHITESPACE_RE.sub(" ", text).strip()


@dataclass(frozen=True)
class EvidenceCandidate:
    """Uma ocorrência do quote em um bloco silver."""

    doc: str
    page: int | None
    block_idx: int | None
    section_path: tuple[str, ...]


@dataclass(frozen=True)
class ResolveResult:
    """Resultado estruturado de `resolve_quote`."""

    candidates: tuple[EvidenceCandidate, ...]
    ambiguous: bool
    evidence_ref: EvidenceRef | None
    missing_hash: bool = False


def resolve_quote(
    quote: str,
    blocks: list[dict],
    *,
    source: str,
    edital_id: str | None = None,
    native_id: str | None = None,
    silver_source_hash: str | None = None,
    canonical_content_hash: str | None = None,
    source_url: str | None = None,
    collected_at=None,
) -> ResolveResult:
    """Localiza `quote` em `blocks` e constrói o `EvidenceRef` correspondente
    sem escolher silenciosamente uma ocorrência ambígua.

    `blocks`: lista de dicts silver (`idx`, `doc`, `page`, `section_path`,
    `kind`, `text`, ...). Ordem de entrada é irrelevante — os candidatos são
    sempre ordenados deterministicamente por `(doc, page, idx)` no resultado
    (`None` de página ordena antes de qualquer inteiro).

    Não fabrica coordenada, página nem hash. Retorna `evidence_ref=None`
    quando nenhum hash foi informado pelo chamador (`missing_hash=True`),
    mesmo que a resolução textual em si não seja ambígua — o chamador deve
    então decidir como obter o hash antes de tentar novamente.
    """
    normalized_quote = _normalize_ws(quote)

    raw_candidates: list[EvidenceCandidate] = []
    matched_blocks: list[dict] = []
    if normalized_quote:
        for block in blocks:
            text = block.get("text")
            if not text:
                continue
            if normalized_quote in _normalize_ws(text):
                matched_blocks.append(block)
                raw_candidates.append(
                    EvidenceCandidate(
                        doc=block.get("doc") or "",
                        page=block.get("page"),
                        block_idx=block.get("idx"),
                        section_path=tuple(block.get("section_path") or []),
                    )
                )

    candidates = tuple(
        sorted(
            raw_candidates,
            key=lambda c: (c.doc, c.page is not None, c.page or 0, c.block_idx or -1),
        )
    )
    ambiguous = len(candidates) >= 2
    missing_hash = not silver_source_hash and not canonical_content_hash

    locator_quality, page, block_idx, section_path = _classify(candidates)
    # `document` só é uma coordenada confiável quando a resolução concordou
    # em um único doc (exact/document_only). Em unresolved — docs diferentes
    # ou nenhuma ocorrência — nomear candidates[0].doc seria escolher
    # silenciosamente um documento entre vários possíveis; fica None.
    resolved_document = (
        candidates[0].doc
        if candidates and locator_quality is not LocatorQuality.UNRESOLVED
        else None
    )

    evidence_ref: EvidenceRef | None = None
    if not missing_hash:
        evidence_ref = EvidenceRef(
            source=source,
            native_id=native_id,
            edital_id=edital_id,
            source_url=source_url,
            document=resolved_document,
            page=page,
            block_idx=block_idx,
            section_path=list(section_path),
            quote=quote,
            canonical_content_hash=canonical_content_hash,
            silver_source_hash=silver_source_hash,
            collected_at=collected_at,
            locator_quality=locator_quality,
        )
        bundle_lineage = _bundle_lineage_from_blocks(
            matched_blocks,
            locator_quality=locator_quality,
            resolved_document=resolved_document,
        )
        if bundle_lineage is not None:
            evidence_ref = _validated_with_bundle_lineage(evidence_ref, bundle_lineage)

    return ResolveResult(
        candidates=candidates,
        ambiguous=ambiguous,
        evidence_ref=evidence_ref,
        missing_hash=missing_hash,
    )


def _classify(
    candidates: tuple[EvidenceCandidate, ...],
) -> tuple[LocatorQuality, int | None, int | None, tuple[str, ...]]:
    """Aplica a regra do prefixo comum não ambíguo (spec §5.2) e devolve
    `(locator_quality, page, block_idx, section_path)` — coordenadas
    fabricáveis só até o nível que TODOS os candidatos concordam."""
    if not candidates:
        return LocatorQuality.UNRESOLVED, None, None, ()

    docs = {c.doc for c in candidates}
    if len(docs) > 1:
        return LocatorQuality.UNRESOLVED, None, None, ()

    pages = {c.page for c in candidates}
    if len(pages) > 1:
        # mesmo doc, páginas diferentes (ou alguma ausente/None entre elas):
        # documento é âncora segura, página não é.
        return LocatorQuality.DOCUMENT_ONLY, None, None, ()

    # mesma página (inclusive todas None) em um único doc.
    (only_page,) = pages
    block_idxs = {c.block_idx for c in candidates}
    if len(block_idxs) > 1:
        if only_page is None:
            # "mesma página" aqui é apenas ausência uniforme de unidade
            # paginada (HTML) — não é uma coordenada real. `exact` sem
            # nenhuma coordenada resolvida violaria o invariante do T01
            # (locator_quality=exact requires at least one resolved
            # coordinate), então o prefixo comum seguro cai para o documento.
            return LocatorQuality.DOCUMENT_ONLY, None, None, ()
        return LocatorQuality.EXACT, only_page, None, ()

    # mesmo doc + mesma página + mesmo bloco.
    only = candidates[0]
    return LocatorQuality.EXACT, only.page, only.block_idx, only.section_path


def _bundle_lineage_from_blocks(
    blocks: list[dict],
    *,
    locator_quality: LocatorQuality,
    resolved_document: str | None,
) -> dict[str, str] | None:
    if locator_quality is LocatorQuality.UNRESOLVED or not resolved_document:
        return None
    lineage_pairs = {
        (metadata.get("bundle_hash"), metadata.get("content_hash"))
        for block in blocks
        if block.get("doc") == resolved_document
        for metadata in [block.get("document_metadata") or {}]
        if metadata.get("bundle_hash") and metadata.get("content_hash")
    }
    if len(lineage_pairs) != 1:
        return None
    bundle_hash, content_hash = next(iter(lineage_pairs))
    return {
        "bundle_hash": bundle_hash,
        "content_hash": content_hash,
    }


def _validated_with_bundle_lineage(
    evidence_ref: EvidenceRef,
    bundle_lineage: dict[str, str],
) -> EvidenceRef:
    try:
        return EvidenceRef.model_validate({
            **evidence_ref.model_dump(mode="json"),
            **bundle_lineage,
        })
    except ValidationError:
        return evidence_ref


__all__ = [
    "EvidenceCandidate",
    "ResolveResult",
    "resolve_quote",
]
