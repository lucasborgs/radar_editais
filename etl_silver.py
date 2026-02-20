"""
ETL Bronze → Silver (Radar de Editais)

Script para processar dados da Camada Bronze para a Silver,
normalizando 9 fontes heterogêneas para um schema unificado
com particionamento Hive por fonte.

Validação de schema via Pydantic com quarantine para registros inválidos.
"""

import json
import hashlib
import html
import logging
import re
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Callable

import pandas as pd
from pydantic import BaseModel, field_validator, ValidationError

# Tentativa de importar BeautifulSoup (opcional para limpeza HTML)
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

BRONZE_PATH = Path("bronze_data")
SILVER_PATH = Path("silver_data")
QUARANTINE_PATH = Path("quarantine")

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Mapeamento de pasta para nome normalizado da fonte
SOURCE_MAPPING = {
    "cnpq_raw": "CNPQ",
    "finep_raw": "FINEP",
    "bndes_raw": "BNDES",
    "fapesp_raw": "FAPESP",
}

# Status normalizados
STATUS_MAPPING = {
    "DISPONÍVEL": "ABERTA",
    "ABERTA": "ABERTA",
    "ENCERRADA": "ENCERRADA",
    "ATIVO": "ABERTA",
    "Em Análise": "ABERTA",
    "FLUXO_CONTINUO": "FLUXO_CONTINUO",
    "VERIFICAR_SITE": "VERIFICAR",
    "RECENTE": "ABERTA",
    "HISTÓRICO": "ENCERRADA",
}

VALID_STATUSES = {"ABERTA", "ENCERRADA", "FLUXO_CONTINUO", "VERIFICAR"}

# =============================================================================
# FUNÇÕES UTILITÁRIAS
# =============================================================================

def clean_html(text: Optional[str]) -> str:
    if not text:
        return ""
    text = str(text)
    text = html.unescape(text)
    if HAS_BS4 and ("<" in text and ">" in text):
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text(separator=" ")
    else:
        text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def parse_date_br(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def parse_date_iso(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        date_part = date_str.split("T")[0].strip()
        return datetime.strptime(date_part, "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def parse_date_flexible(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    result = parse_date_iso(date_str)
    if result:
        return result
    return parse_date_br(date_str)


def generate_id(source: str, url: str, title: str) -> str:
    content = f"{source}|{url or ''}|{title or ''}"
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def normalize_status(status: Optional[str]) -> str:
    if not status:
        return "VERIFICAR"
    return STATUS_MAPPING.get(status.strip(), "VERIFICAR")


def safe_get(record: dict, *keys, default=None):
    value = record
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
        if value is None:
            return default
    return value


# =============================================================================
# PYDANTIC MODEL
# =============================================================================

class EditalRecord(BaseModel):
    """Schema validado para um registro de edital na camada Silver."""
    id: str
    source: str
    title: str
    description: str = ""
    url: str = ""
    deadline_date: Optional[date] = None
    status: str = "VERIFICAR"
    extracted_at: Optional[date] = None
    category: Optional[str] = None
    target_audience: Optional[str] = None
    location: Optional[str] = None
    value_brl: Optional[float] = None

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title não pode ser vazio")
        return v.strip()

    @field_validator("source")
    @classmethod
    def source_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source não pode ser vazio")
        return v.strip()

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            return "VERIFICAR"
        return v

    @field_validator("value_brl")
    @classmethod
    def value_must_be_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            return None
        return v


# =============================================================================
# QUARANTINE
# =============================================================================

def quarantine_record(record: dict, source: str, error: str) -> None:
    """Salva registro inválido na pasta quarantine para análise."""
    q_path = QUARANTINE_PATH / f"source={source}"
    q_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    q_file = q_path / f"{ts}.json"
    q_file.write_text(json.dumps({
        "raw_record": record,
        "validation_error": error,
        "quarantined_at": datetime.now().isoformat(),
    }, ensure_ascii=False, default=str), encoding="utf-8")


# =============================================================================
# PARSERS ESPECÍFICOS POR FONTE
# =============================================================================

def _parse_cnpq(record: dict) -> dict:
    title = record.get("titulo", "")
    url = record.get("url", "")
    return {
        "id": generate_id("CNPQ", url, title),
        "source": "CNPQ",
        "title": title,
        "description": clean_html(record.get("descricao", "")),
        "url": url,
        "deadline_date": parse_date_br(record.get("prazo_fim")),
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": None,
        "target_audience": None,
        "location": None,
        "value_brl": None,
    }


def _parse_finep(record: dict) -> dict:
    title = record.get("titulo", "")
    url = record.get("link", "")
    return {
        "id": generate_id("FINEP", url, title),
        "source": "FINEP",
        "title": title,
        "description": clean_html(record.get("tema", "")),
        "url": url,
        "deadline_date": parse_date_br(record.get("prazo_envio")),
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_flexible(record.get("data_extracao")),
        "category": record.get("tema"),
        "target_audience": record.get("publico_alvo"),
        "location": None,
        "value_brl": None,
    }


def _parse_bndes(record: dict) -> dict:
    title = record.get("titulo", "")
    url = record.get("url", "")
    return {
        "id": generate_id("BNDES", url, title),
        "source": "BNDES",
        "title": title,
        "description": clean_html(record.get("contexto_capturado", "")),
        "url": url,
        "deadline_date": None,
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": record.get("programa"),
        "target_audience": None,
        "location": None,
        "value_brl": None,
    }


def _parse_fapesp(record: dict) -> dict:
    title = record.get("titulo", "")
    url = record.get("url", "")
    description = record.get("texto_cru", "") or record.get("descricao", "") or ""
    return {
        "id": generate_id("FAPESP", url, title),
        "source": "FAPESP",
        "title": title,
        "description": clean_html(description),
        "url": url,
        "deadline_date": parse_date_iso(record.get("data_limite")),
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": record.get("areas"),
        "target_audience": record.get("modalidades"),
        "location": "SP",
        "value_brl": None,
    }


# Registro de parsers
PARSERS: dict[str, Callable[[dict], dict]] = {
    "CNPQ": _parse_cnpq,
    "FINEP": _parse_finep,
    "BNDES": _parse_bndes,
    "FAPESP": _parse_fapesp,
}


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def load_json_file(file_path: Path) -> list[dict]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
        else:
            logger.warning(f"Formato inesperado em {file_path}: {type(data)}")
            return []
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar JSON {file_path}: {e}")
        return []
    except Exception as e:
        logger.error(f"Erro ao ler arquivo {file_path}: {e}")
        return []


def process_source(source_path: Path, source_name: str) -> list[dict]:
    """Processa todos os arquivos JSON de uma fonte com validação Pydantic."""
    parser = PARSERS.get(source_name)
    if not parser:
        logger.warning(f"Parser não encontrado para fonte: {source_name}")
        return []

    processed_records = []
    quarantine_count = 0
    json_files = list(source_path.glob("*.json"))

    logger.info(f"Processando {source_name}: {len(json_files)} arquivos encontrados")

    for json_file in json_files:
        raw_records = load_json_file(json_file)

        for record in raw_records:
            try:
                parsed = parser(record)
                validated = EditalRecord(**parsed)
                processed_records.append(validated.model_dump())
            except ValidationError as e:
                quarantine_record(record, source_name, str(e))
                quarantine_count += 1
            except Exception as e:
                quarantine_record(record, source_name, f"ParseError: {e}")
                quarantine_count += 1

    logger.info(f"  → {len(processed_records)} válidos, {quarantine_count} em quarentena para {source_name}")
    return processed_records


def deduplicate_records(records: list[dict]) -> list[dict]:
    seen_ids = set()
    unique_records = []
    for record in records:
        record_id = record.get("id")
        if record_id and record_id not in seen_ids:
            seen_ids.add(record_id)
            unique_records.append(record)
    duplicates_removed = len(records) - len(unique_records)
    if duplicates_removed > 0:
        logger.info(f"Removidas {duplicates_removed} duplicatas")
    return unique_records


def save_to_parquet(df: pd.DataFrame, output_path: Path) -> None:
    if "deadline_date" in df.columns:
        df["deadline_date"] = pd.to_datetime(df["deadline_date"], errors="coerce")
    if "extracted_at" in df.columns:
        df["extracted_at"] = pd.to_datetime(df["extracted_at"], errors="coerce")
    output_path.mkdir(parents=True, exist_ok=True)
    df.to_parquet(
        output_path,
        partition_cols=["source"],
        index=False,
        engine="pyarrow"
    )
    logger.info(f"Dados salvos em {output_path} com particionamento por 'source'")


def main(sources_filter: Optional[list[str]] = None):
    """
    Função principal do ETL Bronze → Silver.

    Args:
        sources_filter: Se fornecido, processa apenas as fontes listadas.
    """
    logger.info("=" * 60)
    logger.info("INICIANDO ETL BRONZE → SILVER")
    logger.info("=" * 60)

    if not BRONZE_PATH.exists():
        logger.error(f"Pasta Bronze não encontrada: {BRONZE_PATH}")
        return

    all_records = []

    for folder_name, source_name in SOURCE_MAPPING.items():
        if sources_filter and source_name not in sources_filter:
            logger.info(f"Pulando {source_name} (não está no filtro)")
            continue

        source_path = BRONZE_PATH / folder_name
        if not source_path.exists():
            logger.warning(f"Pasta não encontrada: {source_path}")
            continue

        records = process_source(source_path, source_name)
        all_records.extend(records)

    if not all_records:
        logger.warning("Nenhum registro processado!")
        return

    logger.info("-" * 60)
    logger.info(f"TOTAL: {len(all_records)} registros coletados")

    unique_records = deduplicate_records(all_records)

    df = pd.DataFrame(unique_records)

    column_order = [
        "id", "source", "title", "description", "url", "deadline_date",
        "status", "extracted_at", "category", "target_audience",
        "location", "value_brl"
    ]
    df = df[[col for col in column_order if col in df.columns]]

    logger.info("-" * 60)
    logger.info("ESTATÍSTICAS POR FONTE:")
    for source, count in df["source"].value_counts().items():
        logger.info(f"  {source}: {count} registros")

    # Conta quarantined
    q_count = sum(1 for _ in QUARANTINE_PATH.rglob("*.json")) if QUARANTINE_PATH.exists() else 0
    if q_count > 0:
        logger.info(f"  QUARENTENA: {q_count} registros inválidos em {QUARANTINE_PATH}/")

    save_to_parquet(df, SILVER_PATH)

    logger.info("=" * 60)
    logger.info("ETL CONCLUÍDO COM SUCESSO")
    logger.info(f"Total de registros únicos: {len(df)}")
    logger.info(f"Saída: {SILVER_PATH}/")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
