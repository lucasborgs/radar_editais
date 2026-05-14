"""
ProfileExtractor (Radar de Editais)

Dado o texto de um site corporativo, usa LLM para inferir campos do CompanyProfile.
Usado pelo endpoint POST /profile/extract (onboarding por URL).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

_EXTRACT_SYSTEM = """Você é um assistente que extrai informações de empresas a partir de textos de sites corporativos brasileiros.
Retorne APENAS JSON válido, sem explicações."""

_EXTRACT_USER = """Extraia as informações da empresa a partir do texto abaixo.
Use null para campos não encontrados. Seja conciso — máximo 2 frases por campo de texto.

Schema de saída:
{{
  "nome": string | null,
  "tipo_entidade": "empresa" | "startup" | "universidade" | "ICT" | null,
  "one_liner": string | null,
  "problem_statement": string | null,
  "solution_summary": string | null,
  "descricao_atividades": string | null,
  "tamanho_empresa": "MEI" | "ME" | "EPP" | "MEDIO" | "GRANDE" | null,
  "localizacao": string | null,
  "trl": int | null,
  "certificacoes": string[] | null
}}

Texto do site:
{text}"""

_REQUIRED_FIELDS = {"nome", "tipo_entidade", "one_liner", "descricao_atividades"}


@dataclass
class ExtractResult:
    profile: CompanyProfile
    confidence: dict[str, str]   # campo → "high" | "missing"
    source_title: str
    low_confidence: bool          # True se < 2 campos obrigatórios extraídos
    error: str | None = None


class ProfileExtractor:
    """Extrai CompanyProfile a partir da URL do site da empresa."""

    @staticmethod
    def _fetch_url(url: str, timeout: int = 12) -> tuple[str, str]:
        """Faz HTTP GET e extrai texto limpo do HTML."""
        headers = {"User-Agent": "Mozilla/5.0 (compatible; RadarEditais/1.0)"}
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        title = soup.title.string.strip() if soup.title else url
        return text[:12000], title

    def extract(self, url: str) -> ExtractResult:
        try:
            text, source_title = self._fetch_url(url)
        except Exception as e:
            logger.error("fetch falhou para %s: %s", url, e)
            return self._empty_result(error=f"Não foi possível acessar o site: {e}")

        if not text.strip():
            return self._empty_result(error="O site não retornou conteúdo legível.")

        llm_result = self._call_llm(text)
        if llm_result is None:
            return self._empty_result(error="llm_unavailable")

        profile, confidence = self._build_profile(llm_result)
        required_found = sum(
            1 for f in _REQUIRED_FIELDS if confidence.get(f) == "high"
        )
        return ExtractResult(
            profile=profile,
            confidence=confidence,
            source_title=source_title,
            low_confidence=required_found < 2,
        )

    # ------------------------------------------------------------------

    def _call_llm(self, text: str) -> dict | None:
        backend = os.getenv("LLM_BACKEND", "openai").lower()
        try:
            from openai import OpenAI
            if backend == "gemini":
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    return None
                client = OpenAI(
                    api_key=api_key,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                )
                model = "gemini-2.5-flash"
            else:
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    return None
                client = OpenAI(api_key=api_key)
                model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": _EXTRACT_USER.format(text=text)},
                ],
                temperature=0.1,
                max_tokens=800,
            )
            raw = response.choices[0].message.content.strip()
            if "```" in raw:
                raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`")
            return json.loads(raw)
        except Exception as e:
            logger.error("LLM extraction falhou: %s", e)
            return None

    def _build_profile(self, data: dict) -> tuple[CompanyProfile, dict[str, str]]:
        confidence: dict[str, str] = {}

        def get(field: str):
            val = data.get(field)
            confidence[field] = "high" if val not in (None, "", []) else "missing"
            return val

        profile = CompanyProfile(
            nome=get("nome") or "",
            tipo_entidade=get("tipo_entidade") or "",
            one_liner=get("one_liner") or "",
            problem_statement=get("problem_statement") or "",
            solution_summary=get("solution_summary") or "",
            descricao_atividades=get("descricao_atividades") or "",
            tamanho_empresa=get("tamanho_empresa") or "",
            localizacao=get("localizacao") or "",
            trl=get("trl"),
            certificacoes=get("certificacoes") or [],
        )
        return profile, confidence

    def _empty_result(self, error: str) -> ExtractResult:
        all_missing = {f: "missing" for f in _REQUIRED_FIELDS}
        return ExtractResult(
            profile=CompanyProfile(),
            confidence=all_missing,
            source_title="",
            low_confidence=True,
            error=error,
        )
