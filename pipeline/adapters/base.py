"""
SourceAdapter — L1 do stack (WIKI.md §12).

Cada fonte de fomento implementa um adapter que converte seu bronze cru
(PDFs, HTML, API) no contrato agnóstico do Documento Canônico (§12.3). Tudo
acima (structurer, chunker, síntese) consome o contrato — não sabe qual é a
fonte.

Adicionar fonte: escrever `pipeline/adapters/<source>.py` com classe
`Adapter` herdando `SourceAdapter`, e registrar em WIKI.md §12.4. Nada
em L2/L3 muda.
"""
from __future__ import annotations

import importlib
import logging
import re
from abc import ABC, abstractmethod
from typing import TypedDict

from core.kg import schema

logger = logging.getLogger(__name__)


class CanonicalDocEntry(TypedDict):
    """Uma unidade do Documento Canônico (§12.3): um 'documento lógico'."""

    doc_name: str          # rótulo (ex: 'Edital.pdf', 'pagina_chamada')
    units: list[str]       # texto por unidade (1 unit/página de PDF, 1+ pra HTML)


CanonicalDoc = list[CanonicalDocEntry]


class SourceAdapter(ABC):
    """Converte raw bronze de uma fonte específica em Documento Canônico."""

    @abstractmethod
    def to_documents(self, edital_id: str) -> CanonicalDoc:
        """Retorna o conjunto canônico de documentos de um edital. Vazio
        significa 'sem conteúdo extraível' — o caller decide o fallback."""


def get_adapter(source: str) -> SourceAdapter:
    """Resolve o adapter pelo `source_adapters` registry (§12.4 WIKI.md).

    Convenção: cada módulo de adapter expõe uma classe `Adapter`.
    """
    registry = schema.source_adapters()
    entry = registry.get(source)
    if not entry or not entry.get("module"):
        raise ValueError(f"Source adapter não registrado em WIKI.md §12.4: {source!r}")
    module = importlib.import_module(entry["module"])
    return module.Adapter()


# =============================================================================
# UTILITÁRIOS L1 COMPARTILHADOS
# =============================================================================
# Helpers reusados por adapters de fontes HTML (FAPESP, web, …). Vivem em L1
# porque operam sobre o raw de fonte (HTML, texto longo) ANTES da fronteira do
# Documento Canônico — nada à direita (§12.1) os importa.

# Tamanho-alvo por unidade do Documento Canônico para fontes sem paginação
# nativa (HTML servido como corpo único). O structurer reproduz texto VERBATIM
# por unidade e o cliente LLM tem timeout de 60s (core.llm.llm_client): uma unit
# que force perto de max_tokens=4000 de saída (~40-80s a 50-80 tok/s) estoura o
# timeout. ~3500 chars (~900 tokens → saída verbatim ~1000-1200 tokens) fica
# bem abaixo. O contrato §12.3 prevê "HTML → 1 unit (ou split por âncora)".
_UNIT_MAX_CHARS = 3500


def split_into_units(text: str, max_chars: int = _UNIT_MAX_CHARS) -> list[str]:
    """Quebra um corpo de texto longo em unidades ~page-sized do Documento
    Canônico, em fronteira de parágrafo.

    Mantém parágrafos inteiros juntos até estourar `max_chars`; cai para quebra
    por linha simples se não houver parágrafos; nunca devolve lista vazia para
    texto não-vazio. Promovido de pipeline/adapters/fapesp.py (era cópia local)
    para ser reusado por toda fonte HTML.
    """
    paras = [p for p in text.split("\n\n") if p.strip()]
    if not paras:
        paras = [p for p in text.split("\n") if p.strip()] or [text]
    units: list[str] = []
    buf = ""
    for p in paras:
        if buf and len(buf) + len(p) + 2 > max_chars:
            units.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        units.append(buf)
    return units


# Cabeçalho numerado legal no INÍCIO de uma linha: "6.2.2) ...", "7) ...".
# Lookahead zero-width em `^` (MULTILINE) → posições de quebra de seção.
_NUMBERED_HEADER_RE = re.compile(r"(?m)^(?=[ \t]*\d+(?:\.\d+)*\)[ \t]+\S)")


def split_by_numbering(text: str, max_chars: int = _UNIT_MAX_CHARS) -> list[str]:
    """Quebra texto plano com hierarquia numerada legal (`6.2.2)`, `7)`) em
    units alinhadas a fronteira de seção — nunca corta no meio de uma seção.

    Recupera a estrutura que o achatamento HTML→texto deixou só como numeração
    (caso FAPESP). Segmenta no início de cada cabeçalho numerado, empacota
    segmentos contíguos até `max_chars`, e sub-divide por parágrafo
    (`split_into_units`) o segmento que sozinho estourar o teto. Sem numeração
    suficiente (< 2 cabeçalhos) → cai em `split_into_units` (mesmo contrato).

    Determinístico, sem I/O, sem LLM. É um helper de L1 (fonte-agnóstico no
    formato: serve qualquer texto com numeração legal — FAPESP, editais
    convertidos de PDF, etc.).
    """
    if not text or not text.strip():
        return []
    positions = [m.start() for m in _NUMBERED_HEADER_RE.finditer(text)]
    if len(positions) < 2:
        return split_into_units(text, max_chars)

    # Segmentos = [preâmbulo?] + [cabeçalho_i .. cabeçalho_{i+1})
    bounds = positions + [len(text)]
    segments: list[str] = []
    if positions[0] > 0 and text[: positions[0]].strip():
        segments.append(text[: positions[0]])
    for i in range(len(positions)):
        seg = text[bounds[i] : bounds[i + 1]]
        if seg.strip():
            segments.append(seg)

    # Empacota segmentos até max_chars; segmento gigante é sub-dividido.
    units: list[str] = []
    buf = ""
    for seg in segments:
        if len(seg) > max_chars:
            if buf:
                units.append(buf)
                buf = ""
            units.extend(split_into_units(seg, max_chars))
            continue
        if buf and len(buf) + len(seg) + 2 > max_chars:
            units.append(buf)
            buf = seg
        else:
            buf = f"{buf}\n\n{seg}" if buf else seg
    if buf:
        units.append(buf)
    return units


# Prefixo de numeração legal no início de um cabeçalho: "4.1.", "6.2.2)", "7)".
_NUM_PREFIX_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[).]")


def _heading_number(text: str) -> tuple[int, ...] | None:
    """Tupla de numeração de um cabeçalho ('4.1' → (4,1)) ou None se não-numerado."""
    m = _NUM_PREFIX_RE.match(text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _is_ancestor(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """True se `a` é prefixo numérico estrito de `b` ((4,) é ancestral de (4,1))."""
    return len(a) < len(b) and b[: len(a)] == a


def blocks_from_typed(items: list[tuple[str, str]]) -> list[dict]:
    """Constrói blocos `{section_path, kind, text}` a partir de itens TIPADOS em
    ordem de leitura — `(kind, text)`, kind ∈ heading/paragraph/list/table.

    O `section_path` (hierarquia) é derivado da NUMERAÇÃO no texto do cabeçalho
    (compartilhado entre Docling-detecta-heading e FAPESP texto-plano — um
    construtor de estrutura só, não dois caminhos). Cabeçalho não-numerado vira
    raiz. Função pura.
    """
    path: list[tuple[tuple[int, ...] | None, str]] = []  # (numtuple|None, texto)
    out: list[dict] = []
    for kind, raw in items:
        text = (raw or "").strip()
        if not text:
            continue
        if kind == "heading":
            num = _heading_number(text)
            if num is None:
                path = [(None, text)]  # não-numerado → nova raiz
            else:
                path = [e for e in path if e[0] is not None and _is_ancestor(e[0], num)]
                path.append((num, text))
        out.append({
            "section_path": [t for _, t in path],
            "kind": kind,
            "text": text,
        })
    return out


def blocks_from_numbered_text(text: str) -> list[dict]:
    """Blocos silver `{section_path, kind, text}` a partir de texto-plano com
    numeração legal (FAPESP) — DETERMINÍSTICO, sem LLM.

    Detecta linhas-cabeçalho numeradas (`6.2.2)`, `7)`) → kind=heading; o corpo
    entre cabeçalhos → kind=paragraph. O `section_path` sai da numeração via
    `blocks_from_typed`. É o caminho que dispensa o structurer-LLM para fontes
    com estrutura machine-readable (Fase 2 do plano). Sem numeração → 1 bloco
    paragraph (caller cai no fallback LLM se quiser).
    """
    if not text or not text.strip():
        return []
    positions = [m.start() for m in _NUMBERED_HEADER_RE.finditer(text)]
    if not positions:
        return blocks_from_typed([("paragraph", text)])

    items: list[tuple[str, str]] = []
    if text[: positions[0]].strip():
        items.append(("paragraph", text[: positions[0]]))
    bounds = positions + [len(text)]
    for i, p in enumerate(positions):
        seg = text[p : bounds[i + 1]]
        nl = seg.find("\n")
        header, body = (seg, "") if nl == -1 else (seg[:nl], seg[nl + 1:])
        items.append(("heading", header))
        if body.strip():
            items.append(("paragraph", body))
    return blocks_from_typed(items)


def html_to_text(html: str) -> str:
    """Extrai o conteúdo principal de HTML cru → texto limpo, fonte-agnóstico.

    Usa `trafilatura` (best-in-class para extração de conteúdo principal de
    páginas arbitrárias — descarta nav/rodapé/scripts/boilerplate). Se a lib
    estiver ausente ou não extrair nada, cai para um get_text() simples via
    BeautifulSoup (já dependência do projeto). Retorna "" se nada utilizável.

    A limpeza vive em L1 (não no scraper) de propósito: o bronze guarda HTML
    cru e re-limpamos a cada chunk run, então melhorar este extrator re-deriva
    o Documento Canônico SEM re-fetch (muda o source_hash → re-estrutura).
    """
    if not html or not html.strip():
        return ""

    # 1. Caminho principal: trafilatura (import tardio — dep opcional/pesada).
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if extracted and extracted.strip():
            return extracted.strip()
    except ImportError:
        logger.warning(
            "html_to_text: trafilatura ausente — usando fallback BeautifulSoup. "
            "Instale com `pip install trafilatura` para extração de melhor qualidade."
        )
    except Exception as e:
        logger.warning("html_to_text: trafilatura falhou (%s) — fallback bs4", e)

    # 2. Fallback: strip de tags via BeautifulSoup.
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
            tag.decompose()
        import re
        text = soup.get_text("\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    except Exception as e:
        logger.warning("html_to_text: fallback bs4 também falhou (%s)", e)
        return ""
