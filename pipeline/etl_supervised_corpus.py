"""
ETL — Corpus Supervisionado: BNDES + Embrapii

Lê:
  bronze_data/bndes_raw/*.html   — páginas de programas BNDES salvas localmente
  bronze_data/bndes_raw/*.pdf    — PDFs de programas BNDES
  bronze_data/embrapii_raw/*.json — unidades Embrapii (saída do scraper)

Gera:
  finetune_data/supervised_corpus.jsonl  — chunks segmentados prontos para
                                           geração de pares sintéticos

Schema de cada linha:
  {"id": str, "source": str, "programa_id": str, "titulo": str,
   "word_count": int, "text": str}

Uso:
  python pipeline/etl_supervised_corpus.py
  python pipeline/etl_supervised_corpus.py --bndes-only
  python pipeline/etl_supervised_corpus.py --embrapii-only
"""

import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

import warnings
warnings.filterwarnings("ignore", message="Cannot set gray")

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

BNDES_DIR   = Path("bronze_data/bndes_raw")
EMBRAPII_DIR = Path("bronze_data/embrapii_raw")
OUTPUT_DIR  = Path("finetune_data")
OUTPUT_FILE = OUTPUT_DIR / "supervised_corpus.jsonl"

# Segmentação — mesmos parâmetros do corpus TSDAE para consistência
MIN_WORDS    = 40
MAX_WORDS    = 300
TARGET_WORDS = 150

# Mapeamento explícito de arquivo → (titulo_legível, programa_id)
_BNDES_FILE_MAP: dict[str, tuple[str, str]] = {
    "BNDES Garagem - BNDES.html":
        ("BNDES Garagem", "bndes_garagem"),
    "BNDES Funtec - Fundo de desenvolvimento técnico-científico - BNDES Apoio à Inovação.html":
        ("BNDES Funtec", "bndes_funtec"),
    "BNDES_FOLHETOMAISINOVACAOweb_25112025.pdf":
        ("BNDES Mais Inovação", "mais_inovacao"),
    "Fundo Clima.html":
        ("Fundo Clima", "fundo_clima"),
    "Fundo Clima - Desenvolvimento Urbano Resiliente e Sustentável.html":
        ("Fundo Clima - Desenvolvimento Urbano Resiliente e Sustentável", "fundo_clima_urbano"),
    "Fundo Clima - Florestas Nativas e Recursos Hídricos.html":
        ("Fundo Clima - Florestas Nativas e Recursos Hídricos", "fundo_clima_florestas"),
    "Fundo Clima - Indústria Verde.html":
        ("Fundo Clima - Indústria Verde", "fundo_clima_industria_verde"),
    "Fundo Clima - Logística de Transporte, Transporte Coletivo e Mobilidade Verdes.html":
        ("Fundo Clima - Logística e Mobilidade Verdes", "fundo_clima_logistica"),
    "Fundo Clima - Máquinas Verdes.html":
        ("Fundo Clima - Máquinas Verdes", "fundo_clima_maquinas_verdes"),
    "Fundo Clima - Serviços e inovação verdes.html":
        ("Fundo Clima - Serviços e Inovação Verdes", "fundo_clima_servicos_verdes"),
    "Fundo Clima - Transição Energética.html":
        ("Fundo Clima - Transição Energética", "fundo_clima_transicao_energetica"),
    "Programa MPME Inovadora.html":
        ("MPME Inovadora", "mpme_inovadora"),
}

# Linhas de compartilhamento social que aparecem no topo do contentEditContainer
_SOCIAL_NOISE = re.compile(r"^(curtir|compartilhar|tweetar|renderizado em|ações)$", re.I)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# SEGMENTAÇÃO (idêntico ao etl_finetune_tsdae)
# =============================================================================

def _split_long_paragraph(paragraph: str, target_words: int = TARGET_WORDS) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for sent in sentences:
        sw = len(sent.split())
        if current_words + sw > target_words and current:
            chunks.append(" ".join(current))
            current = [sent]
            current_words = sw
        else:
            current.append(sent)
            current_words += sw
    if current:
        chunks.append(" ".join(current))
    return [c for c in chunks if len(c.split()) >= MIN_WORDS]


def segment_text(text: str, doc_id: str, source: str, **extra_meta) -> list[dict]:
    """Divide texto em segmentos de treinamento."""
    if "\n\n" in text:
        raw_paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    else:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        raw_paragraphs = []
        current: list[str] = []
        current_words = 0
        for line in lines:
            lw = len(line.split())
            if current_words + lw > TARGET_WORDS and current:
                raw_paragraphs.append(" ".join(current))
                current = [line]
                current_words = lw
            else:
                current.append(line)
                current_words += lw
        if current:
            raw_paragraphs.append(" ".join(current))

    segments: list[dict] = []
    seg_idx = 0
    for para in raw_paragraphs:
        words = len(para.split())
        if words < MIN_WORDS:
            continue
        sub = _split_long_paragraph(para) if words > MAX_WORDS else [para]
        for chunk in sub:
            wc = len(chunk.split())
            if wc < MIN_WORDS:
                continue
            chunk = re.sub(r"\s+", " ", chunk).strip()
            segments.append({
                "id": f"{doc_id}_{seg_idx}",
                "source": source,
                "word_count": wc,
                "text": chunk,
                **extra_meta,
            })
            seg_idx += 1
    return segments


# =============================================================================
# EXTRAÇÃO — BNDES HTML
# =============================================================================

def _slugify(text: str) -> str:
    """Gera programa_id a partir de texto livre."""
    # NFD decompose → strip combining diacritics → lowercase → alphanumeric only
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _extract_bndes_html_text(filepath: Path) -> str:
    """Extrai texto principal do HTML do BNDES via div.contentEditContainer."""
    from bs4 import BeautifulSoup

    html = filepath.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    content = soup.find("div", class_="contentEditContainer")
    if not content:
        # Fallback: body inteiro
        content = soup.find("body") or soup

    lines = []
    for el in content.find_all(["p", "li", "h2", "h3", "h4"]):
        # separator=" " garante espaço entre texto e links inline ("Nos editais é")
        text = el.get_text(separator=" ", strip=True)
        # Colapsa múltiplos espaços gerados pelo separator
        text = re.sub(r" {2,}", " ", text).strip()
        if not text or _SOCIAL_NOISE.match(text):
            continue
        if len(text) > 10:
            lines.append(text)

    return "\n\n".join(lines)


def process_bndes_html(filepath: Path) -> list[dict]:
    # Normaliza para NFC antes do lookup (garante match independente do encoding do FS)
    titulo, programa_id = _BNDES_FILE_MAP.get(
        unicodedata.normalize("NFC", filepath.name),
        (filepath.stem, _slugify(filepath.stem)),
    )
    try:
        text = _extract_bndes_html_text(filepath)
    except Exception as e:
        log.warning("Erro ao processar %s: %s", filepath.name, e)
        return []

    if not text or len(text.split()) < MIN_WORDS:
        log.warning("Texto insuficiente em %s", filepath.name)
        return []

    doc_id = f"BNDES_{programa_id}"
    segments = segment_text(
        text,
        doc_id=doc_id,
        source="BNDES",
        programa_id=programa_id,
        titulo=titulo,
    )
    log.info("BNDES HTML %-55s → %d segmentos", filepath.name, len(segments))
    return segments


# =============================================================================
# EXTRAÇÃO — BNDES PDF
# =============================================================================

def _extract_pdf_text(filepath: Path) -> str:
    """Extrai texto de PDF com pdfplumber."""
    import pdfplumber

    pages_text: list[str] = []
    with pdfplumber.open(filepath) as doc:
        for page in doc.pages:
            text = page.extract_text()
            if text:
                pages_text.append(text)
    return "\n\n".join(pages_text)


def _clean_pdf_text(raw: str) -> str:
    """Remove artefatos comuns de PDFs (números de página, cabeçalhos repetidos)."""
    _PAGE_NUM = re.compile(r"^\s*\d{1,4}\s*$")
    _PAGE_LABEL = re.compile(
        r"^\s*(p[aá]gina|page|p\.)\s*\d+(\s*(de|of|\/)\s*\d+)?\s*$", re.I
    )
    lines = raw.split("\n")
    # Remove linhas de número de página
    lines = [l for l in lines if not _PAGE_NUM.match(l) and not _PAGE_LABEL.match(l)]
    # Remove cabeçalhos/rodapés repetitivos (>3 ocorrências)
    from collections import Counter
    freq = Counter(l.strip() for l in lines if len(l.strip()) > 5)
    removed = {s for s, c in freq.items() if c > 3}
    lines = [l for l in lines if l.strip() not in removed]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def process_bndes_pdf(filepath: Path) -> list[dict]:
    titulo, programa_id = _BNDES_FILE_MAP.get(
        unicodedata.normalize("NFC", filepath.name),
        (filepath.stem, _slugify(filepath.stem)),
    )
    try:
        raw = _extract_pdf_text(filepath)
    except Exception as e:
        log.warning("Erro ao processar PDF %s: %s", filepath.name, e)
        return []

    if not raw or len(raw.split()) < MIN_WORDS:
        log.warning("Texto insuficiente em %s", filepath.name)
        return []

    text = _clean_pdf_text(raw)
    doc_id = f"BNDES_{programa_id}"
    segments = segment_text(
        text,
        doc_id=doc_id,
        source="BNDES",
        programa_id=programa_id,
        titulo=titulo,
    )
    log.info("BNDES PDF  %-55s → %d segmentos", filepath.name, len(segments))
    return segments


# =============================================================================
# EXTRAÇÃO — EMBRAPII JSON
# =============================================================================

def _build_embrapii_text(unit: dict) -> str:
    """Monta texto de treinamento de uma unidade Embrapii."""
    parts: list[str] = []

    nome = unit.get("unidade_nome") or unit.get("titulo", "")
    instituicao = unit.get("instituicao_base", "")
    uf = unit.get("uf", "")

    # Cabeçalho descritivo
    header = nome
    if instituicao and instituicao.lower() != nome.lower():
        header += f" ({instituicao})"
    if uf:
        header += f" — {uf}"
    parts.append(header)

    # Corpo da descrição
    descricao = unit.get("descricao", "").strip()
    if descricao:
        parts.append(descricao)

    # Competências como bloco de texto
    competencias = unit.get("competencias", [])
    if competencias:
        comp_text = "Áreas de atuação e competências: " + "; ".join(competencias)
        parts.append(comp_text)

    return "\n\n".join(p for p in parts if p)


def process_embrapii_json(filepath: Path) -> list[dict]:
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Erro ao ler %s: %s", filepath.name, e)
        return []

    if not isinstance(data, list):
        data = [data]

    all_segments: list[dict] = []
    skipped = 0

    for unit in data:
        nome = unit.get("unidade_nome") or unit.get("titulo", "unidade")
        # programa_id baseado no nome da unidade (slug)
        programa_id = _slugify(nome)
        text = _build_embrapii_text(unit)

        if not text or len(text.split()) < MIN_WORDS:
            skipped += 1
            continue

        doc_id = f"EMBRAPII_{programa_id}"
        segments = segment_text(
            text,
            doc_id=doc_id,
            source="EMBRAPII",
            programa_id=programa_id,
            titulo=nome,
        )
        all_segments.extend(segments)

    log.info(
        "EMBRAPII JSON %-50s → %d unidades, %d segmentos (%d ignoradas)",
        filepath.name, len(data), len(all_segments), skipped,
    )
    return all_segments


# =============================================================================
# MAIN
# =============================================================================

def process_bndes() -> list[dict]:
    if not BNDES_DIR.exists():
        log.warning("Diretório BNDES não encontrado: %s", BNDES_DIR)
        return []

    all_segments: list[dict] = []

    for html_file in sorted(BNDES_DIR.glob("*.html")):
        all_segments.extend(process_bndes_html(html_file))

    for pdf_file in sorted(BNDES_DIR.glob("*.pdf")):
        all_segments.extend(process_bndes_pdf(pdf_file))

    log.info("BNDES total: %d segmentos", len(all_segments))
    return all_segments


def process_embrapii() -> list[dict]:
    if not EMBRAPII_DIR.exists():
        log.warning("Diretório Embrapii não encontrado: %s", EMBRAPII_DIR)
        return []

    json_files = sorted(EMBRAPII_DIR.glob("*.json"))
    if not json_files:
        log.warning(
            "Nenhum JSON Embrapii encontrado em %s. "
            "Execute o scraper primeiro: python scripts/run_all.py (ou EMBRAPIIScraper().run())",
            EMBRAPII_DIR,
        )
        return []

    all_segments: list[dict] = []
    for json_file in json_files:
        all_segments.extend(process_embrapii_json(json_file))

    log.info("EMBRAPII total: %d segmentos", len(all_segments))
    return all_segments


def main(bndes_only: bool = False, embrapii_only: bool = False) -> None:
    log.info("Iniciando ETL do corpus supervisionado")

    all_segments: list[dict] = []

    if not embrapii_only:
        all_segments.extend(process_bndes())

    if not bndes_only:
        all_segments.extend(process_embrapii())

    if not all_segments:
        log.error("Nenhum segmento gerado. Verifique os diretórios de entrada.")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for seg in all_segments:
            f.write(json.dumps(seg, ensure_ascii=False) + "\n")

    # Resumo
    from collections import Counter
    by_source = Counter(s["source"] for s in all_segments)
    wcs = [s["word_count"] for s in all_segments]

    print("\n" + "=" * 60)
    print("CORPUS SUPERVISIONADO — RESUMO")
    print("=" * 60)
    print(f"  Total de segmentos : {len(all_segments):,}")
    for src, n in sorted(by_source.items()):
        print(f"  {src:<12} : {n:,} segmentos")
    print(f"  Palavras/seg       : min={min(wcs)}  max={max(wcs)}  μ={sum(wcs)//len(wcs)}")
    print(f"\n  Arquivo gerado: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    args = sys.argv[1:]
    main(
        bndes_only="--bndes-only" in args,
        embrapii_only="--embrapii-only" in args,
    )
