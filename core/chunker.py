"""
Structural chunker for edital documents (RADAR §4.5).

Pure function: splits a Portuguese legal-style text (FINEP edital) into chunks
aligned with article/section boundaries (Art., Artigo, Capítulo, Seção, §).
Target ~800 tokens per chunk, 150-token overlap, 80 min, 1500 max.

The chunker is intentionally I/O-free — it takes a string, returns a list of
dicts. PDF extraction, embedding and persistence live elsewhere.

Token counting is an approximation: words * 1.3 (rough heuristic for
Portuguese). This is good enough for chunk-size budgeting; we never feed this
count back to the LLM provider.
"""
from __future__ import annotations

import re

# =============================================================================
# CONFIG
# =============================================================================

TARGET_TOKENS = 800
OVERLAP_TOKENS = 150
MIN_TOKENS = 80
MAX_TOKENS = 1500

# Split on newline followed by a structural marker. We *keep* the marker on the
# following chunk (lookahead) so each chunk starts with its own anchor.
#
# Two families of markers:
#   1. Legal/jurídico — "Art.", "Artigo", "Capítulo", "Seção", "§". Usado em
#      legislação propriamente dita e raramente em editais FINEP.
#   2. FINEP decimal top-level — "N. TITULO" onde TITULO começa com 2+ letras
#      maiúsculas (ex.: "1. OBJETIVO", "12. CRONOGRAMA"). Restringir a 2+
#      maiúsculas filtra dois falsos positivos: (a) listas inline "1. a... 2. b..."
#      e (b) seções Title Case que na prática são parágrafos numerados, não
#      títulos. NÃO usa `\d+(\.\d+)*` para evitar capturar subseções "1.1.",
#      "1.4.6." (esses são conteúdo do chunk, não fronteira).
#      A heurística "primeira palavra em CAIXA-ALTA" foi calibrada contra o
#      corpus FINEP — todos os títulos top-level são caixa-alta.
#   3. FINEP vocabulary — "Desafio Tecnológico N" (marcador específico).
_STRUCTURAL_SPLIT_RE = re.compile(
    r"\n(?="
    r"Art\.|Artigo|Capítulo|CAPÍTULO|Seção|SEÇÃO|§|"
    r"\d+\.\s+[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ]{2,}|"
    r"Desafio Tecnológico\s+\d+"
    r")"
)

# Heuristic regex to recognise a section title in the FIRST line of a chunk.
# Mesma família de marcadores que `_STRUCTURAL_SPLIT_RE`. Usa `(?i:...)` em
# vez de `re.IGNORECASE` global porque o padrão decimal precisa ser
# case-sensitive (`[A-Z]{2,}` filtraria mal com IGNORECASE).
_SECTION_TITLE_RE = re.compile(
    r"^\s*(?:"
    r"(?i:Art\.|Artigo|Capítulo|Seção)|"
    r"CAPÍTULO|SEÇÃO|§|"
    r"\d+\.\s+[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇ]{2,}|"
    r"(?i:Desafio Tecnológico)\s+\d+"
    r")"
)


# Metadata detection — content-type flags úteis pra filtro no retrieval.
# Lista deliberadamente curta: cada flag tem que (a) ser barata de detectar e
# (b) ter caso de uso concreto numa query do writer. Adicionar flag sem
# query-pattern correspondente é dívida.
_META_DATE_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_META_MONEY_RE = re.compile(r"R\$\s*[\d.,]+|\bmilhões\b|\bbilhões\b", re.IGNORECASE)
_META_ELIG_RE = re.compile(
    r"\belegív|\bproponente|\bexecutora|\bcoexecutora|\bICT\b|\bCNPJ\b",
    re.IGNORECASE,
)
_META_CRIT_RE = re.compile(
    r"\bnota\b|\bpeso\b|\bcrit[ée]rio|\bpontua|\bclassifica",
    re.IGNORECASE,
)
# Detecta linha-separadora de tabela Markdown: `| --- | --- |...` ou `|---|---|`.
# Tolerante a espaços e a 3+ traços (`---`, `----`, etc.).
_META_TABLE_RE = re.compile(r"\|\s*-{3,}\s*\|")


def _detect_metadata(text: str) -> dict:
    """Detecta flags de tipo de conteúdo de um chunk.

    Retorna um dict JSON-serializable destinado à coluna `metadata` (jsonb)
    em `edital_chunks`. As flags servem como filtro adicional no retrieval
    (ex.: "qual o prazo?" → `metadata->>'contem_data' = true`).
    """
    if not text:
        return {
            "contem_tabela": False,
            "contem_data": False,
            "contem_valor_financeiro": False,
            "contem_elegibilidade": False,
            "contem_criterios": False,
        }
    return {
        # Markdown table separator — inserido por `_table_to_markdown` em tasks.py.
        "contem_tabela": bool(_META_TABLE_RE.search(text)),
        "contem_data": bool(_META_DATE_RE.search(text)),
        "contem_valor_financeiro": bool(_META_MONEY_RE.search(text)),
        "contem_elegibilidade": bool(_META_ELIG_RE.search(text)),
        "contem_criterios": bool(_META_CRIT_RE.search(text)),
    }

# Word-to-token ratio for Portuguese (approximate).
_WORD_TO_TOKEN = 1.3


# =============================================================================
# HELPERS
# =============================================================================

def _approx_tokens(text: str) -> int:
    """Approximate token count via whitespace word split * 1.3."""
    if not text:
        return 0
    return int(len(text.split()) * _WORD_TO_TOKEN)


def _extract_section(text: str) -> str | None:
    """If the chunk starts with a recognisable section header, return the
    first line (stripped). Otherwise None."""
    if not text:
        return None
    first_line = text.lstrip().split("\n", 1)[0].strip()
    if not first_line:
        return None
    if _SECTION_TITLE_RE.match(first_line):
        # Keep section labels short — clamp at 200 chars for DB sanity.
        return first_line[:200]
    return None


def _split_oversize(text: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Split a too-large chunk along paragraph boundaries (\n\n).

    Falls back to single-newline splits when paragraphs themselves are too
    large. Never returns an empty list — at minimum returns [text].
    """
    if _approx_tokens(text) <= max_tokens:
        return [text]

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        # No paragraph structure — split by single newline as last resort.
        paragraphs = [p for p in text.split("\n") if p.strip()]
        if len(paragraphs) <= 1:
            # Pathological single huge line — accept oversize rather than corrupt.
            return [text]

    result: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for para in paragraphs:
        para_tokens = _approx_tokens(para)
        if buf and buf_tokens + para_tokens > max_tokens:
            result.append("\n\n".join(buf))
            buf = [para]
            buf_tokens = para_tokens
        else:
            buf.append(para)
            buf_tokens += para_tokens
    if buf:
        result.append("\n\n".join(buf))
    return result


def _tail_overlap(text: str, n_tokens: int = OVERLAP_TOKENS) -> str:
    """Return the trailing ~n_tokens of `text` to use as prefix overlap.

    Token-counted, but split on word boundaries — never mid-word.
    """
    if n_tokens <= 0 or not text:
        return ""
    words = text.split()
    if not words:
        return ""
    # Convert n_tokens back to a word count using the inverse heuristic.
    n_words = max(1, int(n_tokens / _WORD_TO_TOKEN))
    return " ".join(words[-n_words:])


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def chunk_edital(text: str, source_file: str | None = None) -> list[dict]:
    """Split edital text into chunks following article/section boundaries.

    Returns a list of dicts with keys:
        - chunk_index: int    (local to this call, 0-based)
        - text:        str    (chunk content, including overlap prefix)
        - section:     str|None
        - page_range:  None   (not inferred from concatenated text — v1)
        - source_file: str|None (echoed from the argument when provided)

    Pure function, no I/O.
    """
    if not text or not text.strip():
        return []

    # 1. Initial split on structural markers. Preserve marker by using a
    #    look-ahead in the regex (so the marker stays on the following piece).
    raw_pieces = _STRUCTURAL_SPLIT_RE.split(text)
    raw_pieces = [p.strip() for p in raw_pieces if p and p.strip()]

    if not raw_pieces:
        return []

    # 2. Pack pieces into chunks up to TARGET_TOKENS, splitting any piece that
    #    by itself exceeds MAX_TOKENS into paragraph-sized sub-pieces.
    expanded: list[str] = []
    for piece in raw_pieces:
        if _approx_tokens(piece) > MAX_TOKENS:
            expanded.extend(_split_oversize(piece, MAX_TOKENS))
        else:
            expanded.append(piece)

    chunks_text: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for piece in expanded:
        piece_tokens = _approx_tokens(piece)
        # Start a new chunk if adding this piece would exceed the target AND
        # the current buffer already has meaningful content.
        if buf and buf_tokens + piece_tokens > TARGET_TOKENS:
            chunks_text.append("\n\n".join(buf))
            buf = [piece]
            buf_tokens = piece_tokens
        else:
            buf.append(piece)
            buf_tokens += piece_tokens
    if buf:
        chunks_text.append("\n\n".join(buf))

    # 3. Merge tail chunks shorter than MIN_TOKENS into their predecessor —
    #    avoids tiny dangling chunks at the end (or anywhere if a section
    #    happens to be very short).
    merged: list[str] = []
    for piece in chunks_text:
        if merged and _approx_tokens(piece) < MIN_TOKENS:
            merged[-1] = merged[-1] + "\n\n" + piece
        else:
            merged.append(piece)

    # 4. Apply overlap: prepend tail of previous chunk to current chunk.
    #    Skip the first chunk (no predecessor) and chunks that already start
    #    with the structural marker we want to preserve (overlap goes BEFORE).
    final_texts: list[str] = []
    for i, body in enumerate(merged):
        if i == 0:
            final_texts.append(body)
            continue
        overlap = _tail_overlap(merged[i - 1], OVERLAP_TOKENS)
        if overlap:
            final_texts.append(f"[…] {overlap}\n\n{body}")
        else:
            final_texts.append(body)

    # 5. Build the output dicts. Section title is read from the *body*
    #    (post-overlap-prefix) so we don't pick up the overlap's first line.
    #    Metadata é detectado sobre o `full` (com overlap) — overlap pode
    #    legitimamente trazer pra cá uma data ou valor que pertence à seção
    #    anterior, e isso é desejável pro retrieval.
    result: list[dict] = []
    for idx, (body, full) in enumerate(zip(merged, final_texts, strict=False)):
        result.append({
            "chunk_index": idx,
            "text": full,
            "section": _extract_section(body),
            "page_range": None,
            "source_file": source_file,
            "metadata": _detect_metadata(full),
        })

    return result
