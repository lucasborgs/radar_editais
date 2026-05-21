"""
Unit tests pra helpers de `core.retriever`:
  • `_build_or_tsquery`  — query rewrite para OR-tsquery (Fix A)
  • `_dedup_by_source`   — diversidade no top-K (Fix B)

Estes testes NÃO tocam o DB. Cobertura de integração (psycopg + pgvector)
exige fixture com pgvector ativo — TODO.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.retriever import (  # noqa: E402
    _build_or_tsquery,
    _dedup_by_source,
    format_chunks_for_prompt,
)


# =============================================================================
# OR-tsquery rewrite
# =============================================================================

def test_or_tsquery_remove_stopwords_e_pontuacao():
    q = "Qual o valor máximo de subvenção por projeto?"
    result = _build_or_tsquery(q)
    terms = [t.strip() for t in result.split("|")]
    # Stopwords não aparecem
    assert "qual" not in terms
    assert "o" not in terms
    assert "de" not in terms
    assert "por" not in terms
    # Termos de domínio sim
    assert "valor" in terms
    assert "máximo" in terms
    assert "subvenção" in terms
    assert "projeto" in terms
    # Sintaxe correta de OR
    assert "|" in result
    assert " | " in result


def test_or_tsquery_dedup_termos_repetidos():
    """Repetições no input não devem duplicar no output."""
    q = "valor valor valor máximo máximo"
    result = _build_or_tsquery(q)
    terms = [t.strip() for t in result.split("|")]
    assert terms.count("valor") == 1
    assert terms.count("máximo") == 1


def test_or_tsquery_cap_max_terms():
    q = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    result = _build_or_tsquery(q, max_terms=4)
    assert result.count("|") == 3  # 4 termos → 3 separadores


def test_or_tsquery_min_len_filtra_termos_curtos():
    q = "valor de subvenção ou não"
    result = _build_or_tsquery(q, min_len=4)
    terms = [t.strip() for t in result.split("|")]
    assert "valor" in terms
    assert "subvenção" in terms
    # 'não' tem 3 chars → cai com min_len=4
    assert "não" not in terms


def test_or_tsquery_vazio_se_so_stopwords():
    assert _build_or_tsquery("") == ""
    assert _build_or_tsquery("   ") == ""
    assert _build_or_tsquery("a o de em") == ""
    assert _build_or_tsquery("???") == ""


def test_or_tsquery_preserva_acentos():
    """`to_tsquery('portuguese', ...)` precisa receber acentos pra casar o
    lexema correto (o stemmer português normaliza, mas o input deve estar
    íntegro pra o parser não cuspir warning)."""
    q = "Quais critérios de avaliação possuem pontuação?"
    result = _build_or_tsquery(q)
    assert "critérios" in result or "criterios" in result.lower()
    assert "avaliação" in result or "avaliacao" in result.lower()


def test_or_tsquery_ignora_digitos():
    """Dígitos isolados não viram tsquery — só letras."""
    q = "Qual o valor de R$ 30 milhões para o projeto 768?"
    result = _build_or_tsquery(q)
    terms = [t.strip() for t in result.split("|")]
    assert "valor" in terms
    assert "milhões" in terms
    # Sem dígitos no output
    assert not any(t.isdigit() for t in terms)
    assert "30" not in terms
    assert "768" not in terms


# =============================================================================
# Dedup por source_file
# =============================================================================

def _build_by_id(*items: tuple[str, str]) -> dict[str, dict]:
    """Helper: monta o dict by_id usado pelo retriever (id → row)."""
    return {cid: {"id": cid, "source_file": src, "text": f"t{cid}", "chunk_index": 0,
                  "section": None, "page_range": None}
            for cid, src in items}


def test_dedup_limita_chunks_do_mesmo_pdf():
    """Cenário real: Edital.pdf tem 3 chunks no top-3. Com max_per_source=2,
    cede 1 slot pros próximos arquivos."""
    by_id = _build_by_id(
        ("c1", "Edital.pdf"),
        ("c2", "Edital.pdf"),
        ("c3", "Edital.pdf"),  # 3º chunk do MESMO PDF
        ("c4", "FAQ.pdf"),
        ("c5", "Anexo_I.pdf"),
    )
    ranked = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7), ("c4", 0.6), ("c5", 0.5)]
    out = _dedup_by_source(ranked, by_id, k=4, max_per_source=2)
    ids = [r["id"] for r in out]
    # 2 melhores do Edital, depois FAQ e Anexo entram
    assert ids == ["c1", "c2", "c4", "c5"]


def test_dedup_nao_agrupa_versoes_de_mesmo_documento():
    """LIMITAÇÃO conhecida: source_file é comparado literal. `FAQ.pdf` e
    `FAQ_Versão_2.pdf` são fontes distintas pra dedup — ambos passam. Pra
    agrupar versões seria necessário normalizar o nome (heurística frágil)
    ou comparar conteúdo (caro). Aceitamos a limitação por enquanto."""
    by_id = _build_by_id(
        ("c1", "FAQ.pdf"),
        ("c2", "FAQ_Versão_2.pdf"),
        ("c3", "FAQ_Versão_3.pdf"),
    )
    ranked = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7)]
    out = _dedup_by_source(ranked, by_id, k=3, max_per_source=1)
    assert len(out) == 3  # nenhum filtrado — buckets distintos


def test_dedup_max_per_source_um_da_diversidade_maxima():
    """Com max_per_source=1, garante 1 chunk por arquivo."""
    by_id = _build_by_id(
        ("c1", "Edital.pdf"),
        ("c2", "Edital.pdf"),
        ("c3", "Edital.pdf"),
        ("c4", "FAQ.pdf"),
        ("c5", "Anexo_I.pdf"),
    )
    ranked = [("c1", 0.9), ("c2", 0.85), ("c3", 0.8), ("c4", 0.6), ("c5", 0.5)]
    out = _dedup_by_source(ranked, by_id, k=3, max_per_source=1)
    sources = [r["source_file"] for r in out]
    assert sources == ["Edital.pdf", "FAQ.pdf", "Anexo_I.pdf"]


def test_dedup_zero_desativa():
    """max_per_source=0 desativa a dedup — retorna top-k sem filtro."""
    by_id = _build_by_id(
        ("c1", "Edital.pdf"),
        ("c2", "Edital.pdf"),
        ("c3", "Edital.pdf"),
    )
    ranked = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7)]
    out = _dedup_by_source(ranked, by_id, k=3, max_per_source=0)
    assert [r["id"] for r in out] == ["c1", "c2", "c3"]


def test_dedup_pode_retornar_menos_que_k_se_corpus_estreito():
    """Se só há 2 fontes diferentes e max_per_source=1, retorna 2 mesmo
    quando k=5. Não enche com duplicatas."""
    by_id = _build_by_id(
        ("c1", "Edital.pdf"),
        ("c2", "Edital.pdf"),
        ("c3", "FAQ.pdf"),
        ("c4", "FAQ.pdf"),
    )
    ranked = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7), ("c4", 0.6)]
    out = _dedup_by_source(ranked, by_id, k=5, max_per_source=1)
    assert len(out) == 2
    assert [r["source_file"] for r in out] == ["Edital.pdf", "FAQ.pdf"]


def test_dedup_preserva_score_no_resultado():
    by_id = _build_by_id(("c1", "Edital.pdf"))
    out = _dedup_by_source([("c1", 0.123)], by_id, k=5, max_per_source=2)
    assert out[0]["score"] == 0.123


def test_dedup_source_none_e_tratado_como_chave_propria():
    """Chunks sem source_file (NULL no DB) compartilham bucket próprio —
    não viram fonte "ilimitada"."""
    by_id = {
        "c1": {"id": "c1", "source_file": None, "text": "t1"},
        "c2": {"id": "c2", "source_file": None, "text": "t2"},
        "c3": {"id": "c3", "source_file": "Edital.pdf", "text": "t3"},
    }
    ranked = [("c1", 0.9), ("c2", 0.8), ("c3", 0.7)]
    out = _dedup_by_source(ranked, by_id, k=3, max_per_source=1)
    # c1 entra (source=None), c2 NÃO (mesmo bucket None), c3 entra (Edital)
    assert [r["id"] for r in out] == ["c1", "c3"]


# =============================================================================
# format_chunks_for_prompt — labelling de chunks primário vs análogo (Etapa 2)
# =============================================================================

def _chunk(
    edital_id: str | None = None,
    section: str | None = "Art. 1",
    source: str | None = "Edital.pdf",
    text: str = "texto do trecho",
    page: str | None = "1-2",
) -> dict:
    """Helper: monta um dict de chunk como retornado por retrieve_chunks."""
    return {
        "id": "abc",
        "edital_id": edital_id,
        "chunk_index": 0,
        "text": text,
        "section": section,
        "source_file": source,
        "page_range": page,
        "score": 0.5,
    }


def test_format_chunks_empty():
    """Sem chunks → string vazia (não emite header)."""
    assert format_chunks_for_prompt([]) == ""
    assert format_chunks_for_prompt([], edital_ids=["601"]) == ""


def test_format_chunks_no_edital_ids():
    """Sem `edital_ids`, formato igual ao comportamento anterior — nenhum
    prefixo "Análogo" mesmo que o chunk tenha edital_id."""
    chunks = [_chunk(edital_id="602", section="Art. 5")]
    out = format_chunks_for_prompt(chunks)
    assert "Análogo" not in out
    assert "[Trecho 1 — Art. 5]" in out


def test_format_chunks_primary_no_prefix():
    """Chunk do edital primário não recebe prefixo."""
    chunks = [_chunk(edital_id="601", section="Art. 3.2")]
    out = format_chunks_for_prompt(chunks, edital_ids=["601"])
    assert "Análogo" not in out
    assert "[Trecho 1 — Art. 3.2]" in out


def test_format_chunks_analogue_prefixed():
    """Chunk de edital diferente do primário recebe prefixo "Análogo X — "."""
    chunks = [_chunk(edital_id="602", section="Art. 3.2")]
    out = format_chunks_for_prompt(chunks, edital_ids=["601", "602"])
    assert "[Trecho 1 — Análogo 602 — Art. 3.2]" in out


def test_format_chunks_mixed():
    """Mistura: primário sem prefixo, análogo com prefixo."""
    chunks = [
        _chunk(edital_id="601", section="Art. 1", source="Edital601.pdf"),
        _chunk(edital_id="602", section="Art. 2", source="Edital602.pdf"),
    ]
    out = format_chunks_for_prompt(chunks, edital_ids=["601", "602"])
    # Primário sem prefixo
    assert "[Trecho 1 — Art. 1]" in out
    # Análogo com prefixo explícito
    assert "[Trecho 2 — Análogo 602 — Art. 2]" in out


def test_format_chunks_analogue_sem_section_usa_fallback():
    """Quando o chunk não tem `section`, o label do análogo ainda
    prefixa "Análogo X — sem seção"."""
    chunks = [_chunk(edital_id="602", section=None)]
    out = format_chunks_for_prompt(chunks, edital_ids=["601", "602"])
    assert "Análogo 602 — sem seção" in out


def test_format_chunks_sem_edital_id_no_chunk_e_sem_prefixo():
    """Chunks que (por qualquer motivo) não carregam edital_id NÃO devem
    receber prefixo de análogo — não há como afirmar que são de outro edital."""
    chunks = [_chunk(edital_id=None, section="Art. 1")]
    out = format_chunks_for_prompt(chunks, edital_ids=["601"])
    assert "Análogo" not in out
    assert "[Trecho 1 — Art. 1]" in out
