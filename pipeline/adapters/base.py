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
from abc import ABC, abstractmethod
from typing import TypedDict

from core import wiki_schema


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
