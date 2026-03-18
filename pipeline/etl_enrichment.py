"""
ETL de Enriquecimento (Radar de Mecanismos de Fomento)

Extrai dados estruturados dos editais/unidades usando LLM (uma vez, cacheado).
Para cada item, extrai: CNAEs aceitos, porte, capital social mínimo,
certificações, documentos exigidos, temáticas, TRL, tipo_instrumento, etc.

Também gera o `search_document` — template semântico padronizado usado
para embedding no ChromaDB (Harmonização Semântica Pré-Embedding).

Saída: silver_data_enriched/editais_enriched.parquet
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

from config import SILVER_DIR as SILVER_PATH, SILVER_ENRICHED_DIR as ENRICHED_PATH, ENRICHMENT_CACHE as CACHE_PATH

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

EXTRACTION_PROMPT = """Você é um analista especializado em editais de fomento e mecanismos de financiamento à inovação no Brasil.

Analise o edital/unidade abaixo e extraia informações estruturadas. Responda APENAS com JSON válido.

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
  "keywords": ["<palavra-chave 1>", "<palavra-chave 2>"],
  "trl_min": <inteiro 1-9 ou null>,
  "trl_max": <inteiro 1-9 ou null>,
  "tipo_instrumento": "<classificação ou null>",
  "allowed_use_of_funds": ["<uso1>", "<uso2>"],
  "value_min_brl": <float ou null>,
  "value_max_brl": <float ou null>
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
- trl_min: nível mínimo de maturidade tecnológica (TRL) exigido ou adequado (escala 1-9). null se não mencionado.
- trl_max: nível máximo de maturidade tecnológica (TRL) elegível (escala 1-9). null se não mencionado. Referência: pesquisa básica=TRL 1-3; prova de conceito=TRL 3-5; escalonamento/piloto=TRL 5-7; produto acabado=TRL 7-9.
- tipo_instrumento: classificação do mecanismo financeiro. Use exatamente uma das opções: "SUBVENCAO_ECONOMICA", "PESQUISA_BASICA", "PESQUISA_APLICADA", "CREDITO_INOVACAO", "ENCOMENDA_TECNOLOGICA", "MATCHING_EMBRAPII". null se não for possível classificar.
- allowed_use_of_funds: usos explicitamente permitidos para o recurso. Use termos padronizados: "P&D_interno", "contratacao", "equipamento", "prototipagem", "internacionalizacao", "marketing". Lista vazia se não mencionado.
- value_min_brl: valor mínimo total do projeto em R$ (float). null se não especificado.
- value_max_brl: valor máximo por empresa/projeto em R$ (float). null se não especificado.

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
    "trl_min": None,
    "trl_max": None,
    "tipo_instrumento": None,
    "allowed_use_of_funds": [],
    "value_min_brl": None,
    "value_max_brl": None,
}


def enrich_edital(row: pd.Series) -> dict:
    """Extrai dados estruturados de um edital/unidade via LLM."""
    title = str(row.get("title", "") or "")
    description = str(row.get("description", "") or "")
    source = str(row.get("source", "") or "")
    category = str(row.get("category", "") or "")
    tipo_fluxo = str(row.get("tipo_fluxo", "") or "")

    # Contexto do edital (limitado para não estourar token)
    edital_text = (
        f"Título: {title}\nFonte: {source}\nTipo de fluxo: {tipo_fluxo}\n"
        f"Categoria: {category}\n\n{description[:4000]}"
    )

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

    # Valida trl_min e trl_max (devem ser int 1-9 ou None)
    for trl_field in ("trl_min", "trl_max"):
        v = result.get(trl_field)
        if v is not None:
            try:
                v = int(v)
                result[trl_field] = v if 1 <= v <= 9 else None
            except (TypeError, ValueError):
                result[trl_field] = None

    # Valida tipo_instrumento
    VALID_TIPOS = {
        "SUBVENCAO_ECONOMICA", "PESQUISA_BASICA", "PESQUISA_APLICADA",
        "CREDITO_INOVACAO", "ENCOMENDA_TECNOLOGICA", "MATCHING_EMBRAPII",
    }
    ti = result.get("tipo_instrumento")
    if ti and ti not in VALID_TIPOS:
        result["tipo_instrumento"] = None

    return result


# =============================================================================
# GERAÇÃO DO SEARCH DOCUMENT (Template Semântico Pré-Embedding)
# =============================================================================

def generate_search_document(row: pd.Series, enrichment: dict) -> str:
    """
    Gera o 'Documento de Busca Padronizado' para embedding (Harmonização Semântica).

    O template normaliza o vocabulário heterogêneo dos diferentes mecanismos
    (juridiquês de editais FINEP vs portfólio técnico de unidades Embrapii)
    em um formato consistente que o modelo de embedding pode comparar diretamente.
    """
    themes = enrichment.get("themes") or []
    themes_str = ", ".join(themes[:5]) if themes else "não especificada"

    trl_min = enrichment.get("trl_min") or row.get("trl_min")
    trl_max = enrichment.get("trl_max") or row.get("trl_max")
    if trl_min and trl_max:
        trl_str = f"TRL {trl_min}-{trl_max}"
    elif trl_min:
        trl_str = f"TRL {trl_min}+"
    elif trl_max:
        trl_str = f"até TRL {trl_max}"
    else:
        trl_str = "TRL não especificado"

    # tipo_instrumento: LLM tem precedência sobre valor do silver
    tipo = (
        enrichment.get("tipo_instrumento")
        or row.get("tipo_instrumento")
        or ""
    )
    tipo_legivel = {
        "SUBVENCAO_ECONOMICA": "Subvenção Econômica",
        "PESQUISA_BASICA": "Pesquisa Básica",
        "PESQUISA_APLICADA": "Pesquisa Aplicada",
        "CREDITO_INOVACAO": "Crédito para Inovação",
        "ENCOMENDA_TECNOLOGICA": "Encomenda Tecnológica",
        "MATCHING_EMBRAPII": "Matching Embrapii",
    }.get(tipo, tipo)

    tipo_fluxo = str(row.get("tipo_fluxo", "") or "")
    fluxo_str = (
        "Fluxo Contínuo (sempre aberto)"
        if tipo_fluxo == "FLUXO_CONTINUO"
        else "Edital com prazo"
    )

    porte = enrichment.get("required_porte") or []
    porte_str = ", ".join(porte) if porte else "não especificado"

    entities = enrichment.get("eligible_entity_types") or []
    entities_str = ", ".join(entities) if entities else "não especificado"

    usos = enrichment.get("allowed_use_of_funds") or []
    usos_str = ", ".join(usos) if usos else "não especificado"

    title = str(row.get("title", "") or "")
    source = str(row.get("source", "") or "")

    return (
        f"Objetivo: {title}. "
        f"Fonte: {source}. "
        f"Área: {themes_str}. "
        f"Maturidade: {trl_str}. "
        f"Mecanismo: {fluxo_str} — {tipo_legivel}. "
        f"Porte elegível: {porte_str}. "
        f"Entidade: {entities_str}. "
        f"Uso: {usos_str}."
    )


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
        processed += 1

        # Só cacheia quando o LLM extraiu ao menos um campo substantivo.
        # Resultados vazios (LLM offline ou JSON vazio) não são cacheados
        # para que sejam re-tentados na próxima execução.
        is_meaningful = bool(
            result.get("themes") or
            result.get("keywords") or
            result.get("tipo_instrumento") or
            result.get("trl_min") is not None
        )
        if is_meaningful:
            cache[edital_hash] = result
        else:
            errors += 1
            logger.warning(f"  Resultado vazio para '{title}' — não cacheado (será re-tentado)")

        # Salvar cache periodicamente (a cada 10 editais com resultado válido)
        if processed % 10 == 0:
            _save_cache(cache)
            logger.info(f"  Cache salvo ({len(cache)} entradas)")

    # Salvar cache final
    _save_cache(cache)

    # Construir DataFrame enriquecido
    df_enriched = df.copy()

    # Expandir campos do enriquecimento como colunas
    enrich_df = pd.DataFrame(enrichments)
    enrich_df.index = df.index

    enrich_cols = [
        "themes", "required_cnaes", "required_porte",
        "min_capital_social", "required_certifications",
        "required_docs", "geographic_restriction",
        "eligible_entity_types", "evaluation_criteria", "keywords",
        "trl_min", "trl_max", "tipo_instrumento",
        "allowed_use_of_funds", "value_min_brl", "value_max_brl",
    ]

    for col in enrich_cols:
        if col in enrich_df.columns:
            df_enriched[col] = enrich_df[col]
        else:
            df_enriched[col] = EMPTY_ENRICHMENT.get(col)

    # Enriquecimento de trl_min/trl_max: LLM tem precedência sobre silver
    # Se o LLM extraiu valores, eles sobrescrevem os defaults do silver
    for trl_field in ("trl_min", "trl_max"):
        if trl_field in enrich_df.columns:
            llm_vals = enrich_df[trl_field]
            # Sobrescrever apenas onde o LLM retornou um valor válido
            mask = llm_vals.notna()
            df_enriched.loc[mask, trl_field] = llm_vals[mask]

    # Mesma lógica para tipo_instrumento: LLM tem precedência
    if "tipo_instrumento" in enrich_df.columns:
        llm_tipo = enrich_df["tipo_instrumento"]
        mask = llm_tipo.notna() & (llm_tipo != "")
        df_enriched.loc[mask, "tipo_instrumento"] = llm_tipo[mask]

    # Gerar search_document para cada linha
    # enrichments está alinhado com df (mesma ordem de iteração)
    logger.info("Gerando search_documents...")
    search_docs = []
    for i, (_, row) in enumerate(df_enriched.iterrows()):
        enr = enrichments[i] if i < len(enrichments) else {}
        search_docs.append(generate_search_document(row, enr))
    df_enriched["search_document"] = search_docs

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
