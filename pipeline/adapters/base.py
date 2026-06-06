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
from abc import ABC, abstractmethod
from typing import TypedDict

from core import wiki_schema

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
    registry = wiki_schema.load().get("source_adapters", {})
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
# por unidade e o cliente LLM tem timeout de 60s (core.llm_client): uma unit
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
