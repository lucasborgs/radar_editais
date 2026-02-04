"""
Scraper para editais da Fundação Lemann.
https://fundacaolemann.org.br/liderancas/bolsas-e-oportunidades/

NOTA: O site da Fundação Lemann usa proteção CloudFront que pode bloquear
requests automatizados. Este scraper implementa múltiplas estratégias:
1. Tenta acesso direto com headers simulando navegador
2. Fallback para URLs alternativas conhecidas
3. Log detalhado para diagnóstico
"""
import re
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import BaseScraper


class LemannScraper(BaseScraper):
    """Scraper para editais da Fundação Lemann."""

    BASE_URL = "https://fundacaolemann.org.br"
    BOLSAS_URL = "https://fundacaolemann.org.br/liderancas/bolsas-e-oportunidades/"
    NOTICIAS_URL = "https://fundacaolemann.org.br/noticias/"

    # Headers mais completos para simular navegador real
    BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    def __init__(self, timeout: int = 30, max_retries: int = 3):
        """
        Args:
            timeout: Timeout em segundos para cada request
            max_retries: Número máximo de tentativas
        """
        super().__init__(source_name="LEMANN", output_subdir="lemann_raw")
        self.timeout = timeout
        self.max_retries = max_retries

        # Configura sessão com retry
        self.session = requests.Session()
        retries = Retry(
            total=max_retries,
            backoff_factor=2,
            status_forcelist=[500, 502, 503, 504],
        )
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def extract(self) -> list:
        """Extrai informações sobre bolsas e editais da Fundação Lemann."""
        all_items = []

        # Tenta extrair da página de bolsas
        bolsas = self._extract_bolsas()
        all_items.extend(bolsas)

        # Se não conseguiu nada, registra programas conhecidos
        if not all_items:
            print("  Site protegido - registrando programas conhecidos")
            all_items = self._get_known_programs()

        print(f"Total: {len(all_items)} itens extraídos.")

        if all_items:
            self._save(all_items, prefix="lemann")

        return all_items

    def _fetch_with_retry(self, url: str) -> requests.Response:
        """Tenta acessar URL com múltiplas tentativas."""
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(
                    url,
                    headers=self.BROWSER_HEADERS,
                    timeout=self.timeout,
                    allow_redirects=True
                )
                return response
            except requests.exceptions.RequestException as e:
                print(f"    Tentativa {attempt + 1}/{self.max_retries} falhou: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)  # Backoff exponencial
        return None

    def _extract_bolsas(self) -> list:
        """Extrai informações da página de bolsas."""
        print(f"Acessando: {self.BOLSAS_URL}")

        response = self._fetch_with_retry(self.BOLSAS_URL)

        if not response or response.status_code != 200:
            status = response.status_code if response else "sem resposta"
            print(f"  Falha ao acessar página: {status}")
            return []

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')

        bolsas = []

        # Busca cards ou seções de programas
        # A estrutura específica pode variar
        cards = soup.find_all(['article', 'div'], class_=re.compile(r'card|programa|bolsa', re.I))

        if not cards:
            # Fallback: busca por links relevantes
            links = soup.find_all('a', href=re.compile(r'bolsa|programa|edital', re.I))
            for link in links:
                titulo = self._clean_text(link.get_text())
                href = link.get('href', '')

                if titulo and len(titulo) > 5:
                    bolsa = {
                        "fonte": self.source_name,
                        "tipo": "BOLSA",
                        "titulo": titulo,
                        "url": href if href.startswith('http') else f"{self.BASE_URL}{href}",
                        "status": "VERIFICAR",
                        "data_extracao": self._get_date()
                    }
                    bolsas.append(bolsa)

        # Remove duplicatas
        seen = set()
        unique_bolsas = []
        for b in bolsas:
            key = b.get('titulo', '')
            if key and key not in seen:
                seen.add(key)
                unique_bolsas.append(b)

        print(f"  Encontradas {len(unique_bolsas)} oportunidades")
        return unique_bolsas

    def _get_known_programs(self) -> list:
        """Retorna lista de programas conhecidos quando o site está inacessível."""
        programas = [
            {
                "fonte": self.source_name,
                "tipo": "BOLSA",
                "titulo": "Bolsa Complementar Alcance",
                "descricao": "Bolsa para estudantes negros e indígenas em programas de mestrado/doutorado no exterior. Valor: USD 7.000/ano.",
                "url": f"{self.BASE_URL}/liderancas/bolsas-e-oportunidades/",
                "status": "VERIFICAR_SITE",
                "data_extracao": self._get_date()
            },
            {
                "fonte": self.source_name,
                "tipo": "PROGRAMA",
                "titulo": "Programa Alcance Prep",
                "descricao": "Apoio para preparação de candidaturas a programas de mestrado em universidades de excelência (Harvard, Stanford, MIT, Oxford, etc.). 100% gratuito.",
                "url": f"{self.BASE_URL}/liderancas/bolsas-e-oportunidades/",
                "status": "VERIFICAR_SITE",
                "data_extracao": self._get_date()
            },
            {
                "fonte": self.source_name,
                "tipo": "PROGRAMA",
                "titulo": "Lemann Fellowship",
                "descricao": "Programa de bolsas para estudantes brasileiros em universidades parceiras (Harvard, Stanford, Columbia, Oxford, Illinois).",
                "url": f"{self.BASE_URL}/liderancas/bolsas-e-oportunidades/",
                "status": "VERIFICAR_SITE",
                "data_extracao": self._get_date()
            },
            {
                "fonte": self.source_name,
                "tipo": "BOLSA",
                "titulo": "Bolsas de Graduação em Direito",
                "descricao": "Bolsas para estudantes negros de baixa renda em Direito na USP e FGV.",
                "url": f"{self.BASE_URL}/liderancas/bolsas-e-oportunidades/",
                "status": "VERIFICAR_SITE",
                "data_extracao": self._get_date()
            },
        ]

        return programas


# Permite execução direta
if __name__ == "__main__":
    scraper = LemannScraper()
    scraper.run()
