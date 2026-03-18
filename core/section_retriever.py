"""
Section Retriever (Radar de Editais)

Carrega e serve seções estruturais de editais a partir do section_index
gerado por pipeline/etl_section_index.py.

Usado pela feature Writing para:
  - Listar títulos disponíveis (input do Router LLM)
  - Recuperar conteúdo de seções selecionadas (input do Writer LLM)
"""

import json
import logging
from pathlib import Path
from typing import Optional

from config import SECTION_INDEX_DIR

logger = logging.getLogger(__name__)


class SectionRetriever:
    """Acessa o section index de um edital por ID."""

    def __init__(self, index_dir: Optional[Path] = None):
        self._dir = index_dir or SECTION_INDEX_DIR

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def exists(self, edital_id: str) -> bool:
        """Verifica se o section index existe para este edital."""
        return self._path(edital_id).exists()

    def list_sections(self, edital_id: str) -> list[str]:
        """
        Retorna os títulos das seções disponíveis para o edital.
        Usado pelo Router LLM como input leve (apenas títulos, sem conteúdo).

        Returns:
            Lista de títulos, ex: ["Objeto", "Elegibilidade", "Cronograma"]
            Retorna [] se o section index não existir.
        """
        payload = self._load(edital_id)
        if payload is None:
            return []
        return [sec["title"] for sec in payload.get("sections", [])]

    def get_sections(self, edital_id: str, titles: list[str]) -> str:
        """
        Retorna o conteúdo concatenado das seções cujos títulos estão em `titles`.
        Matching case-insensitive e por substring.

        Args:
            edital_id: ID do edital
            titles: Lista de títulos selecionados pelo Router LLM

        Returns:
            Texto concatenado das seções selecionadas.
            Retorna o texto completo se `titles` estiver vazio.
        """
        payload = self._load(edital_id)
        if payload is None:
            return ""

        sections = payload.get("sections", [])

        if not titles:
            return self._join(sections)

        titles_lower = [t.lower() for t in titles]
        selected = [
            sec for sec in sections
            if any(tl in sec["title"].lower() or sec["title"].lower() in tl
                   for tl in titles_lower)
        ]

        # Fallback: se nenhuma seção bateu, retorna todas
        if not selected:
            logger.warning(
                f"Nenhuma seção encontrada para {titles} em {edital_id} — retornando todas"
            )
            return self._join(sections)

        return self._join(selected)

    def get_all_text(self, edital_id: str) -> str:
        """
        Retorna o texto completo do edital (todas as seções concatenadas).
        Fallback quando não há section index.
        """
        payload = self._load(edital_id)
        if payload is None:
            return ""
        return self._join(payload.get("sections", []))

    def get_metadata(self, edital_id: str) -> dict:
        """Retorna metadados do section index (source, title, section count)."""
        payload = self._load(edital_id)
        if payload is None:
            return {}
        return {
            "edital_id": edital_id,
            "source": payload.get("source", ""),
            "title": payload.get("title", ""),
            "section_count": len(payload.get("sections", [])),
            "section_titles": [s["title"] for s in payload.get("sections", [])],
        }

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _path(self, edital_id: str) -> Path:
        return self._dir / f"{edital_id}.json"

    def _load(self, edital_id: str) -> Optional[dict]:
        path = self._path(edital_id)
        if not path.exists():
            logger.warning(f"Section index não encontrado: {path}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Erro ao ler section index {path}: {e}")
            return None

    def _join(self, sections: list[dict]) -> str:
        parts = []
        for sec in sections:
            title = sec.get("title", "")
            content = sec.get("content", "")
            if content:
                parts.append(f"## {title}\n{content}")
        return "\n\n".join(parts)
