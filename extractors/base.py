"""
Classe base para todos os scrapers de editais.
Fornece funcionalidades comuns: headers, limpeza de texto, salvamento.
"""
import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime
from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """Classe base abstrata para scrapers de editais."""

    # Headers padrão para evitar bloqueios
    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }

    def __init__(self, source_name: str, output_subdir: str):
        """
        Inicializa o scraper.

        Args:
            source_name: Nome da fonte (ex: "FINEP", "FAPESP")
            output_subdir: Subdiretório para salvar dados (ex: "finep_raw")
        """
        self.source_name = source_name
        self.output_dir = f"bronze_data/{output_subdir}"
        self.headers = self.DEFAULT_HEADERS.copy()
        os.makedirs(self.output_dir, exist_ok=True)

    def _clean_text(self, text: str) -> str:
        """Remove espaços extras, quebras de linha e tabs."""
        if text:
            return ' '.join(text.strip().split())
        return ""

    def _fetch_page(self, url: str, timeout: int = 30) -> BeautifulSoup:
        """
        Faz requisição HTTP e retorna objeto BeautifulSoup.

        Args:
            url: URL para buscar
            timeout: Timeout em segundos

        Returns:
            BeautifulSoup object

        Raises:
            requests.exceptions.RequestException: Em caso de erro de conexão
        """
        response = requests.get(url, headers=self.headers, timeout=timeout)
        response.raise_for_status()
        response.encoding = 'utf-8'
        return BeautifulSoup(response.text, 'html.parser')

    def _save(self, data: list, prefix: str = None) -> str:
        """
        Salva dados em arquivo JSON.

        Args:
            data: Lista de dicionários para salvar
            prefix: Prefixo opcional para o nome do arquivo

        Returns:
            Caminho do arquivo salvo
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = prefix or self.source_name.lower()
        filename = f"{self.output_dir}/{prefix}_{timestamp}.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

        print(f"Salvo em: {filename}")
        return filename

    def _get_timestamp(self) -> str:
        """Retorna timestamp atual formatado."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _get_date(self) -> str:
        """Retorna data atual formatada."""
        return datetime.now().strftime("%Y-%m-%d")

    @abstractmethod
    def extract(self) -> list:
        """
        Método principal de extração. Deve ser implementado por cada scraper.

        Returns:
            Lista de oportunidades extraídas
        """
        pass

    def run(self) -> list:
        """
        Executa o scraper com tratamento de erros.

        Returns:
            Lista de oportunidades extraídas ou lista vazia em caso de erro
        """
        print(f"\n{'='*60}")
        print(f"Iniciando: {self.source_name}")
        print(f"{'='*60}")

        try:
            results = self.extract()
            print(f"Concluído: {len(results)} oportunidades encontradas")
            return results
        except Exception as e:
            print(f"Erro em {self.source_name}: {e}")
            return []
