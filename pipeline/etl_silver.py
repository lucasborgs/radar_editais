"""
ETL Bronze → Silver (Radar de Mecanismos de Fomento)

Processa dados da Camada Bronze para a Silver, normalizando 4 fontes
para um schema unificado com particionamento Hive por fonte.

Fontes:
  - FAPESP, FINEP, BNDES  → tipo_fluxo = EDITAL
  - EMBRAPII               → tipo_fluxo = FLUXO_CONTINUO (diretório de unidades)

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

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

from config import BRONZE_DIR as BRONZE_PATH, SILVER_DIR as SILVER_PATH, ROOT
QUARANTINE_PATH = ROOT / "quarantine"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

SOURCE_MAPPING = {
    "fapesp_raw":   "FAPESP",
    "finep_raw":    "FINEP",
    "bndes_raw":    "BNDES",
    "embrapii_raw": "EMBRAPII",
}

STATUS_MAPPING = {
    "DISPONÍVEL":     "ABERTA",
    "ABERTA":         "ABERTA",
    "ENCERRADA":      "ENCERRADA",
    "ATIVO":          "ABERTA",
    "Em Análise":     "ABERTA",
    "FLUXO_CONTINUO": "FLUXO_CONTINUO",
    "VERIFICAR_SITE": "VERIFICAR",
    "RECENTE":        "ABERTA",
    "HISTÓRICO":      "ENCERRADA",
    "DESCONHECIDO":   "VERIFICAR",
    "FLUXO CONTÍNUO": "FLUXO_CONTINUO",
}

VALID_STATUSES = {"ABERTA", "ENCERRADA", "FLUXO_CONTINUO", "VERIFICAR"}

# =============================================================================
# FILTROS DE RELEVÂNCIA (equivalentes ao etl_generate_pairs.py, sem histórico)
# =============================================================================

# FAPESP: apenas chamadas com foco em inovação empresarial (PIPE, PITE, etc.)
# Lógica: REJECT tem prioridade; se não houver REJECT, precisa haver APPROVE.
FAPESP_REJECT_KW: list[str] = [
    "Auxílio à Pesquisa - Regular", "Auxílio Regular",
    "Projeto Temático", "Auxílio à Pesquisa - Temático",
    "Jovem Pesquisador", "Jovens Doutores",
    "Bolsa de Doutorado", "Bolsa de Mestrado", "Bolsa de Pós-Doutorado",
    "Iniciação Científica", "Estágio de Pesquisa",
    "Pesquisador Visitante",
    "Organização de Reunião Científica",
    "Participação em Reunião Científica",
    "Equipamentos Multiusuários", "EMU",
    "Escola São Paulo de Ciência Avançada", "SPSAS",
    "Ensino Público", "Prêmio",
]

FAPESP_APPROVE_KW: list[str] = [
    "PIPE", "Pesquisa Inovativa em Pequenas Empresas",
    "PITE", "Parceria para Inovação Tecnológica",
    "Centro de Pesquisa em Engenharia", "CPE",
    "Centro de Pesquisa Aplicada", "CPA",
    "PAPPE", "TECNOVA", "Validação Tecnológica",
    "Startup", "Scale-up", "Desafio de Inovação",
]


def _is_relevant(source: str, record: dict) -> bool:
    """Retorna False para registros que devem ser descartados do índice Silver.

    Regras por fonte:
    - FAPESP : filtro por keywords no título (rejeita acadêmico, aprova empresarial)
    - FINEP  : aceita todos (editais já focados em empresas/inovação)
    - BNDES  : aceita todos (scraper já filtra por keywords de chamada)
    - EMBRAPII: aceita todos (unidades de fluxo contínuo para matching)
    - Todos  : descarta registros com status ENCERRADA (apenas oportunidades atuais)
    """
    # Status encerrado → não indexar
    status_raw = str(record.get("status", "")).upper()
    if status_raw == "ENCERRADA":
        return False

    if source == "FAPESP":
        titulo = record.get("titulo", "") or ""
        titulo_l = titulo.lower()
        if any(kw.lower() in titulo_l for kw in FAPESP_REJECT_KW):
            return False

    if source == "FINEP":
        prazo_str = record.get("prazo_envio") or record.get("prazo") or ""
        if prazo_str:
            try:
                day, month, year = prazo_str.strip().split("/")
                deadline = datetime(int(year), int(month), int(day))
                if deadline < datetime.now():
                    return False
            except Exception:
                pass

    return True

# Mapeamento conservador de tipo_instrumento → TRL range
# Refinado pelo etl_enrichment.py via LLM para cada edital específico
TRL_RANGE_BY_INSTRUMENTO = {
    "PESQUISA_BASICA":         (1, 4),
    "PESQUISA_APLICADA":       (3, 6),
    "SUBVENCAO_ECONOMICA":     (4, 7),
    "ENCOMENDA_TECNOLOGICA":   (5, 8),
    "CREDITO_INOVACAO":        (6, 9),
    "MATCHING_EMBRAPII":       (4, 6),
}


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


def infer_tipo_instrumento_finep(title: str) -> str:
    title_lower = title.lower()
    if any(k in title_lower for k in ["subvenção", "subvencao", "pipe", "pappe"]):
        return "SUBVENCAO_ECONOMICA"
    if any(k in title_lower for k in ["encomenda", "tecnológica", "tecnologica"]):
        return "ENCOMENDA_TECNOLOGICA"
    return "SUBVENCAO_ECONOMICA"  # padrão FINEP


def infer_tipo_instrumento_fapesp(title: str) -> str:
    title_lower = title.lower()
    if any(k in title_lower for k in ["pipe", "pesquisa aplicada", "inovação", "inovacao"]):
        return "PESQUISA_APLICADA"
    return "PESQUISA_BASICA"  # padrão FAPESP


# =============================================================================
# PYDANTIC MODEL
# =============================================================================

class EditalRecord(BaseModel):
    """Schema validado para um registro na camada Silver."""
    id: str
    source: str
    tipo_fluxo: str = "EDITAL"            # "EDITAL" | "FLUXO_CONTINUO"
    tipo_instrumento: Optional[str] = None # classificação do instrumento financeiro
    trl_min: Optional[int] = None          # TRL mínimo elegível (1-9); refinado pelo enrichment
    trl_max: Optional[int] = None          # TRL máximo elegível (1-9); refinado pelo enrichment
    title: str
    description: str = ""
    raw_html: Optional[str] = None         # HTML bruto do conteúdo principal (para section_index)
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
# PARSERS
# =============================================================================

def _parse_finep(record: dict) -> dict:
    title = record.get("titulo", "")
    url = record.get("link", "")
    tipo = infer_tipo_instrumento_finep(title)
    trl_min, trl_max = TRL_RANGE_BY_INSTRUMENTO[tipo]
    return {
        "id": generate_id("FINEP", url, title),
        "source": "FINEP",
        "tipo_fluxo": "EDITAL",
        "tipo_instrumento": tipo,
        "trl_min": trl_min,
        "trl_max": trl_max,
        "title": title,
        "description": clean_html(record.get("descricao", "") or record.get("tema", "") or ""),
        "raw_html": record.get("raw_html") or None,
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
    url   = record.get("url", "")

    # Tipo de instrumento: programas (FLUXO_CONTINUO) → SUBVENCAO_ECONOMICA como base;
    # chamadas/editais → CREDITO_INOVACAO (refinado pelo enrichment)
    status_raw = str(record.get("status", "")).upper()
    tipo_fluxo = "FLUXO_CONTINUO" if status_raw == "FLUXO_CONTINUO" else "EDITAL"
    tipo = "SUBVENCAO_ECONOMICA" if tipo_fluxo == "FLUXO_CONTINUO" else "CREDITO_INOVACAO"
    trl_min, trl_max = TRL_RANGE_BY_INSTRUMENTO[tipo]

    # Descrição: prioriza campos ricos do novo scraper; fallback para contexto_capturado
    quem     = clean_html(record.get("quem_pode_solicitar", "") or "")
    financia = clean_html(record.get("o_que_financia", "") or "")
    descricao = clean_html(record.get("descricao", "") or "")
    partes   = [p for p in [descricao, quem, financia] if p]
    description = " | ".join(partes) if partes else clean_html(record.get("contexto_capturado", ""))

    return {
        "id": generate_id("BNDES", url, title),
        "source": "BNDES",
        "tipo_fluxo": tipo_fluxo,
        "tipo_instrumento": tipo,
        "trl_min": trl_min,
        "trl_max": trl_max,
        "title": title,
        "description": description,
        "raw_html": record.get("raw_html") or None,
        "url": url,
        "deadline_date": None,
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": record.get("programa"),
        "target_audience": quem[:300] if quem else None,
        "location": None,
        "value_brl": None,
    }


def _parse_fapesp(record: dict) -> dict:
    title = record.get("titulo", "")
    url = record.get("url", "")
    description = record.get("descricao", "") or record.get("texto_cru", "") or ""
    tipo = infer_tipo_instrumento_fapesp(title)
    trl_min, trl_max = TRL_RANGE_BY_INSTRUMENTO[tipo]
    return {
        "id": generate_id("FAPESP", url, title),
        "source": "FAPESP",
        "tipo_fluxo": "EDITAL",
        "tipo_instrumento": tipo,
        "trl_min": trl_min,
        "trl_max": trl_max,
        "title": title,
        "description": clean_html(description),
        "raw_html": record.get("raw_html") or None,
        "url": url,
        "deadline_date": parse_date_iso(record.get("data_limite")),
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": record.get("areas"),
        "target_audience": record.get("modalidades"),
        "location": "SP",
        "value_brl": None,
    }


def _parse_embrapii_unit(record: dict) -> dict:
    """Parser para unidades Embrapii (fluxo contínuo).

    O scraper raspa o diretório de unidades credenciadas em embrapii.org.br/unidades/.
    Diferente dos editais: sem prazo, sempre aberto, matching por competência técnica.
    A regra de financiamento é fixa: Embrapii ~33%, empresa ~33%, unidade ~34%.
    """
    nome_unidade = record.get("unidade_nome") or record.get("titulo", "")
    title = f"Unidade Embrapii: {nome_unidade}" if nome_unidade else record.get("titulo", "")
    url = record.get("url", "")
    uf = record.get("uf", "")

    # Competências como categoria (join para armazenamento em texto)
    competencias = record.get("competencias", [])
    if isinstance(competencias, list):
        category = ", ".join(competencias) if competencias else None
    else:
        category = str(competencias) if competencias else None

    descricao = clean_html(record.get("descricao", "") or "")
    # Enriquecer descrição com instituição base se disponível
    instituicao = record.get("instituicao_base", "")
    if instituicao and instituicao not in descricao:
        descricao = f"{instituicao}. {descricao}".strip(". ")

    tipo = "MATCHING_EMBRAPII"
    trl_min, trl_max = TRL_RANGE_BY_INSTRUMENTO[tipo]

    return {
        "id": generate_id("EMBRAPII", url, title),
        "source": "EMBRAPII",
        "tipo_fluxo": "FLUXO_CONTINUO",
        "tipo_instrumento": tipo,
        "trl_min": trl_min,
        "trl_max": trl_max,
        "title": title,
        "description": descricao,
        "url": url,
        "deadline_date": None,          # sem prazo — fluxo contínuo
        "status": "FLUXO_CONTINUO",
        "extracted_at": parse_date_flexible(record.get("data_extracao")),
        "category": category,
        "target_audience": None,
        "location": uf or None,
        "value_brl": None,
    }


PARSERS: dict[str, Callable[[dict], dict]] = {
    "FINEP":    _parse_finep,
    "BNDES":    _parse_bndes,
    "FAPESP":   _parse_fapesp,
    "EMBRAPII": _parse_embrapii_unit,
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

    filtered_count = 0
    for json_file in json_files:
        raw_records = load_json_file(json_file)
        for record in raw_records:
            if not _is_relevant(source_name, record):
                filtered_count += 1
                continue
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

    logger.info(
        f"  → {len(processed_records)} válidos, {quarantine_count} em quarentena, "
        f"{filtered_count} filtrados para {source_name}"
    )
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
    # Garante dtypes explícitos em colunas nullable para evitar PyArrow null-type
    # schema mismatch ao ler partições onde todos os valores são None
    for col in ["value_brl", "min_capital_social"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    for col in ["trl_min", "trl_max"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    output_path.mkdir(parents=True, exist_ok=True)
    # Remove arquivos antigos das partições que serão reescritas para evitar acúmulo
    for source in df["source"].unique():
        partition_dir = output_path / f"source={source}"
        if partition_dir.exists():
            for old_file in partition_dir.glob("*.parquet"):
                old_file.unlink()
    df.to_parquet(
        output_path,
        partition_cols=["source"],
        index=False,
        engine="pyarrow"
    )
    logger.info(f"Dados salvos em {output_path} com particionamento por 'source'")


def main(sources_filter: Optional[list[str]] = None):
    """Pipeline ETL Bronze → Silver."""
    logger.info("=" * 60)
    logger.info("INICIANDO ETL BRONZE → SILVER")
    logger.info("=" * 60)

    if not BRONZE_PATH.exists():
        logger.error(f"Pasta Bronze não encontrada: {BRONZE_PATH}")
        return

    all_records = []

    # Carrega registros existentes de fontes que NÃO estão sendo reprocessadas
    if sources_filter and SILVER_PATH.exists():
        for parquet_file in SILVER_PATH.glob("*.parquet"):
            try:
                df_existing = pd.read_parquet(parquet_file)
                kept = df_existing[~df_existing["source"].isin(sources_filter)]
                if not kept.empty:
                    all_records.extend(kept.to_dict("records"))
                    logger.info(f"Mantendo {len(kept)} registros existentes de fontes não alteradas")
            except Exception as e:
                logger.warning(f"Não foi possível ler parquet existente: {e}")

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
        "id", "source", "tipo_fluxo", "tipo_instrumento", "trl_min", "trl_max",
        "title", "description", "url", "deadline_date", "status",
        "extracted_at", "category", "target_audience", "location", "value_brl"
    ]
    df = df[[col for col in column_order if col in df.columns]]

    logger.info("-" * 60)
    logger.info("ESTATÍSTICAS POR FONTE:")
    for source, count in df["source"].value_counts().items():
        logger.info(f"  {source}: {count} registros")
    if "tipo_fluxo" in df.columns:
        for fluxo, count in df["tipo_fluxo"].value_counts().items():
            logger.info(f"  tipo_fluxo={fluxo}: {count} registros")

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
