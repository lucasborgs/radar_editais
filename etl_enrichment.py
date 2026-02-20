"""
ETL de Enriquecimento (Radar de Editais)

Extrai dados estruturados dos editais usando LLM (uma vez, cacheado).
Para cada edital, extrai: CNAEs aceitos, porte, capital social mínimo,
certificações, documentos exigidos, temáticas, restrição geográfica, etc.

Saída: silver_data_enriched/ como Parquet.
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
import requests

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

SILVER_PATH = Path("silver_data")
ENRICHED_PATH = Path("silver_data_enriched")
CACHE_PATH = ENRICHED_PATH / ".enrichment_cache.json"

LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# PROMPT DE EXTRAÇÃO
# =============================================================================

EXTRACTION_PROMPT = """Você é um analista especializado em editais de fomento e licitações no Brasil.

Analise o edital abaixo e extraia informações estruturadas. Responda APENAS com JSON válido.

{
  "themes": ["<temática 1>", "<temática 2>"],
  "required_cnaes": ["<CNAE 1>", "<CNAE 2>"],
  "required_porte": ["<MEI|ME|EPP|MEDIO|GRANDE>"],
  "min_capital_social": <float ou null>,
  "required_certifications": ["<certificação 1>"],
  "required_docs": ["<documento exigido 1>"],
  "geographic_restriction": "<restrição geográfica ou null>",
  "eligible_entity_types": ["<tipo de entidade elegível>"],
  "evaluation_criteria": ["<critério de avaliação 1>"],
  "keywords": ["<palavra-chave 1>", "<palavra-chave 2>"]
}

INSTRUÇÕES:
- themes: áreas temáticas (ex: "inteligência artificial", "saúde", "educação", "meio ambiente", "inovação", "pesquisa básica", "infraestrutura", "tecnologia", "sustentabilidade", "cultura", "empreendedorismo")
- required_cnaes: liste códigos CNAE mencionados. Se não mencionados, retorne lista vazia.
- required_porte: portes empresariais aceitos. Se não especificado, retorne lista vazia.
- min_capital_social: valor mínimo em R$. Se não mencionado, retorne null.
- required_certifications: certificações obrigatórias (ISO, PMP, etc.). Lista vazia se nenhuma.
- required_docs: documentos de habilitação exigidos (certidões, atestados, etc.). Lista vazia se nenhum.
- geographic_restriction: ex: "apenas São Paulo", "região Norte". null se sem restrição.
- eligible_entity_types: tipos de entidade elegíveis (ex: "empresa", "ICT", "universidade", "ONG", "pessoa física", "startup"). Lista vazia se não especificado.
- evaluation_criteria: critérios de avaliação mencionados. Lista vazia se nenhum.
- keywords: 3-8 palavras-chave representativas do conteúdo.

Se a informação não estiver disponível no texto, use lista vazia [] ou null. NUNCA invente dados.
Responda APENAS com o JSON, sem markdown."""


# =============================================================================
# CACHE
# =============================================================================

def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    ENRICHED_PATH.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _hash_edital(row: pd.Series) -> str:
    content = f"{row.get('id', '')}|{row.get('title', '')}|{str(row.get('description', ''))[:2000]}"
    return hashlib.md5(content.encode("utf-8")).hexdigest()


# =============================================================================
# LLM CALLS
# =============================================================================

def _call_ollama(messages: list[dict]) -> tuple[bool, str]:
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 1500},
                "format": "json"
            },
            timeout=120
        )
        if response.status_code != 200:
            return (False, f"Ollama retornou {response.status_code}")
        return (True, response.json()["message"]["content"])
    except requests.exceptions.ConnectionError:
        return (False, "Ollama não acessível")
    except Exception as e:
        return (False, str(e))


def _call_openai(messages: list[dict]) -> tuple[bool, str]:
    if not OPENAI_API_KEY:
        return (False, "OPENAI_API_KEY não configurada")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=1500,
            response_format={"type": "json_object"}
        )
        return (True, response.choices[0].message.content)
    except ImportError:
        return (False, "Biblioteca openai não instalada")
    except Exception as e:
        return (False, str(e))


def _call_llm(messages: list[dict]) -> tuple[bool, str]:
    if LLM_BACKEND == "ollama":
        return _call_ollama(messages)
    return _call_openai(messages)


def _parse_json(text: str) -> Optional[dict]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


# =============================================================================
# EXTRAÇÃO POR EDITAL
# =============================================================================

EMPTY_ENRICHMENT = {
    "themes": [],
    "required_cnaes": [],
    "required_porte": [],
    "min_capital_social": None,
    "required_certifications": [],
    "required_docs": [],
    "geographic_restriction": None,
    "eligible_entity_types": [],
    "evaluation_criteria": [],
    "keywords": [],
}


def enrich_edital(row: pd.Series) -> dict:
    """Extrai dados estruturados de um edital via LLM."""
    title = str(row.get("title", "") or "")
    description = str(row.get("description", "") or "")
    source = str(row.get("source", "") or "")
    category = str(row.get("category", "") or "")

    # Contexto do edital (limitado para não estourar token)
    edital_text = f"Título: {title}\nFonte: {source}\nCategoria: {category}\n\n{description[:4000]}"

    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": edital_text}
    ]

    success, response_text = _call_llm(messages)
    if not success:
        logger.warning(f"  LLM falhou para '{title[:50]}': {response_text}")
        return EMPTY_ENRICHMENT.copy()

    parsed = _parse_json(response_text)
    if not parsed:
        logger.warning(f"  JSON inválido para '{title[:50]}': {response_text[:100]}")
        return EMPTY_ENRICHMENT.copy()

    # Normaliza e valida campos
    result = {}
    for key, default in EMPTY_ENRICHMENT.items():
        val = parsed.get(key, default)
        if isinstance(default, list) and not isinstance(val, list):
            val = [val] if val else []
        if default is None and isinstance(val, list):
            val = val[0] if val else None
        result[key] = val

    return result


# =============================================================================
# PIPELINE
# =============================================================================

def main(sources_filter: Optional[list[str]] = None, force: bool = False):
    """
    Pipeline ETL de Enriquecimento.

    Args:
        sources_filter: Processar apenas essas fontes.
        force: Reprocessar mesmo se cache válido.
    """
    logger.info("=" * 60)
    logger.info("INICIANDO ETL DE ENRIQUECIMENTO")
    logger.info(f"Backend LLM: {LLM_BACKEND} / {OLLAMA_MODEL if LLM_BACKEND == 'ollama' else OPENAI_MODEL}")
    logger.info("=" * 60)

    start_time = datetime.now()

    if not SILVER_PATH.exists():
        logger.error(f"Silver não encontrado: {SILVER_PATH}")
        return

    ENRICHED_PATH.mkdir(parents=True, exist_ok=True)

    # Carregar silver
    df = pd.read_parquet(SILVER_PATH)
    if sources_filter:
        df = df[df["source"].isin(sources_filter)]
        logger.info(f"Filtro ativo: {sources_filter}")

    logger.info(f"Total de editais a processar: {len(df)}")

    # Carregar cache
    cache = _load_cache() if not force else {}
    logger.info(f"Cache: {len(cache)} editais já enriquecidos")

    # Processar cada edital
    enrichments = []
    processed = 0
    cached = 0
    errors = 0

    for idx, row in df.iterrows():
        edital_hash = _hash_edital(row)
        edital_id = row["id"]
        title = str(row.get("title", ""))[:60]

        # Verificar cache
        if edital_hash in cache and not force:
            enrichments.append(cache[edital_hash])
            cached += 1
            continue

        # Chamar LLM
        logger.info(f"  [{processed + 1}] Enriquecendo: {title}...")
        result = enrich_edital(row)

        # Adicionar ID para referência
        result["edital_id"] = edital_id
        result["edital_hash"] = edital_hash

        enrichments.append(result)
        cache[edital_hash] = result
        processed += 1

        if not result.get("themes") and not result.get("keywords"):
            errors += 1

        # Salvar cache periodicamente (a cada 10 editais)
        if processed % 10 == 0:
            _save_cache(cache)
            logger.info(f"  Cache salvo ({len(cache)} entradas)")

    # Salvar cache final
    _save_cache(cache)

    # Construir DataFrame enriquecido
    df_enriched = df.copy()

    # Expandir campos do enriquecimento como colunas
    enrich_df = pd.DataFrame(enrichments)

    # Alinhar pelo índice
    enrich_df.index = df.index

    # Colunas de enriquecimento
    enrich_cols = [
        "themes", "required_cnaes", "required_porte",
        "min_capital_social", "required_certifications",
        "required_docs", "geographic_restriction",
        "eligible_entity_types", "evaluation_criteria", "keywords"
    ]

    for col in enrich_cols:
        if col in enrich_df.columns:
            df_enriched[col] = enrich_df[col]
        else:
            df_enriched[col] = EMPTY_ENRICHMENT.get(col)

    # Salvar como Parquet
    output_file = ENRICHED_PATH / "editais_enriched.parquet"
    df_enriched.to_parquet(output_file, index=False, engine="pyarrow")

    elapsed = datetime.now() - start_time

    logger.info("=" * 60)
    logger.info("ETL ENRIQUECIMENTO CONCLUÍDO")
    logger.info(f"  Total: {len(df)} editais")
    logger.info(f"  Processados (LLM): {processed}")
    logger.info(f"  Do cache: {cached}")
    logger.info(f"  Erros/vazios: {errors}")
    logger.info(f"  Tempo: {elapsed}")
    logger.info(f"  Saída: {output_file}")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ETL Enriquecimento de Editais")
    parser.add_argument("--force", action="store_true", help="Reprocessar tudo ignorando cache")
    parser.add_argument("--sources", nargs="+", help="Filtrar por fontes")
    args = parser.parse_args()
    main(sources_filter=args.sources, force=args.force)
