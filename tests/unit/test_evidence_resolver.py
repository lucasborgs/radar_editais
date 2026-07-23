"""Testes herméticos do resolvedor de evidência (RT01-T03).

Sem I/O de rede/banco. Material principal: fixture silver real de
`tests/fixtures/gold_equivalence/silver/structured_docs/finep/602.jsonl`
(RT01-T02), usada para o caso de trecho único e exato. Os demais casos
(repetição controlada em blocos/páginas/documentos e bloco sem `page`) usam
blocos SINTÉTICOS mínimos, claramente rotulados como tal — confirmado por
inspeção que as fixtures reais não contêm nem repetição controlada do mesmo
trecho, nem blocos com `page=None`.
"""
from __future__ import annotations

import json
from pathlib import Path

from radar.core.kg.evidence_resolver import resolve_quote
from radar.domain.provenance import EvidenceRef, LocatorQuality

_FIXTURES_DIR = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "gold_equivalence"
    / "silver"
    / "structured_docs"
)


def _load_blocks(source: str, stem: str) -> list[dict]:
    path = _FIXTURES_DIR / source / f"{stem}.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_silver_hash(source: str, stem: str) -> str:
    path = _FIXTURES_DIR / source / f"{stem}.meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    return f"md5:{meta['source_hash']}"


FINEP_602_BLOCKS = _load_blocks("finep", "602")
FINEP_602_HASH = _load_silver_hash("finep", "602")

# --- Blocos SINTÉTICOS (rótulo explícito) -----------------------------------
# As fixtures reais de RT01-T02 não contêm repetição controlada do mesmo
# trecho nem blocos com page=None; estes casos exercitam esses cenários com
# o mínimo de dados fabricados, sem citar nenhum edital real.

_SYNTH_SAME_PAGE_TWO_BLOCKS = [
    {
        "idx": 0,
        "doc": "Sintetico.pdf",
        "page": 1,
        "section_path": ["A"],
        "kind": "paragraph",
        "text": "O prazo de submissão é até o dia 31 de dezembro.",
    },
    {
        "idx": 1,
        "doc": "Sintetico.pdf",
        "page": 1,
        "section_path": ["B"],
        "kind": "paragraph",
        "text": "Repetimos: o prazo de submissão é até o dia 31 de dezembro.",
    },
]

_SYNTH_TWO_PAGES_SAME_DOC = [
    {
        "idx": 0,
        "doc": "Sintetico.pdf",
        "page": 1,
        "section_path": ["A"],
        "kind": "paragraph",
        "text": "Podem participar empresas de pequeno porte.",
    },
    {
        "idx": 5,
        "doc": "Sintetico.pdf",
        "page": 3,
        "section_path": ["Anexo II"],
        "kind": "paragraph",
        "text": "Reforçamos: podem participar empresas de pequeno porte.",
    },
]

_SYNTH_TWO_DOCS = [
    {
        "idx": 0,
        "doc": "Edital.pdf",
        "page": 1,
        "section_path": ["1"],
        "kind": "paragraph",
        "text": "Valor máximo de financiamento: R$ 500.000,00.",
    },
    {
        "idx": 0,
        "doc": "Anexo.pdf",
        "page": 1,
        "section_path": ["Anexo"],
        "kind": "paragraph",
        "text": "Confirmamos: valor máximo de financiamento: R$ 500.000,00.",
    },
]

_SYNTH_NO_PAGE_UNIQUE = [
    {
        "idx": 0,
        "doc": "pagina.html",
        "page": None,
        "section_path": [],
        "kind": "paragraph",
        "text": "Esta chamada é destinada a startups em estágio inicial.",
    },
]

_SYNTH_NO_PAGE_REPEATED = [
    {
        "idx": 0,
        "doc": "pagina.html",
        "page": None,
        "section_path": [],
        "kind": "paragraph",
        "text": "Inscrições abertas até o fim do mês.",
    },
    {
        "idx": 1,
        "doc": "pagina.html",
        "page": None,
        "section_path": [],
        "kind": "paragraph",
        "text": "Lembrete: inscrições abertas até o fim do mês.",
    },
]

_SYNTH_HASH = "md5:00000000000000000000000000000000"


# --- Casos ------------------------------------------------------------------


def test_unique_exact_quote_resolves_full_coordinates():
    """Trecho único e exato na fixture real finep/602: exact com doc, page,
    block_idx e section_path corretos (spec §10.2 "trecho único e exato")."""
    quote = (
        "Acordo de Cooperação entre as Partes, assinado em Madri em 27 de "
        "outubro de 2006"
    )
    result = resolve_quote(
        quote,
        FINEP_602_BLOCKS,
        source="finep",
        native_id="602",
        edital_id="finep:602",
        silver_source_hash=FINEP_602_HASH,
    )
    assert not result.ambiguous
    assert len(result.candidates) == 1
    cand = result.candidates[0]
    assert cand.doc == "Edital.pdf"
    assert cand.page == 1
    assert cand.block_idx == 3
    assert cand.section_path == ("1. INTRODUÇÃO",)

    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.EXACT
    assert ref.document == "Edital.pdf"
    assert ref.page == 1
    assert ref.block_idx == 3
    assert ref.section_path == ["1. INTRODUÇÃO"]
    assert ref.quote == quote  # verbatim, não normalizado


def test_repeated_in_two_blocks_same_page_yields_exact_page_only():
    """Trecho repetido em dois blocos da mesma página → exact só com page
    (spec §5.2: prefixo comum seguro é a página, não o bloco)."""
    result = resolve_quote(
        "prazo de submissão é até o dia 31 de dezembro",
        _SYNTH_SAME_PAGE_TWO_BLOCKS,
        source="synthetic",
        silver_source_hash=_SYNTH_HASH,
    )
    assert result.ambiguous
    assert len(result.candidates) == 2
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.EXACT
    assert ref.page == 1
    assert ref.block_idx is None
    assert ref.section_path == []


def test_repeated_in_two_pages_same_doc_yields_document_only():
    """Trecho repetido em duas páginas do mesmo doc → document_only
    (spec §10.2 "trecho repetido em duas páginas")."""
    result = resolve_quote(
        "participar empresas de pequeno porte",
        _SYNTH_TWO_PAGES_SAME_DOC,
        source="synthetic",
        silver_source_hash=_SYNTH_HASH,
    )
    assert result.ambiguous
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.DOCUMENT_ONLY
    assert ref.document == "Sintetico.pdf"
    assert ref.page is None
    assert ref.block_idx is None
    assert ref.section_path == []


def test_repeated_in_different_docs_yields_unresolved_never_exact():
    """Adversarial: trecho repetido em documentos diferentes → unresolved com
    candidates completos, NUNCA exact (spec §5.2: nunca escolher
    silenciosamente a primeira ocorrência)."""
    result = resolve_quote(
        "máximo de financiamento: R$ 500.000,00",
        _SYNTH_TWO_DOCS,
        source="synthetic",
        silver_source_hash=_SYNTH_HASH,
    )
    assert result.ambiguous
    assert len(result.candidates) == 2
    docs = {c.doc for c in result.candidates}
    assert docs == {"Edital.pdf", "Anexo.pdf"}
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.UNRESOLVED
    assert ref.document is None
    assert ref.page is None
    assert ref.block_idx is None
    assert ref.section_path == []
    assert ref.quote == "máximo de financiamento: R$ 500.000,00"


def test_absent_quote_yields_unresolved_with_quote_preserved():
    """Trecho ausente → unresolved, quote preservado, zero coordenadas."""
    result = resolve_quote(
        "este trecho não existe em nenhum bloco",
        FINEP_602_BLOCKS,
        source="finep",
        native_id="602",
        silver_source_hash=FINEP_602_HASH,
    )
    assert not result.candidates
    assert not result.ambiguous
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.UNRESOLVED
    assert ref.document is None
    assert ref.page is None
    assert ref.block_idx is None
    assert ref.quote == "este trecho não existe em nenhum bloco"


def test_html_block_without_page_resolves_without_fabricating_page():
    """Bloco HTML sem page (None), ocorrência única → exact com block_idx,
    page permanece None (nunca fabricado)."""
    result = resolve_quote(
        "startups em estágio inicial",
        _SYNTH_NO_PAGE_UNIQUE,
        source="synthetic",
        silver_source_hash=_SYNTH_HASH,
    )
    assert not result.ambiguous
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.EXACT
    assert ref.page is None
    assert ref.block_idx == 0


def test_html_repeated_without_page_yields_document_only_not_fabricated_exact():
    """Repetição em blocos diferentes, todos com page=None: 'mesma página'
    aqui é ausência uniforme de unidade paginada, não uma coordenada real —
    cai para document_only em vez de um exact sem nenhuma coordenada
    resolvida (o que violaria o invariante do T01)."""
    result = resolve_quote(
        "abertas até o fim do mês",
        _SYNTH_NO_PAGE_REPEATED,
        source="synthetic",
        silver_source_hash=_SYNTH_HASH,
    )
    assert result.ambiguous
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.DOCUMENT_ONLY
    assert ref.page is None
    assert ref.block_idx is None


def test_whitespace_divergence_between_quote_and_block_still_resolves():
    """Quote com quebras de linha, bloco com espaços simples → resolve
    (normalização de whitespace é a única permitida)."""
    quote_with_breaks = "Acordo de Cooperação\nentre  as   Partes,\n assinado em Madri"
    result = resolve_quote(
        quote_with_breaks,
        FINEP_602_BLOCKS,
        source="finep",
        native_id="602",
        silver_source_hash=FINEP_602_HASH,
    )
    assert not result.ambiguous
    assert len(result.candidates) == 1
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.EXACT
    # quote persistido é o original do chamador, não a forma normalizada
    assert ref.quote == quote_with_breaks


def test_case_folding_is_not_performed_case_mismatch_is_unresolved():
    """Normalização proibida (case-fold) NÃO acontece: caixa diferente do
    texto real do bloco não casa → unresolved."""
    result = resolve_quote(
        "ACORDO DE COOPERAÇÃO ENTRE AS PARTES",
        FINEP_602_BLOCKS,
        source="finep",
        native_id="602",
        silver_source_hash=FINEP_602_HASH,
    )
    assert not result.candidates
    ref = result.evidence_ref
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.UNRESOLVED


def test_evidence_ref_round_trips_through_provenance_validators():
    """Todo EvidenceRef emitido passa pelos validadores de
    radar.domain.provenance (round-trip via dump/reconstrução)."""
    result = resolve_quote(
        "Acordo de Cooperação entre as Partes, assinado em Madri",
        FINEP_602_BLOCKS,
        source="finep",
        native_id="602",
        edital_id="finep:602",
        silver_source_hash=FINEP_602_HASH,
    )
    ref = result.evidence_ref
    assert ref is not None
    dumped = ref.model_dump(mode="json")
    rebuilt = EvidenceRef.model_validate(dumped)
    assert rebuilt == ref


def test_missing_hash_prevents_evidence_ref_construction():
    """Sem canonical_content_hash nem silver_source_hash, o resolver não
    fabrica hash ausente: evidence_ref fica None e missing_hash=True, mesmo
    com resolução textual bem-sucedida e não ambígua."""
    result = resolve_quote(
        "Acordo de Cooperação entre as Partes, assinado em Madri",
        FINEP_602_BLOCKS,
        source="finep",
        native_id="602",
    )
    assert not result.ambiguous
    assert result.candidates
    assert result.missing_hash
    assert result.evidence_ref is None


def test_resolution_is_deterministic_across_repeated_calls():
    """Duas resoluções idênticas → resultados idênticos (candidates ordenados
    deterministicamente por (doc, page, idx))."""
    kwargs = dict(
        source="finep",
        native_id="602",
        edital_id="finep:602",
        silver_source_hash=FINEP_602_HASH,
    )
    quote = "Instituição Científica, Tecnológica e de Inovação (ICT)"
    first = resolve_quote(quote, FINEP_602_BLOCKS, **kwargs)
    second = resolve_quote(quote, FINEP_602_BLOCKS, **kwargs)
    assert first.candidates == second.candidates
    assert first.ambiguous == second.ambiguous
    assert first.evidence_ref == second.evidence_ref
