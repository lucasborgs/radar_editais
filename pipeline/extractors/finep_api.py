"""
Cliente da API FINEP (Liferay Headless).

O site FINEP migrou de HTML server-rendered para SPA Liferay. Não é mais
possível scrapar via parse de HTML — a listagem é carregada via JS chamando
endpoints REST autenticados com OAuth2 (`/o/c/chamadapublicas`).

As credenciais `idClientPRD`/`secretClientPRD` estão hardcoded no JavaScript
público do próprio site (não são segredos — são tokens de leitura pública
distribuídos para o frontend SPA acessar a API headless).

Schema relevante de `chamadapublica` (campos usados pelo Radar):
  - id / databaseId   — IDs Liferay e legacy (usar databaseId como chamada_id)
  - titulo            — título oficial
  - descricao         — HTML rico
  - descricaoRawText  — texto puro (preferido)
  - dataDePublicacao  — ISO 8601
  - vigenciaInicio    — abertura da chamada
  - vigenciaFim       — encerramento
  - situacao          — {key: 'aberta'|'encerrada', name}
  - publicoAlvo       — list[{key, name}]  (ICT, empresa, etc.)
  - temaPrincipal, tema, regiao, tipoCooperacao, contrapartida, tipoDeOportunidade
  - taxonomyCategoryBriefs — categorias Liferay
  - documentos        — quando nestedFields=documentos: list[doc]
      .legenda                       (ex: "Regulamento", "Anexo I")
      .documentoProprietario.link.href  (path PDF — concatena com BASE_URL)
      .documentoAberto.link.href     (path ODT — versão editável)
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.finep.gov.br"
TOKEN_URL = f"{BASE_URL}/o/oauth2/token"
CHAMADAS_URL = f"{BASE_URL}/o/c/chamadapublicas"

# Credenciais públicas embutidas no JS do próprio site FINEP — não são secret.
# Se mudarem, basta inspecionar window.Finep ou o HTML dos templates.
PUBLIC_CLIENT_ID = "idClientPRD"
PUBLIC_CLIENT_SECRET = "secretClientPRD"

# Margem de segurança: invalida o token 30s antes do `expires_in` informado.
_TOKEN_SAFETY_MARGIN = 30


class FinepAPIError(Exception):
    """Falha na comunicação com a API FINEP (HTTP 4xx/5xx ou JSON inválido)."""


class FinepAPI:
    """Cliente Liferay Headless do FINEP com token-cache.

    Uso típico:
        api = FinepAPI()
        for chamada in api.list_chamadas(situacao="aberta"):
            print(chamada["titulo"])
    """

    def __init__(self, session: requests.Session | None = None, timeout: int = 30):
        self._session = session or requests.Session()
        self._timeout = timeout
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _get_token(self) -> str:
        """Retorna access_token válido. Reusa se ainda na janela; refresh se perto de expirar."""
        now = time.time()
        if self._token and now < self._token_expires_at - _TOKEN_SAFETY_MARGIN:
            return self._token

        auth = base64.b64encode(
            f"{PUBLIC_CLIENT_ID}:{PUBLIC_CLIENT_SECRET}".encode()
        ).decode()
        try:
            resp = self._session.post(
                TOKEN_URL,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data="grant_type=client_credentials",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            raise FinepAPIError(f"Falha ao obter token: {e}") from e

        self._token = data["access_token"]
        self._token_expires_at = now + int(data.get("expires_in", 600))
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_chamadas(
        self,
        situacao: str | None = None,
        page_size: int = 50,
        with_documentos: bool = True,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista chamadas FINEP, paginando até esgotar (ou max_pages).

        Args:
            situacao: filtro por status — 'aberta', 'encerrada' ou None (tudo).
            page_size: itens por página (1-100, default 50).
            with_documentos: se True, inclui nestedFields=documentos no expand.
            max_pages: limite de páginas (None = todas).

        Returns:
            Lista crua de dicts conforme schema c_chamadapublica.
            Cada item já vem com documentos[] populado se `with_documentos=True`.

        Raises:
            FinepAPIError em caso de HTTP error, timeout ou JSON inválido.
        """
        params: dict[str, Any] = {"pageSize": page_size, "page": 1}
        if situacao:
            params["filter"] = f"situacao eq '{situacao}'"
        if with_documentos:
            params["nestedFields"] = "documentos"

        all_items: list[dict] = []
        page = 1
        while True:
            params["page"] = page
            data = self._get(CHAMADAS_URL, params=params)
            items = data.get("items", [])
            all_items.extend(items)

            last_page = int(data.get("lastPage") or 1)
            total = int(data.get("totalCount") or 0)
            logger.info(
                "FinepAPI: page %d/%d (acumulado %d/%d)",
                page, last_page, len(all_items), total,
            )

            if page >= last_page:
                break
            if max_pages and page >= max_pages:
                break
            page += 1

        return all_items

    def _get(self, url: str, params: dict | None = None) -> dict:
        try:
            resp = self._session.get(
                url, headers=self._auth_headers(), params=params, timeout=self._timeout,
            )
            resp.raise_for_status()
            # Liferay às vezes retorna JSON com control chars dentro de descricao
            # (HTML rico com \r\n não escapados). strict=False tolera isso.
            import json
            return json.loads(resp.text, strict=False)
        except (requests.RequestException, ValueError) as e:
            raise FinepAPIError(f"GET {url} falhou: {e}") from e

    # ------------------------------------------------------------------
    # Documentos
    # ------------------------------------------------------------------

    @staticmethod
    def extract_pdf_urls(chamada: dict, prefer_pdf: bool = True) -> list[tuple[str, str]]:
        """Extrai URLs absolutas dos PDFs de uma chamada (com legenda).

        Args:
            chamada: dict cru retornado pela API (com documentos[] populado).
            prefer_pdf: se True, prioriza `documentoProprietario` (PDF) sobre
                        `documentoAberto` (geralmente ODT/DOCX editável).

        Returns:
            Lista de tuplas (legenda, url_absoluta). Lista vazia se sem docs.
        """
        out: list[tuple[str, str]] = []
        for doc in chamada.get("documentos", []) or []:
            legenda = doc.get("legenda") or "documento"
            picks = (
                [doc.get("documentoProprietario"), doc.get("documentoAberto")]
                if prefer_pdf
                else [doc.get("documentoAberto"), doc.get("documentoProprietario")]
            )
            for d in picks:
                if not d:
                    continue
                href = ((d.get("link") or {}).get("href")) or ""
                if not href:
                    continue
                # Liferay às vezes retorna paths já absolutos; senão prefixa BASE_URL
                url = href if href.startswith("http") else f"{BASE_URL}{href}"
                out.append((legenda, url))
                break  # uma URL por documento basta
        return out

    def download_pdf(self, url: str, dest_path: str) -> bool:
        """Baixa o PDF para `dest_path`. Retorna True se ok.

        Não levanta em caso de falha — apenas loga e retorna False, para que
        o pipeline siga com os demais PDFs.
        """
        try:
            # Documentos do Liferay também ficam atrás de auth — passa o Bearer.
            resp = self._session.get(
                url,
                headers={"Authorization": f"Bearer {self._get_token()}"},
                timeout=self._timeout,
                stream=True,
            )
            resp.raise_for_status()
            # Limita tamanho para evitar baixar arquivos enormes acidentalmente
            max_bytes = 25 * 1024 * 1024  # 25 MB
            total = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        logger.warning("PDF excede %d MB, truncando: %s", max_bytes // 1024 // 1024, url)
                        break
            return True
        except requests.RequestException as e:
            logger.warning("Falha ao baixar PDF %s: %s", url, e)
            return False


# Helper público — útil para construir URLs de scraping/debug fora da classe
def chamada_url_publica(database_id: int) -> str:
    """URL pública da chamada no novo site FINEP (para humanos no browser)."""
    return f"{BASE_URL}/oportunidades/chamadas-publicas?chamadaId={database_id}"


__all__ = ["FinepAPI", "FinepAPIError", "chamada_url_publica", "BASE_URL"]
