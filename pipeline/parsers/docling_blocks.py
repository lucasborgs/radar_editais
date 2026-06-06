"""
Parser estruturado via Docling → blocos silver (candidato do bake-off, WIKI.md §11/§12).

Docling detecta de forma confiável o TIPO de cada item (section_header / text /
list_item / table); o NÍVEL de aninhamento vem da numeração no texto do
cabeçalho ("4.1." → profundidade 2), via `base.blocks_from_typed` — o mesmo
construtor de estrutura usado para texto-plano (FAPESP). Um modelo de estrutura
só, não dois caminhos.

Docling é dependência PESADA (torch + modelos) e OPCIONAL: import tardio, dentro
da função. Roda no ingest (offline). NÃO importar no topo de caminhos de API.
"""
from __future__ import annotations

import logging
from pathlib import Path

from pipeline.adapters.base import blocks_from_typed

logger = logging.getLogger(__name__)

# Docling label → kind do schema silver. picture/page_header/page_footer = ruído.
_LABEL_TO_KIND = {
    "section_header": "heading",
    "title": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "footnote": "paragraph",
    "list_item": "list",
    "table": "table",
    "code": "paragraph",
    "caption": "paragraph",
}
_DROP_LABELS = {"picture", "page_header", "page_footer"}

_converter = None  # cache do DocumentConverter (carrega modelos 1×)


def _get_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        # PDF de edital já tem camada de texto → OCR é desperdício (latência).
        opts = PdfPipelineOptions(do_ocr=False)
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
    return _converter


def _page_of(item) -> int | None:
    prov = getattr(item, "prov", None) or []
    if prov:
        return getattr(prov[0], "page_no", None)
    return None


def blocks_from_docling(pdf_path: str | Path, doc_name: str | None = None) -> list[dict]:
    """Converte um PDF em blocos silver `{idx, doc, page, section_path, kind, text}`
    via Docling — determinístico (sem LLM). Lista vazia em falha (caller decide
    fallback, igual ao structurer)."""
    pdf_path = Path(pdf_path)
    doc_name = doc_name or pdf_path.name
    try:
        doc = _get_converter().convert(str(pdf_path)).document
    except Exception as e:  # noqa: BLE001
        logger.warning("docling_blocks: falha ao converter %s: %s", pdf_path.name, e)
        return []

    typed: list[tuple[str, str]] = []
    pages: list[int | None] = []
    for item, _level in doc.iterate_items():
        label = getattr(getattr(item, "label", None), "value", None)
        if label in _DROP_LABELS:
            continue
        kind = _LABEL_TO_KIND.get(label, "paragraph")
        if label == "table":
            try:
                text = item.export_to_markdown(doc=doc)
            except Exception:  # noqa: BLE001
                text = ""
        else:
            text = getattr(item, "text", "") or ""
        if not text.strip():
            continue
        typed.append((kind, text))
        pages.append(_page_of(item))

    blocks = blocks_from_typed(typed)  # injeta section_path via numeração
    # blocks_from_typed descarta itens vazios; re-alinhar páginas por contagem
    # não é confiável → anexa doc/page best-effort (page do 1º item da mesma ordem).
    out: list[dict] = []
    for idx, (b, pg) in enumerate(zip(blocks, pages, strict=False)):
        out.append({"idx": idx, "doc": doc_name, "page": pg, **b})
    return out
