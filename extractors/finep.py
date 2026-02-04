"""
Scraper para chamadas públicas da FINEP.
http://www.finep.gov.br/chamadas-publicas/chamadas-publicas
"""
from .base import BaseScraper


class FINEPScraper(BaseScraper):
    """Scraper para editais da FINEP."""

    BASE_URL = "http://www.finep.gov.br/chamadas-publicas/chamadas-publicas"

    def __init__(self):
        super().__init__(source_name="FINEP", output_subdir="finep_raw")

    def extract(self) -> list:
        """Extrai chamadas públicas da FINEP."""
        print(f"Acessando: {self.BASE_URL}")

        soup = self._fetch_page(self.BASE_URL, timeout=15)

        # Estrutura do site FINEP (atualizada em 2025):
        # - Container principal: div#conteudoChamada
        # - Cada chamada: div.item com h3 > a (título/link)
        # - Informações: .data_pub, .prazo, .fonte, .publico, .tema

        content_div = soup.find('div', id='conteudoChamada')

        if not content_div:
            print("AVISO: Container 'div#conteudoChamada' não encontrado.")
            return []

        items = content_div.find_all('div', class_='item')
        print(f"Encontrados {len(items)} candidatos a editais.")

        chamadas = []

        for item in items:
            try:
                chamada = self._parse_item(item)
                if chamada:
                    chamadas.append(chamada)
            except Exception as e:
                print(f"Erro ao processar item: {e}")
                continue

        if chamadas:
            self._save(chamadas, prefix="finep_chamadas")

        return chamadas

    def _parse_item(self, item) -> dict:
        """Parseia um item de chamada."""
        # Título e Link (estrutura: h3 > a)
        header = item.find('h3')
        if not header:
            return None
        title_tag = header.find('a')
        if not title_tag:
            return None

        titulo = self._clean_text(title_tag.get_text())
        link_relativo = title_tag.get('href', '')
        link = f"http://www.finep.gov.br{link_relativo}" if link_relativo.startswith('/') else link_relativo

        # Extrai dados estruturados
        data_publicacao = self._extract_span_text(item, 'data_pub')
        prazo = self._extract_span_text(item, 'prazo')
        fonte_recurso = self._extract_span_text(item, 'fonte')
        publico_alvo = self._extract_span_text(item, 'publico', span_class='tag')
        tema = self._extract_span_text(item, 'tema')

        # Determina status baseado na seção pai
        status = self._get_status(item)

        return {
            "fonte": self.source_name,
            "titulo": titulo,
            "link": link,
            "status": status,
            "data_publicacao": data_publicacao,
            "prazo_envio": prazo,
            "fonte_recurso": fonte_recurso,
            "publico_alvo": publico_alvo,
            "tema": tema,
            "data_extracao": self._get_timestamp()
        }

    def _extract_span_text(self, item, div_class: str, span_class: str = None) -> str:
        """Extrai texto de span dentro de div com classe específica."""
        div = item.find('div', class_=div_class)
        if not div:
            return None
        span = div.find('span', class_=span_class) if span_class else div.find('span')
        if not span:
            return None
        return self._clean_text(span.get_text())

    def _get_status(self, item) -> str:
        """Determina status baseado na seção pai."""
        parent_section = item.find_parent('div', class_='item-separator-conteudo')
        if not parent_section:
            return "Desconhecido"

        section_header = parent_section.find('h2', class_='header')
        if not section_header:
            return "Desconhecido"

        section_text = section_header.get_text().strip().lower()
        if "aberta" in section_text:
            return "ABERTA"
        elif "encerrada" in section_text:
            return "ENCERRADA"
        elif "resultado" in section_text:
            return "RESULTADO_DIVULGADO"

        return "Desconhecido"


# Permite execução direta do arquivo
if __name__ == "__main__":
    scraper = FINEPScraper()
    scraper.run()
