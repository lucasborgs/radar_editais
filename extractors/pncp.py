"""
Extrator para o Portal Nacional de Contratações Públicas (PNCP).
Usa API REST oficial: https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao

Diferente dos outros scrapers, este usa API e processa por período/modalidade.
"""
import requests
import json
import time
import os
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import BaseScraper


# Códigos das modalidades de contratação no PNCP
MODALIDADES = {
    1: "Leilão - Lei 14.133/2021",
    2: "Diálogo Competitivo",
    3: "Concurso",
    4: "Concorrência - Lei 14.133/2021",
    5: "Pregão - Lei 14.133/2021",
    6: "Dispensa de Licitação",
    7: "Inexigibilidade",
    8: "Pré-qualificação",
}


class PNCPScraper(BaseScraper):
    """
    Extrator para o Portal Nacional de Contratações Públicas.

    Diferentemente dos outros scrapers, este usa API REST e processa
    dados por período e modalidade de contratação.
    """

    API_URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"

    def __init__(self, dias_retroativos: int = 30, modalidades: list = None):
        """
        Args:
            dias_retroativos: Quantos dias para trás buscar (padrão: 30)
            modalidades: Lista de códigos de modalidade (padrão: todas)
        """
        super().__init__(source_name="PNCP", output_subdir="pncp_raw")

        self.dias_retroativos = dias_retroativos
        self.modalidades = modalidades or list(MODALIDADES.keys())

        # Configura sessão com retry automático
        self.session = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def extract(self) -> list:
        """Extrai contratações do PNCP via API."""
        # Define período de busca
        data_fim = datetime.now()
        data_inicio = data_fim - timedelta(days=self.dias_retroativos)

        print(f"Período: {data_inicio.strftime('%d/%m/%Y')} a {data_fim.strftime('%d/%m/%Y')}")
        print(f"Modalidades: {len(self.modalidades)}")

        all_items = []

        # Processa mês a mês
        data_atual = data_inicio
        while data_atual <= data_fim:
            proximo_mes = (data_atual.replace(day=28) + timedelta(days=4)).replace(day=1)
            fim_periodo = min(proximo_mes - timedelta(days=1), data_fim)

            items_periodo = self._process_periodo(data_atual, fim_periodo)
            all_items.extend(items_periodo)

            data_atual = proximo_mes

        # Salva resultado consolidado
        if all_items:
            self._save(all_items, prefix="pncp_consolidado")

        return all_items

    def _process_periodo(self, data_inicio: datetime, data_fim: datetime) -> list:
        """Processa um período específico."""
        str_inicio = data_inicio.strftime("%Y%m%d")
        str_fim = data_fim.strftime("%Y%m%d")

        print(f"\n  Período: {str_inicio} - {str_fim}")

        items = []

        for modalidade in self.modalidades:
            modalidade_items = self._fetch_modalidade(str_inicio, str_fim, modalidade)
            items.extend(modalidade_items)

        return items

    def _fetch_modalidade(self, str_inicio: str, str_fim: str, modalidade: int) -> list:
        """Busca contratações de uma modalidade específica."""
        items = []
        pagina = 1

        while True:
            params = {
                "dataInicial": str_inicio,
                "dataFinal": str_fim,
                "codigoModalidadeContratacao": modalidade,
                "pagina": pagina,
                "tamanhoPagina": 50
            }

            try:
                response = self.session.get(self.API_URL, params=params, timeout=15)

                if response.status_code == 200:
                    conteudo = response.json()
                    dados_pagina = conteudo.get('data', [])

                    if not dados_pagina:
                        break

                    # Enriquece cada item com metadados
                    for item in dados_pagina:
                        item['_fonte'] = self.source_name
                        item['_modalidade_codigo'] = modalidade
                        item['_modalidade_nome'] = MODALIDADES.get(modalidade, "Desconhecida")
                        item['_data_extracao'] = self._get_date()

                    items.extend(dados_pagina)
                    print(f"    Mod {modalidade} | Pág {pagina}: {len(dados_pagina)} itens")

                    # Salva página individual (backup incremental)
                    self._save_page(dados_pagina, str_inicio, modalidade, pagina)

                    if len(dados_pagina) < 50:
                        break  # Última página

                    pagina += 1
                    time.sleep(0.2)  # Rate limiting

                else:
                    print(f"    Mod {modalidade} | Erro API: {response.status_code}")
                    break

            except Exception as e:
                print(f"    Mod {modalidade} | Erro: {e}")
                time.sleep(2)
                break

        return items

    def _save_page(self, data: list, str_inicio: str, modalidade: int, pagina: int):
        """Salva página individual como backup."""
        filename = f"{self.output_dir}/pncp_{str_inicio}_mod{modalidade}_pg{pagina}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# Permite execução direta
if __name__ == "__main__":
    # Por padrão, busca últimos 30 dias
    scraper = PNCPScraper(dias_retroativos=30)
    scraper.run()
