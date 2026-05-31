"""
Scraper para chamadas públicas da FINEP — versão API.

Em 2026 o site da FINEP migrou para Liferay SPA. O scraping HTML deixou de
funcionar (listagem carregada via JS, redirects do site legacy para o novo).
Esta nova implementação usa a **API Liferay Headless** que o próprio frontend
do site usa internamente — ver `pipeline.extractors.finep_api` para detalhes
do protocolo (OAuth2 público + endpoint REST estruturado).

Vantagens vs. scraping anterior:
  • Dados estruturados (situacao, publicoAlvo, datas, tema) em vez de HTML
  • Filtro server-side (`situacao=aberta|encerrada`) — sem paginação cega
  • Documentos retornados embutidos com URLs absolutas — sem detecção HTML
  • Sem dependência de seletores CSS frágeis

Compatibilidade: mantém o mesmo shape de output do scraper anterior para que
`pipeline/etl_finep_cards.py`, `etl_finep_facts.py` e `build_knowledge_graph.py`
não precisem mudar.

Modos:
    extract(include_historical=False)  — só `situacao=aberta` (default)
    extract(include_historical=True)   — aberta + encerrada (todas as páginas)
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from config import FINEP_PDFS_DIR
from core.wiki_schema import iso_to_br_date

from .base import BaseScraper
from .finep_api import FinepAPI, FinepAPIError, chamada_url_publica

logger = logging.getLogger(__name__)


class FINEPScraper(BaseScraper):
    """Scraper FINEP usando a API Liferay Headless do site novo (2026+)."""

    PAGE_SIZE = 50            # Liferay default cap = 100
    DOWNLOAD_THROTTLE = 0.5   # segundos entre downloads de PDFs (ser educado)

    def __init__(self):
        super().__init__(source_name="FINEP", output_subdir="finep_raw")
        self._api = FinepAPI()
        FINEP_PDFS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def extract(self, include_historical: bool = False, max_pages: int | None = None) -> list:
        """Extrai chamadas FINEP.

        Args:
            include_historical: se True, também busca `situacao=encerrada`.
            max_pages: limite de páginas por situacao (None = todas).

        Returns:
            Lista de dicts compatível com o shape do scraper anterior:
              {
                fonte, chamada_id, titulo, link, status,
                data_publicacao, prazo_envio, fonte_recurso,
                publico_alvo, tema, descricao, raw_html,
                pdf_urls, pdf_texts, data_extracao
              }
        """
        all_results: list[dict] = []

        situacoes = ["aberta", "encerrada"] if include_historical else ["aberta"]
        for situacao in situacoes:
            try:
                print(f"\n>>> FINEP: listando chamadas situacao={situacao}...")
                raw = self._api.list_chamadas(
                    situacao=situacao,
                    page_size=self.PAGE_SIZE,
                    with_documentos=True,
                    max_pages=max_pages,
                )
                print(f"    → {len(raw)} chamadas recebidas via API")
            except FinepAPIError as e:
                logger.error("Falha ao listar chamadas (%s): %s", situacao, e)
                continue

            for chamada in raw:
                try:
                    normalized = self._normalize(chamada)
                    all_results.append(normalized)
                except Exception as e:
                    logger.warning(
                        "Falha ao normalizar chamada databaseId=%s: %s",
                        chamada.get("databaseId"), e,
                    )

        # Enriquece com download de PDFs (separado para deixar normalização rápida)
        print(f"\n>>> FINEP: baixando PDFs para {len(all_results)} chamadas...")
        for i, item in enumerate(all_results, 1):
            n_pdfs = len(item["pdf_urls"])
            if n_pdfs == 0:
                continue
            print(f"  [{i}/{len(all_results)}] {item['titulo'][:60]} — {n_pdfs} PDFs")
            self._download_pdfs(item)
            time.sleep(self.DOWNLOAD_THROTTLE)

        if all_results:
            prefix = "finep_historical" if include_historical else "finep_chamadas"
            self._save(all_results, prefix=prefix)

        return all_results

    # ------------------------------------------------------------------
    # Normalização: API schema → schema legado do pipeline
    # ------------------------------------------------------------------

    def _normalize(self, raw: dict) -> dict:
        """Converte um item cru da API Liferay para o shape legacy do pipeline.

        Mapeamento de campos:
          API.databaseId            → chamada_id
          API.titulo                → titulo
          chamada_url_publica()     → link (URL do humano no browser)
          API.situacao.name         → status (Aberta/Encerrada)
          API.dataDePublicacao      → data_publicacao
          API.vigenciaFim           → prazo_envio
          API.publicoAlvo[]         → publico_alvo (string concat)
          API.tema                  → tema
          API.temaPrincipal.name    → tema (fallback)
          API.descricaoRawText      → descricao
          API.descricao             → raw_html
          API.documentos[].link     → pdf_urls (lista de URLs absolutas)
        """
        chamada_id = str(raw.get("databaseId") or "")
        liferay_id = raw.get("id")
        # Normaliza status para caixa alta (esquema do build_knowledge_graph + wiki_schema)
        situacao_raw = (raw.get("situacao") or {}).get("name") or "Desconhecido"
        status_map = {
            "Aberta": "ABERTA",
            "Encerrada": "ENCERRADA",
            "Em análise": "EM_ANALISE",
            "Resultado divulgado": "RESULTADO_DIVULGADO",
        }
        situacao = status_map.get(situacao_raw, situacao_raw.upper().replace(" ", "_"))
        publico = ", ".join(p.get("name", "") for p in (raw.get("publicoAlvo") or []) if p)
        tema = raw.get("tema") or (raw.get("temaPrincipal") or {}).get("name") or ""

        pdf_pairs = self._api.extract_pdf_urls(raw, prefer_pdf=True)
        pdf_urls = [url for _legenda, url in pdf_pairs]

        return {
            "fonte": self.source_name,
            "chamada_id": chamada_id,
            "titulo": raw.get("titulo") or "",
            "link": chamada_url_publica(liferay_id) if liferay_id else "",
            "status": situacao,
            # Liferay entrega ISO; schema exige dd/mm/yyyy (WIKI.md §4.1).
            "data_publicacao": iso_to_br_date(raw.get("dataDePublicacao")) or None,
            # prazoProposto é o prazo real de submissão; vigenciaFim é sempre null na API.
            "prazo_envio": iso_to_br_date(raw.get("prazoProposto")) or None,
            "fonte_recurso": "",                       # campo `fonteDeRecurso` exige outro endpoint
            "publico_alvo": publico,
            "tema": tema,
            "descricao": raw.get("descricaoRawText") or "",
            "raw_html": raw.get("descricao") or "",
            "pdf_urls": pdf_urls,
            "pdf_legendas": [legenda for legenda, _url in pdf_pairs],
            "pdf_texts": {},                           # preenchido em _download_pdfs
            "data_extracao": self._get_timestamp(),
            # Campos extras vindos da API — úteis para o pipeline pular re-extração LLM:
            "api_regiao": (raw.get("regiao") or {}).get("name") or "",
            "api_tipo_oportunidade": (raw.get("tipoDeOportunidade") or {}).get("name") or "",
            "api_tipo_cooperacao": (raw.get("tipoCooperacao") or {}).get("name") or "",
            "api_contrapartida": (raw.get("contrapartida") or {}).get("name") or "",
            "api_vigencia_inicio": (raw.get("vigenciaInicio") or "")[:10] or None,
            "api_publico_alvo_keys": [p.get("key") for p in (raw.get("publicoAlvo") or []) if p],
            "api_taxonomy_categories": [
                t.get("taxonomyCategoryName") for t in (raw.get("taxonomyCategoryBriefs") or [])
            ],
        }

    # ------------------------------------------------------------------
    # Download de PDFs
    # ------------------------------------------------------------------

    def _download_pdfs(self, item: dict) -> None:
        """Baixa os PDFs da chamada para `bronze_data/finep_pdfs/<chamada_id>/`."""
        chamada_id = item.get("chamada_id") or ""
        if not chamada_id:
            return
        dest_dir = FINEP_PDFS_DIR / chamada_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        pdf_texts: dict[str, str] = {}
        legendas = item.get("pdf_legendas") or []
        for i, url in enumerate(item["pdf_urls"]):
            # Nome local: usa a legenda quando disponível, senão indexa por número
            legenda = legendas[i] if i < len(legendas) else f"doc{i+1}"
            ext = self._extension_from_url(url)
            safe_name = self._safe_filename(legenda) + ext
            dest = dest_dir / safe_name

            # Baixa SEMPRE para um arquivo temporário e só substitui o local se o
            # conteúdo mudou (hash difere). Antes pulávamos qualquer arquivo que
            # já existisse, o que fazia rerratificações com o mesmo nome de
            # arquivo nunca serem captadas (requisito 3). Como só reescrevemos
            # quando muda, o mtime de arquivos inalterados é preservado — e o
            # gate de reprocessamento (chunk/embed) usa o conteúdo como sinal.
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            ok = self._api.download_pdf(url, str(tmp))
            if not ok:
                tmp.unlink(missing_ok=True)
                continue
            try:
                new_digest = hashlib.md5(tmp.read_bytes()).hexdigest()
                unchanged = (
                    dest.exists()
                    and dest.stat().st_size > 0
                    and hashlib.md5(dest.read_bytes()).hexdigest() == new_digest
                )
                if unchanged:
                    logger.debug("PDF inalterado, mantendo: %s", dest.name)
                    tmp.unlink(missing_ok=True)
                else:
                    os.replace(tmp, dest)  # novo ou alterado — troca atômica
                    logger.info("PDF baixado/atualizado: %s/%s", chamada_id, dest.name)
            except Exception as e:
                logger.warning("Falha ao comparar/gravar PDF %s: %s", dest.name, e)
                tmp.unlink(missing_ok=True)
                continue

            # Best-effort: extrai texto para o dict de saída (compatível com pipeline antigo)
            text = self._extract_pdf_text(dest)
            if text:
                pdf_texts[safe_name] = text

        item["pdf_texts"] = pdf_texts

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Sanitiza string para nome de arquivo: alfanuméricos + _ + -."""
        keep = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in name)
        return "_".join(keep.split())[:80] or "documento"

    @staticmethod
    def _extension_from_url(url: str) -> str:
        """Extrai extensão de URLs FINEP no formato `.../filename.pdf/<uuid>?query`.

        O Liferay coloca o nome de arquivo + extensão no penúltimo segmento
        do path (o último segmento é o UUID do asset). `Path(url).suffix` pega
        o sufixo errado (vazio para UUIDs). Esta função descobre a extensão
        olhando os 2 últimos segmentos.

        Returns: extensão com ponto (ex: '.pdf'), ou '' se não encontrar.
        """
        from urllib.parse import urlparse
        path = urlparse(url).path
        segments = [s for s in path.split("/") if s]
        for seg in reversed(segments):
            suffix = Path(seg).suffix.lower()
            if suffix in (".pdf", ".odt", ".doc", ".docx", ".xls", ".xlsx", ".zip"):
                return suffix
        return ".pdf"  # fallback razoável — quase tudo do FINEP é PDF

    @staticmethod
    def _extract_pdf_text(pdf_path: Path) -> str:
        """Extrai texto do PDF com pdfplumber (melhor que pypdf para layouts complexos)."""
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except ImportError:
            logger.warning("pdfplumber não instalado — texto de PDF não extraído")
            return ""
        except Exception as e:
            logger.warning("Falha ao extrair texto de %s: %s", pdf_path.name, e)
            return ""


# Permite execução direta para teste manual
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = FINEPScraper()
    results = scraper.extract(include_historical=False)
    print(f"\n=== Extraídas {len(results)} chamadas ===")
    for r in results[:3]:
        print(f"  {r['chamada_id']} | {r['titulo'][:60]} | {len(r['pdf_urls'])} PDFs")
