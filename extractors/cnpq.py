"""
Scraper para chamadas públicas do CNPq.
http://memoria2.cnpq.br/web/guest/chamadas-publicas
"""
import re
from .base import BaseScraper


class CNPqScraper(BaseScraper):
    """Scraper para chamadas públicas do CNPq."""

    BASE_URL = "http://memoria2.cnpq.br/web/guest/chamadas-publicas"
    PORTLET_PARAM = "p_p_id=resultadosportlet_WAR_resultadoscnpqportlet_INSTANCE_0ZaM"

    def __init__(self, incluir_encerradas: bool = False):
        """
        Args:
            incluir_encerradas: Se True, também busca chamadas encerradas
        """
        super().__init__(source_name="CNPQ", output_subdir="cnpq_raw")
        self.incluir_encerradas = incluir_encerradas

    def extract(self) -> list:
        """Extrai chamadas públicas do CNPq."""
        all_chamadas = []

        # Busca chamadas abertas
        chamadas_abertas = self._fetch_chamadas("abertas")
        all_chamadas.extend(chamadas_abertas)

        # Opcionalmente busca encerradas (para histórico)
        if self.incluir_encerradas:
            chamadas_encerradas = self._fetch_chamadas("encerradas")
            all_chamadas.extend(chamadas_encerradas)

        print(f"Total: {len(all_chamadas)} chamadas extraídas.")

        if all_chamadas:
            self._save(all_chamadas, prefix="cnpq")

        return all_chamadas

    def _fetch_chamadas(self, filtro: str) -> list:
        """Busca chamadas com um filtro específico (abertas/encerradas)."""
        url = f"{self.BASE_URL}?{self.PORTLET_PARAM}&filtro={filtro}"
        print(f"Acessando: {url}")

        soup = self._fetch_page(url)
        chamadas = []

        # Lista de chamadas está em <ol class="list-chamadas">
        lista = soup.find('ol', class_='list-chamadas')
        if not lista:
            print(f"  Lista de chamadas não encontrada para filtro={filtro}")
            return []

        items = lista.find_all('li', recursive=False)
        print(f"  Encontrados {len(items)} itens ({filtro})")

        for item in items:
            chamada = self._parse_chamada(item, filtro)
            if chamada:
                chamadas.append(chamada)

        return chamadas

    def _parse_chamada(self, item, filtro: str) -> dict:
        """Extrai dados de uma chamada individual."""
        # Título está em <h4>
        h4 = item.find('h4')
        if not h4:
            return None

        titulo = self._clean_text(h4.get_text())
        if not titulo or len(titulo) < 5:
            return None

        # Descrição está no <p> dentro de <div class="content">
        content_div = item.find('div', class_='content')
        descricao = None
        if content_div:
            p = content_div.find('p')
            if p:
                descricao = self._clean_text(p.get_text())

        # Datas estão em <ul class="datas">
        prazo_inicio = None
        prazo_fim = None
        datas_ul = item.find('ul', class_='datas')
        if datas_ul:
            li = datas_ul.find('li')
            if li:
                prazo_inicio, prazo_fim = self._parse_periodo(li.get_text())

        # URL - busca no link de compartilhamento
        url = None
        share_link = item.find('a', class_='facebook')
        if share_link:
            href = share_link.get('href', '')
            match = re.search(r'idDivulgacao%3D(\d+)', href)
            if match:
                id_divulgacao = match.group(1)
                url = f"{self.BASE_URL}?{self.PORTLET_PARAM}&filtro={filtro}&detalha=chamadaDivulgada&idDivulgacao={id_divulgacao}"

        # Se não encontrou URL pelo share, tenta link direto
        if not url:
            link = item.find('a', href=True)
            if link:
                url = link.get('href')

        # Extrai número da chamada do título
        numero_chamada = self._extract_numero(titulo)

        # Determina status baseado no filtro
        status = "ABERTA" if filtro == "abertas" else "ENCERRADA"

        return {
            "fonte": self.source_name,
            "titulo": titulo,
            "numero": numero_chamada,
            "url": url,
            "descricao": descricao,
            "prazo_inicio": prazo_inicio,
            "prazo_fim": prazo_fim,
            "status": status,
            "data_extracao": self._get_date()
        }

    def _parse_periodo(self, texto: str) -> tuple:
        """Extrai datas de período no formato 'DD/MM/YYYY a DD/MM/YYYY'."""
        texto = texto.strip()
        match = re.search(r'(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})', texto)
        if match:
            return match.group(1), match.group(2)
        return None, None

    def _extract_numero(self, titulo: str) -> str:
        """Extrai número da chamada do título."""
        # Padrões comuns: "Nº 02/2026", "Nº 32/2025", "N° 28/2025"
        match = re.search(r'N[ºo°]\s*(\d+/\d{4})', titulo, re.IGNORECASE)
        if match:
            return match.group(1)
        return None


# Permite execução direta
if __name__ == "__main__":
    scraper = CNPqScraper(incluir_encerradas=False)
    scraper.run()
