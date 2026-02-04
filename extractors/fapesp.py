"""
Scraper para chamadas de propostas da FAPESP.
https://fapesp.br/chamadas
"""
import re
from datetime import datetime
from .base import BaseScraper


class FAPESPScraper(BaseScraper):
    """Scraper para editais da FAPESP."""

    BASE_URL = "https://fapesp.br/chamadas"

    def __init__(self):
        super().__init__(source_name="FAPESP", output_subdir="fapesp_raw")
        self.date_pattern = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")

    def extract(self) -> list:
        """Extrai chamadas de propostas da FAPESP."""
        print(f"Acessando: {self.BASE_URL}")

        soup = self._fetch_page(self.BASE_URL, timeout=15)

        # Estrutura FAPESP (atualizada 2025):
        # - div.accordion contém as seções
        # - div.accordion-body__contents contém cada chamada
        # - h3 > a = título e link

        accordion = soup.find('div', class_='accordion')

        if not accordion:
            print("AVISO: Acordeão de chamadas não encontrado.")
            return []

        content_divs = accordion.find_all('div', class_='accordion-body__contents')
        print(f"Analisando {len(content_divs)} seções de conteúdo...")

        chamadas = []

        for content_div in content_divs:
            headers = content_div.find_all('h3')

            for header in headers:
                chamada = self._parse_chamada(header)
                if chamada:
                    chamadas.append(chamada)

        if chamadas:
            self._save(chamadas, prefix="fapesp_scan")

        return chamadas

    def _parse_chamada(self, header) -> dict:
        """Parseia uma chamada a partir do header h3."""
        link_tag = header.find('a')
        if not link_tag:
            return None

        titulo = link_tag.get_text().strip()
        url = link_tag.get('href', '')

        # Tratamento de URL
        if not url.startswith('http'):
            url = f"https://fapesp.br/{url.lstrip('/')}"

        # Captura o texto após o h3 até o próximo h3 ou hr
        texto_completo = self._get_sibling_text(header)

        # Extrai informações estruturadas
        data_limite = self._extract_date(texto_completo)
        areas = self._extract_field(texto_completo, r"Áreas?:\s*([^\n<]+)")
        modalidades = self._extract_field(texto_completo, r"Modalidades?:\s*([^\n<]+)")

        # Determina status
        status, is_fluxo_continuo = self._determine_status(texto_completo, data_limite)

        return {
            "fonte": self.source_name,
            "titulo": titulo,
            "url": url,
            "texto_cru": texto_completo,
            "data_limite": data_limite.strftime("%Y-%m-%d") if data_limite else None,
            "areas": areas,
            "modalidades": modalidades,
            "fluxo_continuo": is_fluxo_continuo,
            "status": status,
            "data_extracao": self._get_date()
        }

    def _get_sibling_text(self, header) -> str:
        """Captura texto dos elementos irmãos até próximo h3 ou hr."""
        texto = ""
        for sibling in header.next_siblings:
            if sibling.name in ['h3', 'hr']:
                break
            if hasattr(sibling, 'get_text'):
                texto += sibling.get_text() + " "
            elif isinstance(sibling, str):
                texto += sibling + " "
        return texto.strip()

    def _extract_date(self, text: str) -> datetime:
        """Extrai data do texto no formato dd/mm/aaaa."""
        match = self.date_pattern.search(text)
        if match:
            try:
                day, month, year = match.groups()
                return datetime(int(year), int(month), int(day))
            except ValueError:
                return None
        return None

    def _extract_field(self, text: str, pattern: str) -> str:
        """Extrai campo usando regex."""
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
        return None

    def _determine_status(self, texto: str, data_limite: datetime) -> tuple:
        """Determina status da chamada."""
        is_fluxo_continuo = False

        if "fluxo contínuo" in texto.lower():
            return "FLUXO_CONTINUO", True
        elif data_limite:
            if data_limite < datetime.now():
                return "ENCERRADA", False
            else:
                return "ABERTA", False

        return "ABERTA", False


# Permite execução direta do arquivo
if __name__ == "__main__":
    scraper = FAPESPScraper()
    scraper.run()
