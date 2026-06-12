"""
Chunker agnóstico à fonte (WIKI.md §11, L2 / §12 L3b).

Consome blocos silver do structurer (com `section_path` e `kind`) e agrupa
em chunks respeitando fronteira de seção e documento. Target ~800 tokens
por chunk, 150-token overlap, 80 min, 1500 max.

Função pura: recebe blocos, retorna lista de dicts. Sem I/O, sem PDF, sem
conhecimento de fonte. A discoverya/extração vive em `pipeline/adapters/<source>.py`.

Token counting: tiktoken (cl100k_base — o tokenizer do text-embedding-3-large,
cujo budget de 8192 é o que o chunk não pode estourar). A heurística antiga
(palavras × 1.3) subestimava brutalmente tabelas Markdown (muitos pipes/traços,
poucas "palavras") — um chunk de tabela podia passar 2-3× do budget real,
diluindo a representação. Fallback para a heurística se o tiktoken estiver
ausente ou sem rede pra baixar o BPE (primeiro uso) — nunca quebra o ingest.
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

# Metadata detection — content-type flags consumidas no retrieval: o retriever
# detecta a intenção da query (core/retrieval/retriever.py _QUERY_FLAG_PATTERNS)
# e dá boost RRF aos chunks cuja flag casa. Lista deliberadamente curta: cada
# flag tem que (a) ser barata de detectar e (b) ter query-pattern correspondente
# no retriever — flag sem consumo é dívida (contem_tabela é a exceção tolerada:
# não há pergunta natural de usuário que peça "tabela").
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
        # Markdown table separator — emitido por blocos kind=table do structurer.
        "contem_tabela": bool(_META_TABLE_RE.search(text)),
        "contem_data": bool(_META_DATE_RE.search(text)),
        "contem_valor_financeiro": bool(_META_MONEY_RE.search(text)),
        "contem_elegibilidade": bool(_META_ELIG_RE.search(text)),
        "contem_criterios": bool(_META_CRIT_RE.search(text)),
    }

# Word-to-token ratio for Portuguese (approximate) — caminho de FALLBACK
# quando o tiktoken está indisponível.
_WORD_TO_TOKEN = 1.3

# Encoder tiktoken cacheado por processo. None + _ENCODER_FAILED=True significa
# "tentamos e falhou" (dep ausente, sem rede pro download do BPE) — não retenta
# a cada chamada.
_ENCODER = None
_ENCODER_FAILED = False


def _get_encoder():
    global _ENCODER, _ENCODER_FAILED
    if _ENCODER is not None or _ENCODER_FAILED:
        return _ENCODER
    try:
        import tiktoken
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 — ImportError ou falha de rede no fetch do BPE
        _ENCODER_FAILED = True
    return _ENCODER


# =============================================================================
# HELPERS
# =============================================================================

def _approx_tokens(text: str) -> int:
    """Token count exato (tiktoken cl100k_base) com fallback heurístico."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return int(len(text.split()) * _WORD_TO_TOKEN)


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

    Token-counted, but split on word boundaries — never mid-word. A estimativa
    inicial é a heurística inversa (palavras); quando o tiktoken está
    disponível, encolhe a cauda se a contagem exata estourar o budget (caso
    típico: cauda de tabela, onde 1 "palavra" ≫ 1.3 tokens).
    """
    if n_tokens <= 0 or not text:
        return ""
    words = text.split()
    if not words:
        return ""
    # Convert n_tokens back to a word count using the inverse heuristic.
    n_words = max(1, int(n_tokens / _WORD_TO_TOKEN))
    tail = " ".join(words[-n_words:])
    enc = _get_encoder()
    if enc is not None:
        while n_words > 1 and len(enc.encode(tail)) > n_tokens:
            n_words = max(1, n_words // 2)
            tail = " ".join(words[-n_words:])
    return tail


# =============================================================================
# SILVER ENTRY POINT (WIKI.md §11 — consome blocos do structurer)
# =============================================================================

_DROP_KINDS = {"boilerplate", "signature"}


def _page_range(pages: list[int]) -> str | None:
    if not pages:
        return None
    lo, hi = min(pages), max(pages)
    return f"p.{lo}" if lo == hi else f"p.{lo}-{hi}"


def chunk_from_blocks(blocks: list[dict]) -> list[dict]:
    """Chunkeia a partir dos blocos silver (§11) em vez de regex estrutural.

    Mesmo contrato de saída de `chunk_edital`, mas:
      - `section` vem de `section_path` (sempre populado, fonte-agnóstico)
      - `page_range` é inferido (blocos carregam página)
      - fronteira de chunk respeita seção E documento (não funde PDFs)
      - blocos boilerplate/signature são descartados (ruído pra RAG)
    """
    usable = [
        b for b in blocks
        if b.get("kind") not in _DROP_KINDS and (b.get("text") or "").strip()
    ]
    if not usable:
        return []

    # 1. Agrupa blocos contíguos enquanto doc e section_path não mudam e o
    #    budget não estoura. Bloco oversize é quebrado preservando metadados.
    groups: list[dict] = []  # {texts[], section, doc, pages[]}
    cur: dict | None = None

    def _flush():
        nonlocal cur
        if cur and cur["texts"]:
            groups.append(cur)
        cur = None

    for b in usable:
        sp = b.get("section_path") or []
        section = " > ".join(sp)[:200] if sp else None
        doc = b.get("doc")
        page = b.get("page")
        pieces = _split_oversize(b["text"].strip(), MAX_TOKENS)
        for piece in pieces:
            pt = _approx_tokens(piece)
            new_boundary = (
                cur is None
                or cur["section"] != section
                or cur["doc"] != doc
                or cur["tokens"] + pt > TARGET_TOKENS
            )
            if new_boundary:
                _flush()
                cur = {"texts": [], "section": section, "doc": doc,
                       "pages": set(), "tokens": 0}
            cur["texts"].append(piece)
            cur["tokens"] += pt
            if page is not None:
                cur["pages"].add(page)
    _flush()

    # 2. Funde grupos curtos (< MIN_TOKENS) no anterior, desde que mesma
    #    seção e doc (não atravessa fronteira semântica só por tamanho).
    merged: list[dict] = []
    for g in groups:
        g_text = "\n\n".join(g["texts"])
        if (
            merged
            and _approx_tokens(g_text) < MIN_TOKENS
            and merged[-1]["section"] == g["section"]
            and merged[-1]["doc"] == g["doc"]
        ):
            merged[-1]["texts"].extend(g["texts"])
            merged[-1]["pages"] |= g["pages"]
        else:
            merged.append(g)

    # 3. Overlap: prefixa a cauda do chunk anterior (mesmo doc).
    result: list[dict] = []
    for idx, g in enumerate(merged):
        body = "\n\n".join(g["texts"])
        full = body
        if idx > 0 and merged[idx - 1]["doc"] == g["doc"]:
            ov = _tail_overlap("\n\n".join(merged[idx - 1]["texts"]), OVERLAP_TOKENS)
            if ov:
                full = f"[…] {ov}\n\n{body}"
        result.append({
            "chunk_index": idx,
            "text": full,
            "section": g["section"],
            "page_range": _page_range(sorted(g["pages"])),
            "source_file": g["doc"],
            "metadata": _detect_metadata(full),
        })
    return result
