"""
Extração de fatos atômicos dos PDFs de editais FINEP.

Input:  bronze_data/finep_pdfs/{chamada_id}/*.pdf
Output: silver_data/finep/facts/{chamada_id}.json

Processo:
  1. Para cada edital, detecta seções lógicas no texto do PDF (Template A)
  2. Para cada seção relevante, LLM (Gemini Flash) extrai fatos atômicos
  3. Cache por hash MD5 do texto para evitar re-extração

Uso:
    python pipeline/etl_finep_facts.py --backend gemini
    python pipeline/etl_finep_facts.py --edital 782
    python pipeline/etl_finep_facts.py --backend gemini --dry-run
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from config import FINEP_PDFS_DIR, FINEP_FACTS_DIR

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

FACTS_DIR = FINEP_FACTS_DIR
CACHE_FILE = FINEP_FACTS_DIR.parent / ".facts_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# CLASSIFICADOR DE SEÇÕES (Template A — validado empiricamente em 28 PDFs)
# =============================================================================

SECTION_TYPES: dict[str, str] = {
    r"objeto|objetivo":                                    "OBJETIVO",
    r"desafio|linha.?tem|tema|tecnologia.?habilit":        "TEMAS",
    r"elegibil|participan|proponen|entidade.?p[uú]blica":  "ELEGIBILIDADE",
    r"caracter[ií]stica.+proposta":                        "CARACTERISTICAS_PROPOSTAS",
    r"despesa.?apoi[aá]|itens?.?n[aã]o.?financ":           "DESPESAS_APOIAVEIS",
    r"recurso.+financ|valores?.?solicit|disponib":           "RECURSOS",
    r"contrapartida":                                       "CONTRAPARTIDA",
    r"cronograma|prazo|calend":                             "CRONOGRAMA",
    r"apresenta[çc][aã]o.+(proposta|documenta)|envio":     "SUBMISSAO",
    r"diretrizes.+sele[çc]|processo.+sele[çc]|avalia[çc]|m[eé]rito": "AVALIACAO",
    r"resultado|delibera[çc]|interposi[çc]":                "RESULTADOS",
    r"contrata[çc][aã]o|repasse":                           "CONTRATACAO",
    r"base.?legal":                                         "BASE_LEGAL",
    r"acompanhamento|presta[çc][aã]o.?de.?conta":           "PRESTACAO_CONTAS",
    r"propriedade.?intelectual":                            "PROPRIEDADE_INTELECTUAL",
    r"[eé]tic|riscos?.+vi[eé]s":                            "ETICA",
    r"disposi[çc].+gera|disposi[çc].+fina|considera[çc].+fina": "DISPOSICOES_GERAIS",
}

# Seções prioritárias para extração de fatos (as mais úteis para matching)
PRIORITY_SECTIONS = {
    "OBJETIVO", "TEMAS", "ELEGIBILIDADE", "RECURSOS", "CRONOGRAMA",
    "AVALIACAO", "CARACTERISTICAS_PROPOSTAS", "DESPESAS_APOIAVEIS",
    "CONTRAPARTIDA", "SUBMISSAO",
}


def classify_section(title: str) -> str:
    """Classifica o tipo da seção baseado no título."""
    t = title.lower()
    for pattern, section_type in SECTION_TYPES.items():
        if re.search(pattern, t):
            return section_type
    return "OUTRO"


# =============================================================================
# DETECÇÃO DE SEÇÕES NO TEXTO DO PDF
# =============================================================================

def detect_sections(text: str) -> list[dict]:
    """Detecta seções numeradas no texto do edital.

    Retorna lista de dicts com: number, title, section_type, content.
    """
    lines = text.split("\n")
    sections = []

    # Pattern: seções numeradas "N. TÍTULO" ou "N - TÍTULO"
    numbered_re = re.compile(
        r"^\s*(\d+\.?\d*\.?)\s*[.\-–—]\s*(.+)$"
    )

    # Pattern: linha inteira em CAPS (mín 15 chars)
    caps_re = re.compile(
        r"^[A-ZÁÀÂÃÉÈÊÍÏÓÔÕÚÜÇ\s,]{15,}$"
    )

    last_num = float("inf")

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped or len(line_stripped) < 5:
            continue

        section = None

        # Tenta pattern numerado
        m = numbered_re.match(line_stripped)
        if m:
            num_str = m.group(1).rstrip(".")
            title = m.group(2).strip()

            # Aceita apenas nível 1 (sem ponto) para evitar sub-seções
            if "." not in num_str and len(title) > 3 and not title[0].isdigit():
                num_val = int(num_str)
                # Filtra numeração reiniciante (ex: tabela de cronograma)
                if num_val > last_num and num_val != last_num + 1:
                    continue
                last_num = num_val

                section = {
                    "line_num": i,
                    "number": num_str,
                    "title": title,
                }

        # Tenta CAPS header (somente se sem seção numerada)
        if not section and caps_re.match(line_stripped):
            lower = line_stripped.lower()
            # Ignora cabeçalhos de página
            if any(kw in lower for kw in ["ministério", "finep", "página", "cnpj", "endereço"]):
                continue
            if len(line_stripped.split()) >= 3:
                section = {
                    "line_num": i,
                    "number": "",
                    "title": line_stripped,
                }

        if section:
            section["section_type"] = classify_section(section["title"])
            sections.append(section)

    # Extrai conteúdo entre seções
    for idx, sec in enumerate(sections):
        start_line = sec["line_num"] + 1
        end_line = sections[idx + 1]["line_num"] if idx + 1 < len(sections) else len(lines)
        sec["content"] = "\n".join(lines[start_line:end_line]).strip()
        del sec["line_num"]  # não precisa persistir

    return sections


# =============================================================================
# EXTRAÇÃO DE TEXTO DOS PDFS
# =============================================================================

def extract_pdf_text(pdf_path: Path) -> str:
    """Extrai texto de um PDF local com pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber não instalado")
        return ""

    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
        return "\n".join(text_parts)
    except Exception as e:
        logger.warning("Erro ao parsear %s: %s", pdf_path.name, e)
        return ""


# =============================================================================
# CLASSIFICAÇÃO DE PDFs POR TIER
# =============================================================================

# Tier 1 — documento normativo principal (sempre processar, um por edital)
_TIER1_KEYWORDS = ["regulamento", "edital", "chamada"]

# Tier 2 — documento específico da chamada (processar se disponível)
_TIER2_KEYWORDS = [
    "caracteristicas_especificas",
    "detalhamento_das_linhas_tematicas",
    "instrucoes_para_a_inscricao",
    "estrutura_de_governanca",
]

# Tier Global — processar uma vez globalmente (não repetir por edital)
_TIER_GLOBAL_KEYWORDS = ["definicao_do_nivel_de_maturidade", "definicao_de_nivel_de_maturidade"]

# Skip — nunca processar
_SKIP_KEYWORDS = [
    "minuta", "declaracao", "carta_de_manifestacao", "faq",
    "apresentacao", "resultado", "oficio", "telas_fap",
    "orientacoes_para_apresentacao", "tabela_com_requisitos",
    "orientacoes_para_despesas", "relatorio_parcial",
]


def _matches_any(name: str, keywords: list[str]) -> bool:
    return any(kw in name for kw in keywords)


def classify_pdfs(pdf_dir: Path) -> dict[str, list[Path]]:
    """Classifica PDFs em tier1, tier2, global e skip.

    Returns:
        Dict com chaves 'tier1', 'tier2', 'global' — cada uma lista de Paths.
    """
    result: dict[str, list[Path]] = {"tier1": [], "tier2": [], "global": []}
    pdfs = sorted(pdf_dir.glob("*.pdf"))

    for pdf in pdfs:
        name = pdf.stem.lower()

        if _matches_any(name, _SKIP_KEYWORDS):
            continue
        if _matches_any(name, _TIER_GLOBAL_KEYWORDS):
            result["global"].append(pdf)
            continue
        if _matches_any(name, _TIER2_KEYWORDS):
            result["tier2"].append(pdf)
            continue
        if _matches_any(name, _TIER1_KEYWORDS):
            result["tier1"].append(pdf)
            continue
        # PDF não classificado: se não há tier1 ainda, usa como fallback
        if not result["tier1"]:
            result["tier1"].append(pdf)

    # Garante exatamente um PDF no tier1 (o melhor match por prioridade)
    if len(result["tier1"]) > 1:
        for kw in _TIER1_KEYWORDS:
            match = [p for p in result["tier1"] if kw in p.stem.lower()]
            if match:
                result["tier1"] = [match[0]]
                break

    return result


# Mantém compatibilidade com health_check.py que usa pick_main_pdf
def pick_main_pdf(pdf_dir: Path) -> Path | None:
    tiers = classify_pdfs(pdf_dir)
    pdfs = tiers["tier1"] or tiers["tier2"]
    return pdfs[0] if pdfs else None


# =============================================================================
# LLM — EXTRAÇÃO DE FATOS ATÔMICOS
# =============================================================================

EXTRACTION_PROMPT = """Extraia TODOS os fatos concretos e verificáveis desta seção de edital de fomento.

Regras:
- Cada fato deve ser uma sentença AUTO-CONTIDA que faça sentido isoladamente
- Inclua: valores monetários, prazos, requisitos, restrições, critérios, percentuais, públicos-alvo
- NÃO inclua: frases genéricas sem informação prática, citações legais puras, repetições
- Quando houver valores, inclua a unidade (R$, %, meses, etc.)
- Se a seção contiver uma tabela ou lista de itens, cada item relevante vira um fato separado

Responda APENAS com um JSON array de strings. Exemplo:
["O valor máximo por projeto é R$ 5.000.000,00", "O prazo de execução é de 36 meses"]

Seção ({section_type}): {section_title}

{content}"""


def _make_llm_client(backend: str):
    """Cria cliente OpenAI-compatible para o backend escolhido."""
    from openai import OpenAI

    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or \
                  getpass.getpass("Gemini API Key: ")
        return OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"
    elif backend == "openai":
        api_key = os.getenv("OPENAI_API_KEY") or getpass.getpass("OpenAI API Key: ")
        return OpenAI(api_key=api_key), "gpt-4o-mini"
    else:
        raise ValueError(f"Backend desconhecido: {backend}")


def extract_facts_from_section(
    client,
    model: str,
    section_type: str,
    section_title: str,
    content: str,
    max_content_chars: int = 6000,
) -> list[str]:
    """Extrai fatos atômicos de uma seção via LLM."""
    if len(content.strip()) < 50:
        return []

    prompt = EXTRACTION_PROMPT.format(
        section_type=section_type,
        section_title=section_title,
        content=content[:max_content_chars],
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()

        # Extrai JSON do response (pode vir com ```json ... ```)
        json_match = re.search(r"\[.*\]", raw, re.DOTALL)
        if json_match:
            facts = json.loads(json_match.group())
            return [f.strip() for f in facts if isinstance(f, str) and len(f.strip()) > 10]

        logger.warning("Resposta LLM sem JSON válido: %s", raw[:200])
        return []

    except Exception as e:
        logger.error("Erro LLM: %s", e)
        return []


# =============================================================================
# CACHE MD5
# =============================================================================

def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# =============================================================================
# PROCESSAMENTO DE UM EDITAL
# =============================================================================

def _extract_facts_from_pdf(
    pdf: Path,
    chamada_id: str,
    client,
    model: str,
    cache: dict,
    dry_run: bool,
    delay: float,
) -> list[dict]:
    """Extrai fatos atômicos de um único PDF. Usa cache por hash MD5."""
    text = extract_pdf_text(pdf)
    if not text or len(text) < 200:
        logger.warning("    Texto muito curto em %s (%d chars)", pdf.name, len(text))
        return []

    text_md5 = _text_hash(text)
    cache_key = f"{chamada_id}:{pdf.name}"

    if cache.get(cache_key) == text_md5 and not dry_run:
        logger.info("    Cache hit: %s", pdf.name)
        return []  # fatos já incluídos no arquivo salvo anteriormente

    sections = detect_sections(text)
    logger.info("    %d seções em %s", len(sections), pdf.name)

    if dry_run:
        for sec in sections:
            pri = "★" if sec["section_type"] in PRIORITY_SECTIONS else " "
            logger.info("      %s [%s] %s", pri, sec["section_type"], sec["title"][:50])
        cache[cache_key] = text_md5
        return []

    facts: list[dict] = []
    for sec in sections:
        if sec["section_type"] not in PRIORITY_SECTIONS:
            continue
        if len(sec["content"].strip()) < 50:
            continue

        logger.info("      Extraindo [%s] %s", sec["section_type"], sec["title"][:50])
        extracted = extract_facts_from_section(
            client, model,
            section_type=sec["section_type"],
            section_title=sec["title"],
            content=sec["content"],
        )
        for fact_text in extracted:
            facts.append({
                "text": fact_text,
                "source_pdf": pdf.name,
                "section_type": sec["section_type"],
                "extracted_at": datetime.now().strftime("%Y-%m-%d"),
                "origin": "extraction",
                "status": "confirmed",
            })
        time.sleep(delay)

    cache[cache_key] = text_md5
    return facts


def process_edital(
    chamada_id: str,
    client,
    model: str,
    cache: dict,
    dry_run: bool = False,
    delay: float = 2.0,
) -> dict | None:
    """Processa Tier 1 + Tier 2 de um edital."""
    pdf_dir = FINEP_PDFS_DIR / chamada_id
    if not pdf_dir.exists():
        logger.warning("Diretório não encontrado: %s", pdf_dir)
        return None

    tiers = classify_pdfs(pdf_dir)
    pdfs_to_process = tiers["tier1"] + tiers["tier2"]

    if not pdfs_to_process:
        logger.warning("Nenhum PDF processável em %s", pdf_dir)
        return None

    facts_file = FACTS_DIR / f"{chamada_id}.json"

    # Identifica quais PDFs já estão em cache e quais são novos
    def _is_cached(p: Path) -> bool:
        text = extract_pdf_text(p) or ""
        return cache.get(f"{chamada_id}:{p.name}") == _text_hash(text)

    cached_pdfs = [p for p in pdfs_to_process if _is_cached(p)]
    new_pdfs = [p for p in pdfs_to_process if not _is_cached(p)]

    # Todos em cache → retorna arquivo salvo sem chamar LLM
    if not new_pdfs and facts_file.exists() and not dry_run:
        logger.info("  Cache hit completo para %s", chamada_id)
        return json.loads(facts_file.read_text(encoding="utf-8"))

    logger.info("  PDFs novos: %s | em cache: %s",
                [p.name for p in new_pdfs],
                [p.name for p in cached_pdfs])

    # Carrega fatos já extraídos dos PDFs em cache (para não perder)
    base_facts: list[dict] = []
    if facts_file.exists() and cached_pdfs:
        existing = json.loads(facts_file.read_text(encoding="utf-8"))
        cached_names = {p.name for p in cached_pdfs}
        base_facts = [f for f in existing.get("facts", [])
                      if f.get("source_pdf") in cached_names]

    # Extrai fatos dos PDFs novos
    new_facts: list[dict] = []
    for pdf in new_pdfs:
        facts = _extract_facts_from_pdf(pdf, chamada_id, client, model, cache, dry_run, delay)
        new_facts.extend(facts)

    if dry_run:
        return None

    all_facts = base_facts + new_facts
    result = {
        "edital_id": chamada_id,
        "source_pdfs": [p.name for p in pdfs_to_process],
        "n_facts": len(all_facts),
        "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "facts": all_facts,
    }

    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    facts_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    _save_cache(cache)

    logger.info("  → %d fatos (%d existentes + %d novos)", len(all_facts), len(base_facts), len(new_facts))
    return result


# =============================================================================
# PROCESSAMENTO GLOBAL (TRL definition — uma vez para todos os editais)
# =============================================================================

GLOBAL_FACTS_DIR = FINEP_FACTS_DIR.parent / "global"
TRL_FACTS_FILE = GLOBAL_FACTS_DIR / "trl_definition.json"


def process_global_pdfs(
    client,
    model: str,
    cache: dict,
    dry_run: bool = False,
    delay: float = 2.0,
) -> dict | None:
    """Processa definicao_do_nivel_de_maturidade uma única vez globalmente.

    Busca o primeiro PDF com esse nome em qualquer subdiretório de FINEP_PDFS_DIR
    e salva os fatos em silver_data/finep/global/trl_definition.json.
    """
    # Já processado anteriormente
    if TRL_FACTS_FILE.exists() and not dry_run:
        data = json.loads(TRL_FACTS_FILE.read_text(encoding="utf-8"))
        logger.info("TRL definition já processada (%d fatos) — pulando", data.get("n_facts", 0))
        return data

    # Busca o primeiro PDF correspondente
    trl_pdf = None
    for subdir in sorted(FINEP_PDFS_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        for pdf in subdir.glob("*.pdf"):
            if _matches_any(pdf.stem.lower(), _TIER_GLOBAL_KEYWORDS):
                trl_pdf = pdf
                break
        if trl_pdf:
            break

    if not trl_pdf:
        logger.info("Nenhum PDF de definição TRL encontrado")
        return None

    logger.info("Processando TRL definition: %s", trl_pdf)

    facts = _extract_facts_from_pdf(
        trl_pdf, "global", client, model, cache, dry_run, delay
    )

    if dry_run or not facts:
        return None

    result = {
        "type": "global_reference",
        "source_pdf": trl_pdf.name,
        "source_edital": trl_pdf.parent.name,
        "n_facts": len(facts),
        "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "facts": facts,
    }

    GLOBAL_FACTS_DIR.mkdir(parents=True, exist_ok=True)
    TRL_FACTS_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    _save_cache(cache)

    logger.info("TRL definition: %d fatos salvos em %s", len(facts), TRL_FACTS_FILE)
    return result


# =============================================================================
# MAIN
# =============================================================================

def main(
    backend: str = "gemini",
    edital_ids: list[str] | None = None,
    dry_run: bool = False,
    delay: float = 2.0,
) -> list[dict]:
    """Processa editais FINEP: extrai fatos atômicos dos PDFs."""
    print("=" * 60)
    print("EXTRAÇÃO DE FATOS ATÔMICOS — FINEP")
    print("=" * 60)

    if not FINEP_PDFS_DIR.exists():
        print(f"Diretório de PDFs não encontrado: {FINEP_PDFS_DIR}")
        return []

    if edital_ids:
        dirs = [FINEP_PDFS_DIR / eid for eid in edital_ids]
    else:
        dirs = sorted(d for d in FINEP_PDFS_DIR.iterdir() if d.is_dir())

    print(f"Editais para processar: {len(dirs)}")

    if dry_run:
        print("MODO DRY-RUN — apenas mostra seções, sem chamar LLM\n")
        client, model = None, ""
    else:
        client, model = _make_llm_client(backend)
        print(f"Backend: {backend} | Model: {model} | Delay: {delay}s\n")

    cache = _load_cache()

    # Etapa 0: processa TRL definition globalmente (uma vez)
    print(">>> TRL Definition (global)")
    process_global_pdfs(client, model, cache, dry_run=dry_run, delay=delay)

    # Etapa 1: processa cada edital (Tier 1 + Tier 2)
    results = []
    total_facts = 0

    for i, pdf_dir in enumerate(dirs, 1):
        chamada_id = pdf_dir.name
        logger.info("[%d/%d] Edital %s", i, len(dirs), chamada_id)

        result = process_edital(
            chamada_id, client, model, cache,
            dry_run=dry_run, delay=delay,
        )

        if result:
            results.append(result)
            total_facts += result.get("n_facts", 0)

    print(f"\n{'=' * 60}")
    print(f"RESUMO: {len(results)} editais processados, {total_facts} fatos extraídos")
    print(f"Fatos por edital: {FACTS_DIR}/")
    print(f"TRL definition:   {TRL_FACTS_FILE}")
    print(f"{'=' * 60}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extrai fatos atômicos de PDFs FINEP")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "openai"])
    parser.add_argument("--edital", nargs="+", help="IDs específicos (ex: 782 790)")
    parser.add_argument("--dry-run", action="store_true", help="Mostra seções sem chamar LLM")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay entre chamadas LLM (s)")
    args = parser.parse_args()

    main(
        backend=args.backend,
        edital_ids=args.edital,
        dry_run=args.dry_run,
        delay=args.delay,
    )
