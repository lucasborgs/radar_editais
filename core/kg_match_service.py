"""
KGMatchService — Matching Karpathy-style.

A LLM lê o índice completo de editais FINEP (knowledge_graph/index.json)
junto com o perfil da empresa e retorna os editais mais relevantes com
justificativa por dimensão. Para o top-3, enriquece com dados do card
(knowledge_graph/cards/{id}.json) quando disponível.

Sem embeddings, sem ChromaDB — apenas raciocínio LLM sobre índice estruturado.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from config import KNOWLEDGE_GRAPH_DIR, KG_WIKI_DIR
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

# =============================================================================
# PROMPT DE MATCHING
# =============================================================================

MATCH_SYSTEM_PROMPT = """Você é um especialista em fomento à inovação no Brasil com profundo
conhecimento das chamadas públicas FINEP, FNDCT e programas de CT&I.

Sua tarefa é analisar o perfil de uma empresa e identificar os editais FINEP mais relevantes
para ela a partir de um catálogo estruturado.

Critérios de avaliação (use todos):
- Alinhamento temático: a área de atuação da empresa coincide com os temas do edital?
- Público-alvo: o tipo/porte da empresa está entre os elegíveis?
- Mecanismo financeiro: o instrumento (subvenção/reembolsável) é compatível com a preferência?
- Maturidade tecnológica (TRL): o TRL atual do projeto está dentro do range aceito?
- Situação: editais ABERTA têm prioridade, mas editais encerrados podem indicar padrões futuros
- Fonte de recurso: alinhamento com os programas de fomento do setor da empresa

Responda APENAS com JSON válido. Sem markdown, sem texto fora do JSON."""

MATCH_USER_PROMPT = """PERFIL DA EMPRESA:
{profile_context}

CATÁLOGO DE EDITAIS FINEP:
{index_json}

Retorne os {top_k} editais mais relevantes para esta empresa no formato JSON abaixo.
score deve ser de 0.0 a 10.0. match_dimensions deve ter no máximo 4 dimensões relevantes.

{{
  "matches": [
    {{
      "id": "id_do_edital",
      "title": "título do edital",
      "score": 8.5,
      "status": "ABERTA|ENCERRADA|Desconhecido",
      "deadline": "DD/MM/YYYY ou vazio",
      "match_dimensions": {{
        "tematico": "explicação em 1 frase",
        "publico_alvo": "explicação em 1 frase",
        "mecanismo": "explicação em 1 frase",
        "trl": "explicação em 1 frase ou null"
      }},
      "justificativa": "parágrafo curto explicando por que este edital é relevante para a empresa"
    }}
  ]
}}"""


# =============================================================================
# CLIENTE LLM
# =============================================================================

def _make_client():
    """Cria cliente LLM baseado em variáveis de ambiente."""
    backend = os.getenv("LLM_BACKEND", "openai").lower()

    if backend == "gemini":
        from openai import OpenAI
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"

    elif backend == "openai":
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY não definida")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        return OpenAI(api_key=api_key), model

    elif backend == "ollama":
        from openai import OpenAI
        model = os.getenv("OLLAMA_MODEL", "llama3.2")
        return OpenAI(
            api_key="ollama",
            base_url="http://localhost:11434/v1",
        ), model

    else:
        raise ValueError(f"LLM_BACKEND desconhecido: {backend}")


# =============================================================================
# SERVIÇO
# =============================================================================

class KGMatchService:
    """Matching de empresa↔editais via LLM sobre o índice FINEP."""

    INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"

    def __init__(self):
        self._index: dict = {}
        self._index_mtime: float = 0.0
        self._client = None
        self._model = ""
        self._load_index()

    # ------------------------------------------------------------------
    # Índice
    # ------------------------------------------------------------------

    def _load_index(self) -> None:
        if not self.INDEX_FILE.exists():
            logger.warning("index.json não encontrado: %s", self.INDEX_FILE)
            self._index = {"editais": []}
            return

        mtime = self.INDEX_FILE.stat().st_mtime
        if mtime != self._index_mtime:
            self._index = json.loads(self.INDEX_FILE.read_text(encoding="utf-8"))
            self._index_mtime = mtime
            logger.info("Índice carregado: %d editais", len(self._index.get("editais", [])))

    def _get_index_for_prompt(self) -> str:
        """Formata o índice de forma compacta para o prompt (~150 chars por edital)."""
        self._load_index()
        lines = []
        for e in self._index.get("editais", []):
            themes = ", ".join(e.get("themes", []))[:80]
            publico = ", ".join(e.get("publico_alvo", []))
            fonte = ", ".join(e.get("fonte_recurso", []))[:50]
            lines.append(
                f'ID:{e["id"]} | {e["title"][:70]} | Status:{e["status"]} | '
                f'Prazo:{e.get("deadline","?")} | Temas:{themes} | '
                f'Público:{publico} | Fonte:{fonte}'
            )
        return "\n".join(lines)

    def get_stats(self) -> dict:
        self._load_index()
        summary = self._index.get("summary", {})
        return {
            "total_editais": self._index.get("total_editais", 0),
            "last_updated": self._index.get("last_updated", ""),
            "by_status": summary.get("by_status", {}),
            "n_themes": summary.get("n_themes", 0),
            "n_fontes": summary.get("n_fontes", 0),
        }

    def list_editais(
        self,
        status: str | None = None,
        tema: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        self._load_index()
        editais = self._index.get("editais", [])

        if status:
            editais = [e for e in editais if e.get("status", "").upper() == status.upper()]
        if tema:
            tema_lower = tema.lower()
            editais = [
                e for e in editais
                if any(tema_lower in t.lower() for t in e.get("themes", []))
            ]
        return editais[:limit]

    def get_edital_by_id(self, edital_id: str) -> dict | None:
        """Retorna card rico se disponível, senão entry do índice."""
        card_file = KG_WIKI_DIR / f"{edital_id}.json"
        if card_file.exists():
            try:
                return json.loads(card_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        self._load_index()
        for e in self._index.get("editais", []):
            if e["id"] == edital_id:
                return e
        return None

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def _ensure_client(self) -> None:
        if self._client is None:
            self._client, self._model = _make_client()

    def match(
        self,
        profile: CompanyProfile,
        top_k: int = 10,
    ) -> list[dict]:
        """Retorna top_k editais mais relevantes para o perfil da empresa.

        Fluxo:
        1. LLM lê índice completo + perfil → lista rankeada
        2. Para o top-3 com card disponível → enriquece com key_requirements
        """
        self._ensure_client()

        index_str = self._get_index_for_prompt()
        profile_str = profile.to_context()

        prompt = MATCH_USER_PROMPT.format(
            profile_context=profile_str,
            index_json=index_str,
            top_k=top_k,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": MATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=3000,
            )
            raw = response.choices[0].message.content.strip()
        except Exception as e:
            logger.error("Erro LLM no matching: %s", e)
            return []

        matches = self._parse_matches(raw)

        # Enriquece top-3 com dados do card
        for match in matches[:3]:
            card = self.get_edital_by_id(match["id"])
            if card and card.get("key_requirements"):
                match["key_requirements"] = card["key_requirements"]
            if card and card.get("objective"):
                match["objective"] = card["objective"]

        return matches

    def _parse_matches(self, raw: str) -> list[dict]:
        """Extrai lista de matches do JSON retornado pela LLM."""
        # Remove possível markdown
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    raw = part
                    break

        try:
            data = json.loads(raw)
            matches = data.get("matches", [])
            if isinstance(matches, list):
                return matches
        except json.JSONDecodeError:
            # Tenta extrair JSON com regex
            m = re.search(r'"matches"\s*:\s*(\[.*?\])', raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass

        logger.warning("Não foi possível parsear resposta do matching: %s", raw[:300])
        return []
