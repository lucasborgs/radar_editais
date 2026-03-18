"""
Scraper para chamadas de propostas da FAPESP.

Modo produção (padrão):
  Acessa https://fapesp.br/chamadas (estrutura h3 > a + p com campos).
  Captura chamadas do ano corrente.
  Para cada chamada, busca a página de detalhe para capturar a descrição completa.

Modo histórico (include_historical=True):
  Acessa páginas de arquivo por ano (desde 2020 por padrão).
  Páginas de arquivo usam estrutura diferente: p > a + texto seguinte.
  Suporta meta-refresh redirect (ex: fapesp.br/17333/ → fapesp.br/17333/chamadas-de-propostas-2024).
  Para cada chamada, busca a página de detalhe (ex: https://fapesp.br/8386/...).
"""
import re
from datetime import datetime

from .base import BaseScraper


class FAPESPScraper(BaseScraper):
    """Scraper para editais da FAPESP."""

    BASE_URL = "https://fapesp.br/chamadas"

    # Página do ano corrente — ponto de entrada principal para chamadas de 2026.
    # Os links para outros anos ficam em ul.internal dentro desta página.
    CURRENT_YEAR_URL = "https://fapesp.br/2185/chamadas-de-propostas-2026"

    # Fallback caso a descoberta dinâmica falhe
    HISTORICAL_YEAR_URLS: list = [
        "https://fapesp.br/17955/chamadas-de-propostas-2025",
        "https://fapesp.br/17333/chamadas-de-propostas-2024",
        "https://fapesp.br/16576/chamadas-de-propostas-2023",
        "https://fapesp.br/15851/chamadas-de-propostas-2022",
        "https://fapesp.br/15281/chamadas-de-propostas-2021",
        "https://fapesp.br/14746/chamadas-de-propostas-2020",
    ]

    # Padrão URL de chamadas FAPESP: https://fapesp.br/NNNNN (ID numérico ≥ 4 dígitos)
    _CHAMADA_URL_RE = re.compile(r"https://fapesp\.br/(\d{4,})")

    # Seletores CSS para extrair conteúdo da página de detalhe (tentados em ordem).
    # FAPESP usa WordPress; o conteúdo principal fica em div.entry-content ou div#conteudo.
    _DETAIL_SELECTORS = [
        ("div", {"class": "entry-content"}),
        ("div", {"id": "conteudo"}),
        ("main", {}),
        ("article", {}),
    ]

    def __init__(self):
        super().__init__(source_name="FAPESP", output_subdir="fapesp_raw")
        self._date_re = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})")

    def extract(self, include_historical: bool = False) -> list:
        """Extrai chamadas de propostas da FAPESP.

        Args:
            include_historical: Se True, acessa as páginas de arquivo por ano
                                além da página principal (chamadas mais antigas/encerradas).
        """
        all_results = []
        seen_urls: set = set()

        # Página principal (chamadas em destaque / abertas)
        for item in self._scrape_main_page(self.BASE_URL):
            url = item.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_results.append(item)

        # Página do ano corrente (listagem completa de 2026)
        for item in self._scrape_year_page(self.CURRENT_YEAR_URL):
            url = item.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_results.append(item)

        if include_historical:
            # Descobre anos anteriores dinamicamente via ul.internal da página corrente
            year_urls = self._discover_year_urls(self.CURRENT_YEAR_URL) or self.HISTORICAL_YEAR_URLS
            print(f"Modo histórico FAPESP: {len(year_urls)} anos anteriores encontrados")
            for year_url in year_urls:
                if year_url == self.CURRENT_YEAR_URL:
                    continue  # já scrapeado acima
                for item in self._scrape_year_page(year_url):
                    url = item.get("url", "")
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(item)

        if all_results:
            prefix = "fapesp_historical" if include_historical else "fapesp_scan"
            self._save(all_results, prefix=prefix)

        return all_results

    # =========================================================================
    # Página principal (estrutura: div.accordion > h3 > a + siblings)
    # =========================================================================

    def _scrape_main_page(self, url: str) -> list:
        """Extrai chamadas da página principal https://fapesp.br/chamadas."""
        print(f"Acessando: {url}")
        try:
            soup = self._fetch_page(url, timeout=15)
        except Exception as e:
            print(f"Erro ao acessar {url}: {e}")
            return []

        # A página principal usa um acordeão; fallback para busca geral de h3
        accordion = soup.find("div", class_="accordion")
        if accordion:
            headers = accordion.find_all("h3")
        else:
            print("AVISO: accordion não encontrado; usando fallback h3 global")
            headers = soup.find_all("h3")

        print(f"Analisando {len(headers)} seções h3...")

        chamadas = []
        for h3 in headers:
            try:
                chamada = self._parse_h3_chamada(h3)
                if chamada:
                    chamadas.append(chamada)
            except Exception as e:
                print(f"Erro ao processar h3: {e}")

        print(f"Extraídas {len(chamadas)} chamadas da página principal.")
        return chamadas

    def _parse_h3_chamada(self, h3) -> dict:
        """Parseia chamada a partir de h3 > a (estrutura da página principal)."""
        link_tag = h3.find("a")
        if not link_tag:
            return None

        titulo = link_tag.get_text().strip()
        url = link_tag.get("href", "")
        if not url.startswith("http"):
            url = f"https://fapesp.br/{url.lstrip('/')}"

        # Captura texto dos siblings até próximo h3 ou hr
        texto = self._get_sibling_text(h3)

        data_limite = self._extract_date(texto)
        areas = self._extract_field(texto, r"[aÁ]reas?:\s*([^\n<]+)")
        modalidades = self._extract_field(texto, r"[mM]odalidades?:\s*([^\n<]+)")
        chamada_num = self._extract_field(texto, r"Chamada FAPESP\s*([\d/]+)")
        apoio = self._extract_field(texto, r"[aA]poio:\s*([^\n<]+)")

        status, fluxo_continuo = self._determine_status(texto, data_limite)

        # Busca descrição completa e HTML da página de detalhe
        descricao, raw_html = self._fetch_detail_description(url)

        return {
            "fonte": self.source_name,
            "titulo": titulo,
            "url": url,
            "chamada_num": chamada_num,
            "texto_cru": texto[:500],
            "descricao": descricao,
            "raw_html": raw_html,
            "data_limite": data_limite.strftime("%Y-%m-%d") if data_limite else None,
            "areas": areas,
            "modalidades": modalidades,
            "apoio": apoio,
            "fluxo_continuo": fluxo_continuo,
            "status": status,
            "data_extracao": self._get_date(),
        }

    # =========================================================================
    # Páginas de arquivo por ano
    # Estrutura real: <li><p><a href>Title</a><br>Chamada FAPESP NN/YYYY<br>Prazo...</p></li>
    # Todo o metadata fica dentro do mesmo <p>, após a tag <a>.
    # =========================================================================

    def _scrape_year_page(self, url: str) -> list:
        """Extrai chamadas de uma página de arquivo por ano da FAPESP."""
        print(f"Acessando arquivo: {url}")
        try:
            soup = self._fetch_with_meta_refresh(url, timeout=20)
        except Exception as e:
            print(f"Erro ao acessar {url}: {e}")
            return []

        # Encontra <p> que contêm um link de chamada.
        # Links de navegação ficam em <li><a> ou <h2><a>, não em <p><a>.
        chamadas = []
        seen_in_page: set = set()

        for p_tag in soup.find_all("p"):
            a_tag = p_tag.find("a", href=self._CHAMADA_URL_RE)
            if not a_tag:
                continue

            href = a_tag.get("href", "")
            if href in seen_in_page:
                continue
            seen_in_page.add(href)

            titulo = a_tag.get_text().strip()
            if len(titulo) < 10:
                continue

            # Metadata está no <p> após o <a> (separado por <br>).
            # Obtemos o texto completo do <p>, subtraindo o título no início.
            p_full = p_tag.get_text(" ", strip=True)
            context_text = p_full[len(titulo):].strip()

            data_limite = self._extract_date(context_text)
            chamada_num = self._extract_field(context_text, r"Chamada FAPESP\s*([\d/]+)")
            apoio = self._extract_field(context_text, r"[aA]poio:\s*([^\n]+)")

            # Filtro de qualidade: pula itens sem data E sem número de chamada
            # (típico de links de navegação capturados acidentalmente)
            if not data_limite and not chamada_num:
                continue

            status, fluxo_continuo = self._determine_status(context_text, data_limite)

            # Busca descrição completa e HTML da página de detalhe
            descricao, raw_html = self._fetch_detail_description(href)

            chamadas.append({
                "fonte": self.source_name,
                "titulo": titulo,
                "url": href,
                "chamada_num": chamada_num,
                "texto_cru": context_text[:500],
                "descricao": descricao,
                "raw_html": raw_html,
                "data_limite": data_limite.strftime("%Y-%m-%d") if data_limite else None,
                "areas": None,
                "modalidades": None,
                "apoio": apoio,
                "fluxo_continuo": fluxo_continuo,
                "status": status,
                "data_extracao": self._get_date(),
            })

        n_with_desc = sum(1 for c in chamadas if c.get("descricao"))
        print(f"  → {len(chamadas)} chamadas extraídas de {url.split('/')[-1]} ({n_with_desc} com descrição)")
        return chamadas

    # =========================================================================
    # Utilitários de parsing
    # =========================================================================

    def _discover_year_urls(self, current_year_url: str) -> list:
        """Descobre URLs de outros anos a partir do ul.internal da página corrente."""
        try:
            soup = self._fetch_with_meta_refresh(current_year_url, timeout=20)
            ul = soup.find("ul", class_="internal")
            if not ul:
                return []
            urls = []
            for a in ul.find_all("a", href=True):
                href = a["href"]
                if "chamadas-de-propostas" in href and href != current_year_url:
                    urls.append(href)
            return urls
        except Exception as e:
            print(f"Aviso: falha ao descobrir anos anteriores: {e}")
            return []

    def _fetch_detail_description(self, url: str) -> tuple[str, str]:
        """Busca o texto completo da página de detalhe da chamada FAPESP.

        A página de detalhe (ex: https://fapesp.br/8386/...) contém a introdução
        completa do edital, objetivos, áreas elegíveis, valores e critérios —
        informações ausentes na página de listagem.

        Returns:
            (text, raw_html) — texto limpo e HTML bruto do elemento de conteúdo.
        """
        if not url:
            return "", ""
        try:
            soup = self._fetch_with_meta_refresh(url, timeout=20)
            for tag_name, attrs in self._DETAIL_SELECTORS:
                el = soup.find(tag_name, attrs) if attrs else soup.find(tag_name)
                if not el:
                    continue
                # Remove elementos não-textuais
                for noise in el.find_all(["script", "style", "nav", "footer", "header"]):
                    noise.decompose()
                text = " ".join(el.get_text(" ", strip=True).split())
                if len(text) > 200:
                    return text, el.decode_contents()
        except Exception as e:
            print(f"  Aviso: detalhe não disponível para {url}: {e}")
        return "", ""

    def _fetch_with_meta_refresh(self, url: str, timeout: int = 20):
        """Busca uma página e segue meta-refresh se presente."""
        soup = self._fetch_page(url, timeout=timeout)
        meta = soup.find("meta", attrs={"http-equiv": re.compile(r"refresh", re.I)})
        if meta:
            content = meta.get("content", "")
            # Extrai URL do content="0; URL='...'"
            match = re.search(r"URL=['\"]?([^'\">\s]+)", content, re.I)
            if match:
                redirect_url = match.group(1).strip("'\"")
                print(f"  meta-refresh → {redirect_url}")
                soup = self._fetch_page(redirect_url, timeout=timeout)
        return soup

    def _get_sibling_text(self, h3) -> str:
        """Captura texto dos siblings de h3 até próximo h3 ou hr."""
        texto = ""
        for sibling in h3.next_siblings:
            if getattr(sibling, "name", None) in ["h3", "hr"]:
                break
            if hasattr(sibling, "get_text"):
                texto += sibling.get_text() + " "
            elif isinstance(sibling, str):
                texto += sibling + " "
        return texto.strip()

    def _extract_date(self, text: str) -> datetime:
        """Extrai primeira data no formato dd/mm/aaaa."""
        match = self._date_re.search(text)
        if match:
            try:
                day, month, year = match.group(1).split("/")
                return datetime(int(year), int(month), int(day))
            except ValueError:
                pass
        return None

    def _extract_field(self, text: str, pattern: str) -> str:
        """Extrai campo via regex."""
        match = re.search(pattern, text)
        return match.group(1).strip() if match else None

    def _determine_status(self, texto: str, data_limite: datetime) -> tuple:
        """Determina status e flag de fluxo contínuo."""
        if "fluxo contínuo" in texto.lower():
            return "FLUXO_CONTINUO", True
        if data_limite:
            if data_limite < datetime.now():
                return "ENCERRADA", False
            return "ABERTA", False
        return "ABERTA", False


# Permite execução direta do arquivo
if __name__ == "__main__":
    scraper = FAPESPScraper()
    scraper.run()
