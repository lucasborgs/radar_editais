"""
ETL Bronze → Silver (Radar de Editais)

Script para processar dados da Camada Bronze para a Silver,
normalizando 9 fontes heterogêneas para um schema unificado
com particionamento Hive por fonte.

Autor: Pipeline Radar Editais
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

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Mapeamento de pasta para nome normalizado da fonte
SOURCE_MAPPING = {
    "itausocial_raw": "ITAU_SOCIAL",
    "cnpq_raw": "CNPQ",
    "finep_raw": "FINEP",
    "arapyau_raw": "ARAPYAU",
    "bndes_raw": "BNDES",
    "fapesp_raw": "FAPESP",
    "lemann_raw": "LEMANN",
    "serrapilheira_raw": "SERRAPILHEIRA",
    "pncp_raw": "PNCP",
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
    "Divulgada no PNCP": "ABERTA",
}

# =============================================================================
# FUNÇÕES UTILITÁRIAS
# =============================================================================

def clean_html(text: Optional[str]) -> str:
    """
    Remove tags HTML e normaliza espaços/quebras de linha.

    Args:
        text: Texto potencialmente contendo HTML

    Returns:
        Texto limpo sem HTML
    """
    if not text:
        return ""

    # Converte para string se necessário
    text = str(text)

    # Decodifica entidades HTML (&#8217; → ', &amp; → &, etc.)
    text = html.unescape(text)

    # Remove HTML usando BeautifulSoup se disponível
    if HAS_BS4 and ("<" in text and ">" in text):
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text(separator=" ")
    else:
        # Fallback: regex para remover tags HTML
        text = re.sub(r'<[^>]+>', ' ', text)

    # Normaliza quebras de linha e espaços
    text = text.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ')
    text = re.sub(r'\s+', ' ', text)

    return text.strip()


def parse_date_br(date_str: Optional[str]) -> Optional[date]:
    """
    Converte data no formato DD/MM/YYYY para objeto date.

    Args:
        date_str: String no formato DD/MM/YYYY

    Returns:
        Objeto date ou None se inválido
    """
    if not date_str:
        return None

    try:
        return datetime.strptime(date_str.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None


def parse_date_iso(date_str: Optional[str]) -> Optional[date]:
    """
    Converte data no formato ISO 8601 (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS) para objeto date.

    Args:
        date_str: String no formato ISO 8601

    Returns:
        Objeto date ou None se inválido
    """
    if not date_str:
        return None

    try:
        # Remove parte do tempo se existir
        date_part = date_str.split("T")[0].strip()
        return datetime.strptime(date_part, "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def parse_date_flexible(date_str: Optional[str]) -> Optional[date]:
    """
    Tenta múltiplos formatos de data.

    Args:
        date_str: String de data em formato desconhecido

    Returns:
        Objeto date ou None se inválido
    """
    if not date_str:
        return None

    # Tenta ISO primeiro
    result = parse_date_iso(date_str)
    if result:
        return result

    # Tenta formato brasileiro
    result = parse_date_br(date_str)
    if result:
        return result

    return None


def generate_id(source: str, url: str, title: str) -> str:
    """
    Gera ID único baseado em hash SHA256.

    Args:
        source: Nome da fonte
        url: URL do edital
        title: Título do edital

    Returns:
        Hash de 16 caracteres
    """
    content = f"{source}|{url or ''}|{title or ''}"
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def normalize_status(status: Optional[str]) -> str:
    """
    Normaliza valores de status para padrão unificado.

    Args:
        status: Status original da fonte

    Returns:
        Status normalizado
    """
    if not status:
        return "VERIFICAR"

    return STATUS_MAPPING.get(status.strip(), "VERIFICAR")


def safe_get(record: dict, *keys, default=None):
    """
    Obtém valor aninhado de forma segura.

    Args:
        record: Dicionário fonte
        keys: Sequência de chaves para navegação
        default: Valor padrão se não encontrar

    Returns:
        Valor encontrado ou default
    """
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
# PARSERS ESPECÍFICOS POR FONTE
# =============================================================================

def _parse_itausocial(record: dict) -> dict:
    """
    Parser para dados do Itaú Social.

    Campos esperados: fonte, titulo, url, descricao, status, data_extracao
    """
    title = record.get("titulo", "")
    url = record.get("url", "")

    return {
        "id": generate_id("ITAU_SOCIAL", url, title),
        "source": "ITAU_SOCIAL",
        "title": title,
        "description": clean_html(record.get("descricao", "")),
        "url": url,
        "deadline_date": None,
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": None,
        "target_audience": None,
        "location": None,
        "value_brl": None,
    }


def _parse_cnpq(record: dict) -> dict:
    """
    Parser para dados do CNPq.

    Campos esperados: fonte, titulo, numero, url, descricao, prazo_inicio, prazo_fim, status, data_extracao
    """
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
    """
    Parser para dados da FINEP.

    Campos esperados: fonte, titulo, link, status, data_publicacao, prazo_envio,
                      fonte_recurso, publico_alvo, tema, data_extracao
    """
    title = record.get("titulo", "")
    url = record.get("link", "")  # ATENÇÃO: campo é 'link', não 'url'

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


def _parse_arapyau(record: dict) -> dict:
    """
    Parser para dados do Instituto Arapyaú.

    Estrutura variável: tipo pode ser PROGRAMA ou NOTICIA
    """
    tipo = record.get("tipo", "")
    title = record.get("titulo", "")
    url = record.get("url", "")

    # Para PROGRAMA, concatena ciclos como descrição
    if tipo == "PROGRAMA":
        ciclos = record.get("ciclos", [])
        description = " ".join(ciclos) if isinstance(ciclos, list) else str(ciclos or "")
    else:
        description = record.get("descricao", "") or ""

    return {
        "id": generate_id("ARAPYAU", url, title),
        "source": "ARAPYAU",
        "title": title,
        "description": clean_html(description),
        "url": url,
        "deadline_date": None,
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": tipo,
        "target_audience": None,
        "location": None,
        "value_brl": None,
    }


def _parse_bndes(record: dict) -> dict:
    """
    Parser para dados do BNDES.

    Campos esperados: fonte, programa, titulo, url, contexto_capturado, status, data_extracao
    Nota: fonte pode vir como 'BNDES_INOVACAO', normalizar para 'BNDES'
    """
    title = record.get("titulo", "")
    url = record.get("url", "")

    return {
        "id": generate_id("BNDES", url, title),
        "source": "BNDES",  # Normalizado (não usar record.get("fonte"))
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
    """
    Parser para dados da FAPESP.

    Campos esperados: fonte, titulo, url, texto_cru, data_limite, areas,
                      modalidades, fluxo_continuo, status, data_extracao
    """
    title = record.get("titulo", "")
    url = record.get("url", "")

    # Texto cru precisa de limpeza especial
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
        "location": "SP",  # FAPESP é sempre São Paulo
        "value_brl": None,
    }


def _parse_lemann(record: dict) -> dict:
    """
    Parser para dados da Fundação Lemann.

    Campos esperados: fonte, tipo, titulo, descricao, url, status, data_extracao
    """
    title = record.get("titulo", "")
    url = record.get("url", "")

    return {
        "id": generate_id("LEMANN", url, title),
        "source": "LEMANN",
        "title": title,
        "description": clean_html(record.get("descricao", "")),
        "url": url,
        "deadline_date": None,
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": record.get("tipo"),  # BOLSA ou PROGRAMA
        "target_audience": None,
        "location": None,
        "value_brl": None,
    }


def _parse_serrapilheira(record: dict) -> dict:
    """
    Parser para dados do Instituto Serrapilheira.

    Campos esperados: fonte, titulo, url, ano, categoria, status, data_extracao
    Nota: campo 'ano' pode ser string, int ou null
    """
    title = record.get("titulo", "")
    url = record.get("url", "")

    return {
        "id": generate_id("SERRAPILHEIRA", url, title),
        "source": "SERRAPILHEIRA",
        "title": title,
        "description": "",  # Serrapilheira não tem descrição no bronze
        "url": url,
        "deadline_date": None,
        "status": normalize_status(record.get("status")),
        "extracted_at": parse_date_iso(record.get("data_extracao")),
        "category": record.get("categoria"),
        "target_audience": None,
        "location": None,
        "value_brl": None,
    }


def _parse_pncp(record: dict) -> dict:
    """
    Parser para dados do Portal Nacional de Contratações Públicas.

    Estrutura complexa com objetos aninhados: orgaoEntidade, unidadeOrgao, amparoLegal
    """
    # Extrai dados de objetos aninhados
    razao_social = safe_get(record, "orgaoEntidade", "razaoSocial", default="")
    objeto_compra = record.get("objetoCompra", "")
    uf_sigla = safe_get(record, "unidadeOrgao", "ufSigla", default="")
    municipio = safe_get(record, "unidadeOrgao", "municipioNome", default="")

    # Usa numeroControlePNCP como base para ID se disponível
    numero_controle = record.get("numeroControlePNCP", "")
    url = record.get("linkSistemaOrigem", "")
    title = objeto_compra

    # Monta descrição combinando razão social e objeto
    description = f"{razao_social} - {objeto_compra}" if razao_social else objeto_compra

    # Localização
    location = f"{municipio}/{uf_sigla}" if municipio and uf_sigla else uf_sigla

    # Valor
    valor = record.get("valorTotalEstimado")
    if valor is not None:
        try:
            valor = float(valor)
        except (ValueError, TypeError):
            valor = None

    return {
        "id": numero_controle if numero_controle else generate_id("PNCP", url, title),
        "source": "PNCP",
        "title": title,
        "description": clean_html(description),
        "url": url,
        "deadline_date": parse_date_iso(record.get("dataEncerramentoProposta")),
        "status": normalize_status(record.get("situacaoCompraNome")),
        "extracted_at": parse_date_iso(record.get("dataAtualizacao")),
        "category": record.get("modalidadeNome"),
        "target_audience": None,
        "location": location,
        "value_brl": valor,
    }


# Registro de parsers
PARSERS: dict[str, Callable[[dict], dict]] = {
    "ITAU_SOCIAL": _parse_itausocial,
    "CNPQ": _parse_cnpq,
    "FINEP": _parse_finep,
    "ARAPYAU": _parse_arapyau,
    "BNDES": _parse_bndes,
    "FAPESP": _parse_fapesp,
    "LEMANN": _parse_lemann,
    "SERRAPILHEIRA": _parse_serrapilheira,
    "PNCP": _parse_pncp,
}


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def load_json_file(file_path: Path) -> list[dict]:
    """
    Carrega arquivo JSON e retorna lista de registros.

    Args:
        file_path: Caminho do arquivo JSON

    Returns:
        Lista de dicionários (registros)
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Se for uma lista, retorna direto; se for dict único, envolve em lista
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
    """
    Processa todos os arquivos JSON de uma fonte.

    Args:
        source_path: Caminho da pasta da fonte
        source_name: Nome normalizado da fonte

    Returns:
        Lista de registros processados
    """
    parser = PARSERS.get(source_name)
    if not parser:
        logger.warning(f"Parser não encontrado para fonte: {source_name}")
        return []

    processed_records = []
    json_files = list(source_path.glob("*.json"))

    logger.info(f"Processando {source_name}: {len(json_files)} arquivos encontrados")

    for json_file in json_files:
        raw_records = load_json_file(json_file)

        for record in raw_records:
            try:
                parsed = parser(record)
                parsed["created_at"] = datetime.now()
                processed_records.append(parsed)
            except Exception as e:
                logger.warning(f"Erro ao processar registro em {json_file}: {e}")
                continue

    logger.info(f"  → {len(processed_records)} registros processados para {source_name}")
    return processed_records


def deduplicate_records(records: list[dict]) -> list[dict]:
    """
    Remove registros duplicados baseado no campo 'id'.

    Args:
        records: Lista de registros

    Returns:
        Lista sem duplicatas
    """
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
    """
    Salva DataFrame em formato Parquet com Hive Partitioning.

    Args:
        df: DataFrame a ser salvo
        output_path: Caminho de saída
    """
    # Garante que deadline_date seja datetime (para compatibilidade Parquet)
    if "deadline_date" in df.columns:
        df["deadline_date"] = pd.to_datetime(df["deadline_date"], errors="coerce")

    if "extracted_at" in df.columns:
        df["extracted_at"] = pd.to_datetime(df["extracted_at"], errors="coerce")

    # Cria diretório de saída se não existir
    output_path.mkdir(parents=True, exist_ok=True)

    # Salva com particionamento
    df.to_parquet(
        output_path,
        partition_cols=["source"],
        index=False,
        engine="pyarrow"
    )

    logger.info(f"Dados salvos em {output_path} com particionamento por 'source'")


def main():
    """
    Função principal do ETL Bronze → Silver.
    """
    logger.info("=" * 60)
    logger.info("INICIANDO ETL BRONZE → SILVER")
    logger.info("=" * 60)

    if not BRONZE_PATH.exists():
        logger.error(f"Pasta Bronze não encontrada: {BRONZE_PATH}")
        return

    all_records = []

    # Processa cada fonte
    for folder_name, source_name in SOURCE_MAPPING.items():
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

    # Deduplicação
    unique_records = deduplicate_records(all_records)

    # Converte para DataFrame
    df = pd.DataFrame(unique_records)

    # Reordena colunas conforme schema
    column_order = [
        "id", "source", "title", "description", "url", "deadline_date",
        "created_at", "status", "extracted_at", "category", "target_audience",
        "location", "value_brl"
    ]
    df = df[[col for col in column_order if col in df.columns]]

    # Log de estatísticas
    logger.info("-" * 60)
    logger.info("ESTATÍSTICAS POR FONTE:")
    for source, count in df["source"].value_counts().items():
        logger.info(f"  {source}: {count} registros")

    # Salva em Parquet
    save_to_parquet(df, SILVER_PATH)

    logger.info("=" * 60)
    logger.info("ETL CONCLUÍDO COM SUCESSO")
    logger.info(f"Total de registros únicos: {len(df)}")
    logger.info(f"Saída: {SILVER_PATH}/")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
