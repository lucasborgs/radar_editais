"""
ETL Section Index (Radar de Editais)

Lê silver_data_enriched/editais_enriched.parquet e gera um índice de seções
estruturais por documento em silver_data/section_index/{id}.json.

Usado pela feature Writing para recuperar seções específicas de um edital
sem precisar carregar o documento inteiro no contexto do LLM.

Estratégias de detecção (prioridade decrescente):
  1. raw_html  → BeautifulSoup com h2/h3/h4 (mais preciso, preserva estrutura real)
  2. Por fonte → BNDES: split " | " | EMBRAPII: campos parquet | FAPESP/FINEP: regex
  3. Genérico  → paragrafos agrupados (fallback universal)
"""

import json
import logging
import re
from pathlib import Path

import pandas as pd

from config import SILVER_ENRICHED_DIR, SECTION_INDEX_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ENRICHED_FILE = SILVER_ENRICHED_DIR / "editais_enriched.parquet"

# Padrões de seção numerada: "1. Título", "1.1 Título", "TÍTULO EM CAPS:"
_NUMBERED_SECTION_RE = re.compile(
    r"(?<!\w)(\d+(?:\.\d+)*\.?\s+[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ][^\n]{3,60})"
)
_CAPS_HEADER_RE = re.compile(
    r"(?<!\w)([A-ZÁÉÍÓÚÂÊÔÀÃÕÇ]{4,}(?:\s+[A-ZÁÉÍÓÚÂÊÔÀÃÕÇ]{2,})*\s*:)"
)

# Títulos de seção comuns em editais brasileiros (para label inference)
_SECTION_LABELS = [
    ("objeto",          "Objeto"),
    ("objetivo",        "Objetivos"),
    ("elegibilidade",   "Elegibilidade"),
    ("quem pode",       "Quem Pode Participar"),
    ("público",         "Público-Alvo"),
    ("cronograma",      "Cronograma"),
    ("recurso",         "Recursos e Valores"),
    ("valor",           "Recursos e Valores"),
    ("financ",          "Itens Financiáveis"),
    ("taxa",            "Taxas e Condições"),
    ("juros",           "Taxas e Condições"),
    ("documento",       "Documentação Exigida"),
    ("habilitação",     "Documentação Exigida"),
    ("critério",        "Critérios de Avaliação"),
    ("avaliação",       "Critérios de Avaliação"),
    ("submissão",       "Como Submeter"),
    ("inscrição",       "Como Submeter"),
    ("contato",         "Contato e Informações"),
    ("informação",      "Contato e Informações"),
]


def _detect_sections_html(raw_html: str) -> list[dict]:
    """
    Detecção de seções a partir do HTML bruto usando headers estruturais (h2, h3, h4).
    Mais preciso que regex: usa a estrutura HTML original do documento.

    Returns:
        Lista de seções, ou [] se não houver headers suficientes.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    if not raw_html or len(raw_html) < 100:
        return []

    soup = BeautifulSoup(raw_html, "html.parser")
    headers = soup.find_all(["h2", "h3", "h4"])

    if len(headers) < 2:
        return []

    sections = []
    for header in headers:
        title = header.get_text(strip=True)
        if not title or len(title) < 3 or len(title) > 150:
            continue

        # Coleta conteúdo até o próximo header do mesmo nível ou superior
        content_parts = []
        for sib in header.next_siblings:
            if getattr(sib, "name", None) in ["h2", "h3", "h4"]:
                break
            if hasattr(sib, "get_text"):
                text = sib.get_text(" ", strip=True)
                if text:
                    content_parts.append(text)
            elif isinstance(sib, str) and sib.strip():
                content_parts.append(sib.strip())

        content = " ".join(content_parts).strip()
        # Inclui a seção mesmo com conteúdo curto se o título for significativo
        if content or len(title) > 10:
            sections.append({"title": title, "content": content})

    return sections if len(sections) >= 2 else []


def _infer_label(text: str) -> str:
    """Tenta inferir um label semântico para o conteúdo de uma seção."""
    text_lower = text.lower()
    for keyword, label in _SECTION_LABELS:
        if keyword in text_lower:
            return label
    return "Informações Gerais"


# =============================================================================
# ESTRATÉGIAS DE DETECÇÃO POR FONTE
# =============================================================================

def _detect_sections_bndes(description: str, row: pd.Series) -> list[dict]:
    """
    BNDES: o parser do etl_silver monta description como:
      "descricao | Quem pode solicitar: X | O que financia: Y"
    Basta split por ' | '.
    """
    if not description:
        return []

    parts = [p.strip() for p in description.split(" | ") if p.strip()]
    if len(parts) <= 1:
        return _detect_sections_generic(description)

    sections = []
    labels_used = ["Descrição Geral", "Quem Pode Participar", "O Que Financia",
                   "Itens Financiáveis", "Condições", "Como Contratar"]
    for i, part in enumerate(parts):
        label = labels_used[i] if i < len(labels_used) else _infer_label(part)
        sections.append({"title": label, "content": part})
    return sections


def _detect_sections_embrapii(description: str, row: pd.Series) -> list[dict]:
    """
    EMBRAPII: cada unidade tem campos estruturados.
    Reconstrói seções a partir dos campos do parquet.
    """
    sections = []

    if description:
        sections.append({"title": "Descrição da Unidade", "content": description})

    category = str(row.get("category") or "")
    if category and category != "nan":
        sections.append({"title": "Competências Tecnológicas", "content": category})

    themes = row.get("themes")
    if themes is not None:
        import numpy as np
        if isinstance(themes, np.ndarray):
            themes = themes.tolist()
        if isinstance(themes, list) and themes:
            sections.append({
                "title": "Áreas Temáticas",
                "content": ", ".join(str(t) for t in themes),
            })

    location = str(row.get("location") or "")
    if location and location != "nan":
        sections.append({"title": "Localização", "content": location})

    return sections if sections else _detect_sections_generic(description)


def _detect_sections_numbered(description: str) -> list[dict]:
    """
    FAPESP / FINEP: tenta encontrar seções numeradas (1. Título, 1.1 Título)
    ou headers em CAPS no texto.
    """
    if not description:
        return []

    # Encontra todas as posições de cabeçalhos numerados
    matches = list(_NUMBERED_SECTION_RE.finditer(description))

    if len(matches) >= 2:
        sections = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(description)
            content = description[start:end].strip()
            title = match.group(1).strip().rstrip(".")
            if len(content) > 30:
                sections.append({"title": title, "content": content})
        if sections:
            return sections

    # Fallback: CAPS headers
    matches = list(_CAPS_HEADER_RE.finditer(description))
    if len(matches) >= 2:
        sections = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(description)
            content = description[start:end].strip()
            title = match.group(1).strip().rstrip(":")
            if len(content) > 30:
                sections.append({"title": title, "content": content})
        if sections:
            return sections

    return []


def _detect_sections_generic(description: str) -> list[dict]:
    """
    Fallback genérico: divide por parágrafos e agrupa em blocos mínimos de 200 chars.
    Garante que todo documento tem ao menos uma seção.
    """
    if not description:
        return [{"title": "Conteúdo", "content": ""}]

    paragraphs = [p.strip() for p in re.split(r"\s{2,}|\n{2,}", description) if p.strip()]

    if not paragraphs:
        return [{"title": "Conteúdo", "content": description}]

    # Agrupa parágrafos curtos com o próximo
    sections = []
    buffer = ""
    for para in paragraphs:
        buffer = f"{buffer} {para}".strip() if buffer else para
        if len(buffer) >= 200:
            sections.append({"title": _infer_label(buffer), "content": buffer})
            buffer = ""
    if buffer:
        if sections:
            sections[-1]["content"] += f" {buffer}"
        else:
            sections.append({"title": _infer_label(buffer), "content": buffer})

    # Deduplica títulos
    seen_titles: dict[str, int] = {}
    for sec in sections:
        t = sec["title"]
        if t in seen_titles:
            seen_titles[t] += 1
            sec["title"] = f"{t} ({seen_titles[t]})"
        else:
            seen_titles[t] = 1

    return sections


# =============================================================================
# DISPATCHER
# =============================================================================

DETECTORS = {
    "BNDES":    _detect_sections_bndes,
    "EMBRAPII": _detect_sections_embrapii,
    "FAPESP":   lambda desc, row: _detect_sections_numbered(desc) or _detect_sections_generic(desc),
    "FINEP":    lambda desc, row: _detect_sections_numbered(desc) or _detect_sections_generic(desc),
}


def detect_sections(source: str, description: str, row: pd.Series) -> list[dict]:
    # Estratégia 1: HTML bruto — mais preciso quando disponível (scrapers novos)
    raw_html = str(row.get("raw_html") or "")
    if raw_html and raw_html != "nan":
        html_sections = _detect_sections_html(raw_html)
        if html_sections:
            return html_sections

    # Estratégia 2: detecção por fonte (plain text)
    detector = DETECTORS.get(source, lambda d, r: _detect_sections_generic(d))
    return detector(description, row)


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def build_section_index(force: bool = False) -> None:
    """
    Lê editais_enriched.parquet e gera section_index/{id}.json para cada edital.

    Args:
        force: Se True, regenera mesmo que o arquivo já exista.
    """
    logger.info("=" * 60)
    logger.info("INICIANDO ETL SECTION INDEX")
    logger.info("=" * 60)

    if not ENRICHED_FILE.exists():
        logger.error(f"Arquivo não encontrado: {ENRICHED_FILE}")
        logger.error("Execute etl_enrichment.py primeiro.")
        return

    SECTION_INDEX_DIR.mkdir(parents=True, exist_ok=True)

    df = pd.read_parquet(ENRICHED_FILE)
    logger.info(f"Registros carregados: {len(df)}")
    # Compatibilidade com parquets gerados antes do step 2.0 (sem coluna raw_html)
    if "raw_html" not in df.columns:
        df["raw_html"] = None

    stats: dict[str, int] = {}
    skipped = 0

    for _, row in df.iterrows():
        edital_id = str(row["id"])
        source = str(row.get("source", ""))
        out_path = SECTION_INDEX_DIR / f"{edital_id}.json"

        if out_path.exists() and not force:
            skipped += 1
            continue

        description = str(row.get("description", "") or "")
        sections = detect_sections(source, description, row)

        payload = {
            "edital_id": edital_id,
            "source": source,
            "title": str(row.get("title", "") or ""),
            "sections": sections,
        }

        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        stats[source] = stats.get(source, 0) + 1

    logger.info("-" * 60)
    logger.info(f"Ignorados (já existiam): {skipped}")
    for source, count in sorted(stats.items()):
        logger.info(f"  {source}: {count} section indexes gerados")
    logger.info(f"  TOTAL gerados: {sum(stats.values())}")
    logger.info(f"Saída: {SECTION_INDEX_DIR}/")
    logger.info("=" * 60)
    logger.info("ETL SECTION INDEX CONCLUÍDO")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gera section index por edital")
    parser.add_argument("--force", action="store_true", help="Regenera todos os arquivos")
    args = parser.parse_args()
    build_section_index(force=args.force)
