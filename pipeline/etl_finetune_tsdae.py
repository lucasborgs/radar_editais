"""
ETL — Corpus TSDAE (Fine-Tuning Não-Supervisionado)

Prepara o corpus de treinamento para TSDAE (Denoising Auto-Encoder) do modelo
AlBERTina (PORTULAN/albertina-900m-portuguese-ptbr-encoder-brwac).

TSDAE aprende vocabulário de domínio de fomento (subvenção econômica, TRL, CNPJ,
matching Embrapii, etc.) sem necessidade de labels — base para o estágio
supervisionado com pares (query → search_document).

Fontes:
  - FAPESP: body_text dos JSONs em bronze_data/fapesp_pages/
  - FINEP:  texto extraído dos PDFs de editais em bronze_data/finep_pdfs/

Saída:
  finetune_data/tsdae_corpus.jsonl   — corpus completo com metadados
  finetune_data/tsdae_train.txt      — textos de treino (80%), 1 por linha
  finetune_data/tsdae_val.txt        — textos de validação (20%), 1 por linha
  finetune_data/tsdae_stats.json     — estatísticas e breakdown

Treinamento (Google Colab com GPU):
  Ver bloco # ── COLAB ── abaixo (após main()), ou rode:
    python3 etl_finetune_tsdae.py --colab-script > tsdae_colab.py

Uso:
  python3 etl_finetune_tsdae.py                  # roda tudo
  python3 etl_finetune_tsdae.py --fapesp-only    # só FAPESP
  python3 etl_finetune_tsdae.py --finep-only     # só FINEP
  python3 etl_finetune_tsdae.py --colab-script   # imprime script de treino Colab
"""

import json
import logging
import random
import re
import sys
from pathlib import Path
from typing import Optional

import warnings
import pdfplumber
# pdfplumber emite warnings sobre cores inválidas em PDFs antigos — suprimir
warnings.filterwarnings("ignore", message="Cannot set gray")

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

FAPESP_PAGES_DIR = Path("bronze_data/fapesp_pages")
FINEP_PDFS_DIR = Path("bronze_data/finep_pdfs")
FINEP_MANIFEST = Path("bronze_data/finep_pdf_manifest.json")
OUTPUT_DIR = Path("finetune_data")

CORPUS_FILE = OUTPUT_DIR / "tsdae_corpus.jsonl"
TRAIN_FILE = OUTPUT_DIR / "tsdae_train.txt"
VAL_FILE = OUTPUT_DIR / "tsdae_val.txt"
STATS_FILE = OUTPUT_DIR / "tsdae_stats.json"

# Segmentação
MIN_WORDS = 40       # segmentos com menos de N palavras são descartados
MAX_WORDS = 300      # segmentos com mais de N palavras são divididos
TARGET_WORDS = 150   # alvo ao dividir segmentos longos

# Split treino/validação
VAL_RATIO = 0.20
RANDOM_SEED = 42

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# =============================================================================
# LIMPEZA DE TEXTO
# =============================================================================

# Ligaduras comuns em PDFs (raramente visíveis, mas por segurança)
_LIGATURES = str.maketrans({
    "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "ft", "ﬆ": "st", "\x00": "", "\ufeff": "",
})

# Padrão para linhas que são apenas número de página
_PAGE_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$")

# Padrão para "Página X de Y" e variações
_PAGE_LABEL_RE = re.compile(
    r"^\s*(p[aá]gina|page|p\.)\s*\d+(\s*(de|of|\/)\s*\d+)?\s*$",
    re.IGNORECASE,
)

# Padrão para numeração de seção no início de linha (ex: "1.2.3 Critérios")
_SECTION_NUM_RE = re.compile(r"^\s*\d+[\.\d]*[\.\s]")

# Padrão para linha em CAIXA ALTA (tipicamente cabeçalho de seção)
_ALL_CAPS_RE = re.compile(r"^[A-ZÁÉÍÓÚÂÊÎÔÛÃÕÇÀÜÝ\s\d\W]{4,}$")

# Palavras que sozinhas numa linha indicam artefato HTML (bold inline de FAPESP)
_MIN_LINE_WORDS = 3  # linhas com menos de N palavras são tratadas como artefatos


def _clean_ligatures(text: str) -> str:
    return text.translate(_LIGATURES)


def _remove_repeated_lines(lines: list[str], max_occurrences: int = 3) -> list[str]:
    """Remove linhas que aparecem repetidamente (cabeçalhos/rodapés de PDF)."""
    from collections import Counter
    stripped = [l.strip() for l in lines]
    freq = Counter(s for s in stripped if len(s) > 5)
    removed = {s for s, c in freq.items() if c > max_occurrences}
    return [l for l, s in zip(lines, stripped) if s not in removed]


def _is_garbage_line(line: str) -> bool:
    """True se a linha é artefato (número de página, linha vazia, etc.)."""
    stripped = line.strip()
    if not stripped:
        return False  # linhas vazias são preservadas para demarcar parágrafos
    if _PAGE_NUM_RE.match(stripped):
        return True
    if _PAGE_LABEL_RE.match(stripped):
        return True
    return False


def _join_wrapped_lines(lines: list[str]) -> str:
    """
    Junta quebras de linha de word-wrap dentro de parágrafos.

    Uma quebra é considerada word-wrap (e deve ser juntada) quando:
      - A linha atual NÃO termina com pontuação terminal (. ? ! : ;)
      - A próxima linha NÃO começa com numeração de seção (1.2.3)
      - A próxima linha NÃO está em CAIXA ALTA (cabeçalho)
      - A próxima linha NÃO é vazia (separador de parágrafo)

    Linhas vazias são preservadas como separadores de parágrafo.
    """
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue
        if result and result[-1] != "":
            prev = result[-1]
            ends_sentence = bool(re.search(r"[.!?;:]\s*$", prev))
            next_is_section = bool(_SECTION_NUM_RE.match(stripped))
            next_is_caps = bool(_ALL_CAPS_RE.match(stripped)) and len(stripped) > 8
            # Junta se é word-wrap
            if not ends_sentence and not next_is_section and not next_is_caps:
                result[-1] = prev + " " + stripped
                continue
        result.append(stripped)
    return "\n".join(result)


def clean_pdf_text(raw_text: str) -> str:
    """
    Limpa texto extraído de PDF do FINEP:
    1. Corrige ligaduras
    2. Remove linhas de número de página / cabeçalhos
    3. Detecta e remove cabeçalhos/rodapés repetitivos
    4. Junta quebras de linha de word-wrap
    """
    text = _clean_ligatures(raw_text)
    lines = text.split("\n")

    # Remove linhas de número de página antes de detectar repetições
    lines = [l for l in lines if not _is_garbage_line(l)]

    # Remove cabeçalhos/rodapés repetitivos (>3 ocorrências exatas)
    lines = _remove_repeated_lines(lines, max_occurrences=3)

    # Junta word-wrap
    cleaned = _join_wrapped_lines(lines)

    # Normaliza múltiplas linhas vazias em no máximo duas
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def clean_fapesp_text(raw_text: str) -> str:
    """
    Limpa body_text do FAPESP (extraído de HTML):
    - Remove linhas órfãs de 1-2 palavras (artefatos de bold/links do CMS)
    - Junta fragmentos que ficaram isolados
    - Normaliza espaçamento
    """
    lines = raw_text.split("\n")
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # Linha vazia: marca separador de parágrafo
            if result and result[-1] != "":
                result.append("")
            continue

        word_count = len(stripped.split())

        if word_count < _MIN_LINE_WORDS:
            # Linha curta: provavelmente artefato HTML (bold inline, link, etc.)
            # Tenta juntar com a linha anterior, se ela não for separador
            if result and result[-1] != "":
                result[-1] = result[-1] + " " + stripped
            # Se não houver contexto anterior, descarta (muito curta para ser útil)
            continue

        # Linha normal: verifica se deve ser juntada à anterior (word-wrap)
        if result and result[-1] != "":
            prev = result[-1]
            ends_sentence = bool(re.search(r"[.!?;:]\s*$", prev))
            next_is_section = bool(_SECTION_NUM_RE.match(stripped))
            next_is_caps = bool(_ALL_CAPS_RE.match(stripped)) and len(stripped) > 10
            if not ends_sentence and not next_is_section and not next_is_caps:
                result[-1] = prev + " " + stripped
                continue

        result.append(stripped)

    cleaned = "\n".join(result)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# =============================================================================
# SEGMENTAÇÃO
# =============================================================================

def _split_long_paragraph(paragraph: str, target_words: int = TARGET_WORDS) -> list[str]:
    """Divide parágrafo longo em chunks de ~target_words palavras (split em '.')."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for sent in sentences:
        sent_words = len(sent.split())
        if current_words + sent_words > target_words and current:
            chunks.append(" ".join(current))
            current = [sent]
            current_words = sent_words
        else:
            current.append(sent)
            current_words += sent_words

    if current:
        chunks.append(" ".join(current))

    return [c for c in chunks if len(c.split()) >= MIN_WORDS]


def segment_text(text: str, doc_id: str, source: str, **extra_meta) -> list[dict]:
    """
    Divide o texto limpo em segmentos de treinamento.
    Retorna lista de dicts com 'text', 'id', 'source', 'word_count'.
    """
    # Divide por parágrafos (separados por \n\n ou \n)
    # Para FAPESP (só \n): trata cada linha de >=MIN_WORDS como parágrafo
    # Para FINEP (tem \n\n): divide por parágrafos reais
    if "\n\n" in text:
        raw_paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    else:
        # Agrega linhas consecutivas em blocos de TARGET_WORDS
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
        if words > MAX_WORDS:
            # Divide em pedaços menores
            sub = _split_long_paragraph(para)
        else:
            sub = [para]

        for chunk in sub:
            wc = len(chunk.split())
            if wc < MIN_WORDS:
                continue
            # Normaliza espaço final
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
# EXTRAÇÃO — FAPESP
# =============================================================================

def _select_fapesp_pdfs() -> list[Path]:
    return sorted(FAPESP_PAGES_DIR.glob("*.json"))


def process_fapesp() -> list[dict]:
    """Extrai e segmenta body_text de todos os JSONs de páginas FAPESP."""
    files = _select_fapesp_pdfs()
    log.info("FAPESP: %d arquivos JSON encontrados", len(files))

    all_segments: list[dict] = []
    skipped = 0

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Erro ao ler %s: %s", f, e)
            continue

        chamada_id = data.get("chamada_id", f.stem)
        titulo = data.get("titulo", "")
        body_text = data.get("body_text", "")
        body_chars = data.get("body_chars", 0)

        if not body_text or body_chars < 200:
            skipped += 1
            continue

        cleaned = clean_fapesp_text(body_text)
        segments = segment_text(
            cleaned,
            doc_id=f"FAPESP_{chamada_id}",
            source="FAPESP",
            chamada_id=chamada_id,
            titulo=titulo,
        )
        all_segments.extend(segments)

    log.info(
        "FAPESP: %d segmentos gerados (%d arquivos sem body_text ignorados)",
        len(all_segments), skipped,
    )
    return all_segments


# =============================================================================
# EXTRAÇÃO — FINEP
# =============================================================================

# Palavras-chave que identificam PDFs de edital (no campo nome_documento)
_EDITAL_KEYWORDS = re.compile(
    r"\b(edital|chamada\s+p[úu]blica|regulamento|sele[çc][aã]o\s+p[úu]blica)\b",
    re.IGNORECASE,
)

# Palavras-chave que excluem PDFs secundários (resultados, erratas, etc.)
_EXCLUDE_KEYWORDS = re.compile(
    r"\b(resultado|habilitac|habilitaç|convocac|convocaç|prorrogac|prorrogaç|"
    r"comunicado|errata|retificac|retificaç|corrigend|recurso|adendo|"
    r"rerratificac|rerratificaç)\b",
    re.IGNORECASE,
)


def _select_primary_pdf(pdfs: list[dict]) -> Optional[dict]:
    """
    Seleciona o PDF principal de um edital FINEP.

    Prioridade:
      1. nome_documento contém palavras de edital E NÃO contém exclusões
         → pega o mais recente (por ordem no manifest, que é cronológico)
      2. Fallback: qualquer PDF não excluído
    """
    # Filtra PDFs disponíveis localmente
    available = [
        p for p in pdfs
        if p.get("baixado") and Path(p.get("arquivo_local", "")).exists()
    ]
    if not available:
        return None

    # Candidatos primários (contêm "edital" sem ser resultado)
    primaries = [
        p for p in available
        if _EDITAL_KEYWORDS.search(p.get("nome_documento", ""))
        and not _EXCLUDE_KEYWORDS.search(p.get("nome_documento", ""))
    ]

    if primaries:
        # Prefere a versão rerratificada mais recente (primeira no manifest = mais recente)
        return primaries[0]

    # Fallback: qualquer PDF não excluído
    fallbacks = [
        p for p in available
        if not _EXCLUDE_KEYWORDS.search(p.get("nome_documento", ""))
    ]
    return fallbacks[0] if fallbacks else None


def _extract_pdf_text(pdf_path: Path) -> str:
    """Extrai texto de todas as páginas de um PDF com pdfplumber."""
    pages_text: list[str] = []
    try:
        with pdfplumber.open(pdf_path) as doc:
            for page in doc.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
    except Exception as e:
        log.debug("pdfplumber erro em %s: %s", pdf_path.name, e)
        return ""

    # Junta páginas com separador de parágrafo
    return "\n\n".join(pages_text)


def process_finep() -> list[dict]:
    """Extrai e segmenta texto dos PDFs primários de editais FINEP."""
    if not FINEP_MANIFEST.exists():
        log.warning("Manifest FINEP não encontrado: %s", FINEP_MANIFEST)
        return []

    manifest = json.loads(FINEP_MANIFEST.read_text(encoding="utf-8"))
    log.info("FINEP: %d chamadas no manifest", len(manifest))

    all_segments: list[dict] = []
    no_pdf = 0
    failed = 0

    for chamada_id, data in manifest.items():
        pdfs = data.get("pdfs", [])
        titulo = data.get("titulo", "")
        primary = _select_primary_pdf(pdfs)

        if primary is None:
            no_pdf += 1
            continue

        pdf_path = Path(primary["arquivo_local"])
        raw_text = _extract_pdf_text(pdf_path)

        if not raw_text or len(raw_text) < 300:
            failed += 1
            log.debug("FINEP %s: texto insuficiente em %s", chamada_id, pdf_path.name)
            continue

        cleaned = clean_pdf_text(raw_text)
        segments = segment_text(
            cleaned,
            doc_id=f"FINEP_{chamada_id}",
            source="FINEP",
            chamada_id=chamada_id,
            titulo=titulo,
            pdf_nome=primary.get("nome_documento", ""),
            pdf_file=pdf_path.name,
        )

        if not segments:
            failed += 1
            continue

        all_segments.extend(segments)

    log.info(
        "FINEP: %d segmentos gerados (%d sem PDF primário, %d extrações vazias)",
        len(all_segments), no_pdf, failed,
    )
    return all_segments


# =============================================================================
# SPLIT E SALVAMENTO
# =============================================================================

def _split_stratified(
    segments: list[dict], val_ratio: float, seed: int
) -> tuple[list[dict], list[dict]]:
    """
    Split 80/20 estratificado por source (FAPESP/FINEP) para manter
    proporção de cada fonte no treino e na validação.
    """
    rng = random.Random(seed)

    by_source: dict[str, list[dict]] = {}
    for seg in segments:
        by_source.setdefault(seg["source"], []).append(seg)

    train: list[dict] = []
    val: list[dict] = []

    for source, items in by_source.items():
        rng.shuffle(items)
        n_val = max(1, int(len(items) * val_ratio))
        val.extend(items[:n_val])
        train.extend(items[n_val:])

    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


def save_corpus(segments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Salva corpus JSONL + arquivos .txt de treino e validação."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Corpus completo (JSONL)
    with CORPUS_FILE.open("w", encoding="utf-8") as f:
        for seg in segments:
            f.write(json.dumps(seg, ensure_ascii=False) + "\n")
    log.info("Corpus salvo: %s (%d segmentos)", CORPUS_FILE, len(segments))

    # Split estratificado
    train_segs, val_segs = _split_stratified(segments, VAL_RATIO, RANDOM_SEED)

    # Arquivos .txt (1 texto por linha — formato esperado pelo sentence-transformers)
    with TRAIN_FILE.open("w", encoding="utf-8") as f:
        for seg in train_segs:
            # Substitui quebras de linha internas por espaço (garante 1 doc por linha)
            text = seg["text"].replace("\n", " ")
            f.write(text + "\n")

    with VAL_FILE.open("w", encoding="utf-8") as f:
        for seg in val_segs:
            text = seg["text"].replace("\n", " ")
            f.write(text + "\n")

    log.info(
        "Split: %d treino / %d validação (%.0f%% / %.0f%%)",
        len(train_segs), len(val_segs),
        100 * len(train_segs) / len(segments),
        100 * len(val_segs) / len(segments),
    )
    return train_segs, val_segs


def save_stats(
    all_segments: list[dict],
    train_segs: list[dict],
    val_segs: list[dict],
) -> None:
    """Salva estatísticas do corpus."""
    from collections import Counter

    by_source = Counter(s["source"] for s in all_segments)
    word_counts = [s["word_count"] for s in all_segments]

    stats = {
        "total_segments": len(all_segments),
        "train_segments": len(train_segs),
        "val_segments": len(val_segs),
        "by_source": dict(by_source),
        "word_count": {
            "min": min(word_counts),
            "max": max(word_counts),
            "mean": round(sum(word_counts) / len(word_counts), 1),
            "median": sorted(word_counts)[len(word_counts) // 2],
        },
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "config": {
            "min_words": MIN_WORDS,
            "max_words": MAX_WORDS,
            "target_words": TARGET_WORDS,
            "val_ratio": VAL_RATIO,
        },
    }

    STATS_FILE.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Stats salvas: %s", STATS_FILE)

    # Resumo no console
    print("\n" + "=" * 60)
    print("CORPUS TSDAE — RESUMO")
    print("=" * 60)
    print(f"  Total de segmentos : {stats['total_segments']:,}")
    print(f"  Treino             : {stats['train_segments']:,}")
    print(f"  Validação          : {stats['val_segments']:,}")
    print()
    for src, n in sorted(by_source.items()):
        print(f"  {src:<12} : {n:,} segmentos")
    print()
    wc = stats["word_count"]
    print(f"  Palavras/seg       : min={wc['min']}  med={wc['median']}  max={wc['max']}  μ={wc['mean']}")
    print()
    print(f"  Arquivos gerados:")
    print(f"    {CORPUS_FILE}")
    print(f"    {TRAIN_FILE}")
    print(f"    {VAL_FILE}")
    print(f"    {STATS_FILE}")
    print("=" * 60)


# =============================================================================
# SCRIPT DE TREINAMENTO COLAB
# =============================================================================

COLAB_SCRIPT = '''#!/usr/bin/env python3
"""
Treinamento TSDAE — AlBERTina para Domínio de Fomento
======================================================
Execute no Google Colab com GPU (T4 ou superior).

Setup Colab:
    !pip install -q sentence-transformers

Upload dos arquivos:
    from google.colab import files
    # Faça upload de tsdae_train.txt e tsdae_val.txt
"""

import logging
from pathlib import Path
from torch.utils.data import DataLoader
from sentence_transformers import SentenceTransformer, LoggingHandler
from sentence_transformers.datasets import DenoisingAutoEncoderDataset
from sentence_transformers.losses import DenoisingAutoEncoderLoss

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
    handlers=[LoggingHandler()],
)

# ── Hiperparâmetros ──────────────────────────────────────────────────────────
MODEL_NAME = "PORTULAN/albertina-900m-portuguese-ptbr-encoder-brwac"
TRAIN_FILE = "tsdae_train.txt"
VAL_FILE   = "tsdae_val.txt"
OUTPUT_DIR = "models/albertina_fomento_tsdae"
BATCH_SIZE = 4          # T4: 4-8; A100: 16-32
EPOCHS = 1              # TSDAE converge rápido; 1 epoch geralmente suficiente
WARMUP_STEPS = 200
DELETION_RATIO = 0.60   # Fração de tokens deletados (padrão do paper TSDAE)
LR = 3e-5

# ── Carrega dados ────────────────────────────────────────────────────────────
train_sentences = [
    line.strip() for line in open(TRAIN_FILE, encoding="utf-8")
    if line.strip()
]
print(f"Treino: {len(train_sentences):,} segmentos")

# ── Modelo ───────────────────────────────────────────────────────────────────
model = SentenceTransformer(MODEL_NAME)

# ── Dataset TSDAE ────────────────────────────────────────────────────────────
# DenoisingAutoEncoderDataset deleta aleatoriamente deletion_ratio dos tokens
# e treina o modelo a reconstruir o texto original.
train_dataset = DenoisingAutoEncoderDataset(
    train_sentences,
    noise_fn=None,        # usa a função padrão de deleção aleatória
)
train_dataloader = DataLoader(
    train_dataset,
    batch_size=BATCH_SIZE,
    shuffle=True,
    drop_last=True,
)

# ── Loss TSDAE ───────────────────────────────────────────────────────────────
# tie_encoder_decoder=True: encoder e decoder compartilham pesos (AlBERTina)
# deletion_ratio: fração de tokens deletados na entrada corrompida
train_loss = DenoisingAutoEncoderLoss(
    model,
    decoder_name_or_path=MODEL_NAME,
    tie_encoder_decoder=True,
    deletion_ratio=DELETION_RATIO,
)

# ── Treino ───────────────────────────────────────────────────────────────────
model.fit(
    train_objectives=[(train_dataloader, train_loss)],
    epochs=EPOCHS,
    warmup_steps=WARMUP_STEPS,
    optimizer_params={"lr": LR},
    show_progress_bar=True,
    use_amp=True,                        # FP16 — obrigatório para T4
    checkpoint_path=OUTPUT_DIR + "_ckpt",
    checkpoint_save_steps=500,
    output_path=OUTPUT_DIR,
)

print(f"\\nModelo salvo em: {OUTPUT_DIR}")
print("Próximo passo: baixe o diretório e extraia em radar_editais/models/albertina_fomento_tsdae/")
print("Em seguida, apague chroma_db/ e re-rode etl_gold_vectors.py para re-embedar com o modelo fine-tuned.")
'''


def print_colab_script() -> None:
    print(COLAB_SCRIPT)


# =============================================================================
# MAIN
# =============================================================================

def main(fapesp_only: bool = False, finep_only: bool = False) -> None:
    log.info("Iniciando preparação do corpus TSDAE")

    all_segments: list[dict] = []

    if not finep_only:
        fapesp_segs = process_fapesp()
        all_segments.extend(fapesp_segs)

    if not fapesp_only:
        finep_segs = process_finep()
        all_segments.extend(finep_segs)

    if not all_segments:
        log.error("Nenhum segmento gerado. Verifique os diretórios de entrada.")
        sys.exit(1)

    train_segs, val_segs = save_corpus(all_segments)
    save_stats(all_segments, train_segs, val_segs)


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--colab-script" in args:
        print_colab_script()
        sys.exit(0)

    fapesp_only = "--fapesp-only" in args
    finep_only = "--finep-only" in args

    main(fapesp_only=fapesp_only, finep_only=finep_only)
