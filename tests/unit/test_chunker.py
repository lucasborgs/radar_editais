"""
Testes do token counting do chunker (tiktoken + fallback heurístico).

O caso que motivou a mudança: tabelas Markdown têm poucas "palavras" (split por
whitespace) mas muitos tokens reais (pipes, números, traços) — a heurística
palavras×1.3 subestimava 2-3× e deixava chunks de tabela estourarem o budget.

Os testes de tiktoken assumem o BPE cacheado (CI/dev com rede no primeiro uso);
o fallback é testado forçando _ENCODER_FAILED.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import core.retrieval.chunker as chunker  # noqa: E402
from core.retrieval.chunker import (  # noqa: E402
    MAX_TOKENS,
    _approx_tokens,
    _split_oversize,
    _tail_overlap,
)

pytestmark = pytest.mark.unit

# Linha de tabela densa em tokens: 6 "palavras" no split por whitespace,
# mas ~25 tokens reais (pipes, número formatado, data).
_TABLE_ROW = "| R$ 1.234.567,89 | 31/12/2026 | Subvenção |"


def _heuristic(text: str) -> int:
    return int(len(text.split()) * chunker._WORD_TO_TOKEN)


# =============================================================================
# Contagem exata vs heurística
# =============================================================================

def test_tabela_subestimada_pela_heuristica():
    """A razão do item: em tabelas, a contagem exata é ≫ palavras×1.3."""
    table = "\n".join([_TABLE_ROW] * 50)
    exact = _approx_tokens(table)
    assert exact > _heuristic(table) * 2  # subestimação >2× confirmada


def test_prosa_heuristica_e_exata_proximas():
    """Em prosa PT-BR a heurística era razoável — a exata fica na vizinhança
    (sanidade: a troca não distorce o caso comum)."""
    prose = (
        "O presente edital tem por objetivo selecionar propostas de projetos "
        "de inovação tecnológica desenvolvidos por empresas brasileiras. " * 20
    )
    exact = _approx_tokens(prose)
    heur = _heuristic(prose)
    assert 0.6 * heur <= exact <= 1.8 * heur


def test_fallback_sem_tiktoken(monkeypatch):
    """Encoder indisponível (sem dep / sem rede) → heurística, nunca quebra."""
    monkeypatch.setattr(chunker, "_ENCODER", None)
    monkeypatch.setattr(chunker, "_ENCODER_FAILED", True)
    text = "uma frase qualquer com sete palavras aqui"
    assert _approx_tokens(text) == _heuristic(text)


def test_approx_tokens_vazio():
    assert _approx_tokens("") == 0
    assert _approx_tokens(None) == 0


# =============================================================================
# _split_oversize — a regressão que o tiktoken corrige
# =============================================================================

def test_split_oversize_quebra_tabela_que_a_heuristica_deixava_passar():
    """Tabela com contagem exata > MAX_TOKENS mas heurística < MAX_TOKENS:
    antes passava inteira (chunk estourado); agora é quebrada."""
    table = "\n".join([_TABLE_ROW] * 130)
    # Pré-condição do cenário: pela heurística esta tabela CABERIA num chunk
    # (o código antigo não quebrava)…
    assert _heuristic(table) < MAX_TOKENS
    # …mas a contagem exata mostra que estoura o budget.
    assert _approx_tokens(table) > MAX_TOKENS
    pieces = _split_oversize(table, MAX_TOKENS)
    assert len(pieces) > 1
    for p in pieces:
        assert _approx_tokens(p) <= MAX_TOKENS


def test_split_oversize_preserva_texto_pequeno():
    assert _split_oversize("texto curto", MAX_TOKENS) == ["texto curto"]


# =============================================================================
# _tail_overlap — cauda não estoura o budget em texto denso
# =============================================================================

def test_tail_overlap_encolhe_em_texto_denso():
    """Cauda de tabela: a estimativa por palavras superdimensiona; com tiktoken
    a cauda encolhe até caber no budget (folga de 1 iteração de halving)."""
    table = "\n".join([_TABLE_ROW] * 100)
    tail = _tail_overlap(table, n_tokens=150)
    assert tail  # nunca vazio para texto não-vazio
    assert _approx_tokens(tail) <= 150


def test_tail_overlap_prosa_comportamento_estavel():
    prose = "palavra " * 500
    tail = _tail_overlap(prose.strip(), n_tokens=150)
    assert 0 < _approx_tokens(tail) <= 150


def test_tail_overlap_vazio_e_zero():
    assert _tail_overlap("", 150) == ""
    assert _tail_overlap("texto", 0) == ""


def test_chunk_preserva_metadados_de_autoridade():
    blocks = [{
        "doc": "Regulamento.pdf", "page": 4,
        "section_path": ["4.3 Itens Financiáveis"], "kind": "paragraph",
        "text": "Pagamento de pessoal para pesquisadores.",
        "document_metadata": {
            "authority_state": "vigente", "revision": 3,
            "published_at": "2026-02-09",
        },
    }]
    chunks = chunker.chunk_from_blocks(blocks)
    assert chunks[0]["metadata"]["authority_state"] == "vigente"
    assert chunks[0]["metadata"]["revision"] == 3
