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
  "solution_summary": string | null,
  "descricao_atividades": string | null,
  "tamanho_empresa": "MEI" | "ME" | "EPP" | "MEDIO" | "GRANDE" | null,
  "trl": int | null,
  "uf": string | null,
  "ano_fundacao": int | null
}}

Texto do site:
{text}"""


# Sistema prompt do modo agente (Sprint 4 do Cenário B). Substitui o
# _EXTRACT_SYSTEM/_EXTRACT_USER quando agent_enabled=True. As ferramentas
# (fetch_page, list_links_matching, lookup_cnpj, submit_profile) são
# registradas via core.agent_tools.build_profile_tools.
EXTRACTOR_AGENT_SYSTEM = """Você é um analista que constrói o perfil técnico-comercial de empresas
brasileiras para uma plataforma de matching com editais de fomento.

Você recebeu a URL do site de uma empresa e precisa coletar dados
suficientes para preencher o perfil. Sua investigação termina com uma
única chamada de submit_profile.

DIRETRIZES DE INVESTIGAÇÃO
- Comece sempre buscando a home com fetch_page(url_inicial).
- Se a home não tem dados suficientes, use list_links_matching para
  descobrir páginas internas relevantes ('sobre', 'about', 'produtos',
  'tecnologia', 'clientes', 'contato', 'investidores').
- Abra no máximo 3-5 páginas internas. Limite total: 10 páginas.
- Se achar um CNPJ no rodapé ou em "contato", use lookup_cnpj para
  enriquecer com dados oficiais (razão social, porte, atividade).
- Nunca invente dados. Se não conseguir achar, use string vazia ou None.

CAMPOS DO PERFIL (submit_profile)
- nome: razão social ou nome fantasia (obrigatório)
- tipo_entidade: 'empresa', 'startup', 'universidade' ou 'ict' (obrigatório)
- one_liner: 1 frase sobre o que a empresa faz (obrigatório)
- descricao_atividades: 2-3 frases sobre produtos/serviços/área (obrigatório)
- solution_summary: 1 frase sobre solução principal (opcional)
- tamanho_empresa: porte BNDES (MEI/ME/EPP/MEDIO/GRANDE), opcional
- trl: 1-9 se for empresa de tecnologia com produto identificável; None se não
- uf: sigla de 2 letras do estado-sede (ex.: 'SP'), opcional
- ano_fundacao: ano de constituição (ex.: 2018), opcional
- faturamento_anual: receita bruta anual em R$, opcional

INFERINDO PORTE E TRL
- Porte: deduza de receita declarada, número de funcionários, ou
  classificação no CNPJ (se você consultou). Não chute por intuição.
- TRL: TRL ≤ 3 = pesquisa/conceito; TRL 4-6 = protótipo/piloto; TRL 7-9 =
  produto em produção. Só atribua se tiver evidência clara.

ELEGIBILIDADE ORGANIZACIONAL (uf / ano_fundacao / faturamento_anual)
- São os pares que os editais filtram (região, idade da empresa, porte/receita).
- Se você consultou o CNPJ com lookup_cnpj, derive 'uf' do campo "UF" e
  'ano_fundacao' do ano de "Início de atividade". Não invente sem evidência.

QUANDO PARAR
- Quando os 4 campos obrigatórios estão preenchidos com dados do site
  (ou da BrasilAPI). Chame submit_profile e termine.
- Se não conseguir achar dados mínimos em 5 páginas, submita com o que
  tiver (campos obrigatórios podem ser estimados conservadoramente) e
  o sistema vai marcar como low_confidence.

LIMITES
- Não tente acessar páginas que não vieram da empresa (sem fetch_page
  em LinkedIn, Glassdoor, Crunchbase — só o domínio da empresa e BrasilAPI).
- Não inclua opiniões ou avaliações qualitativas nos campos de texto.
- Responda ao usuário em PT-BR, conciso."""


# Anthropic Sonnet 4.6 (D1 híbrido). Configurável via env. Herda o default
# da WritingSession se ANTHROPIC_MODEL_AGENT_EXTRACTOR não estiver definido.
ANTHROPIC_MODEL_AGENT_EXTRACTOR = os.getenv(
    "ANTHROPIC_MODEL_AGENT_EXTRACTOR",
    os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6"),
)
# Investigação pode chegar a: fetch home + 2-3 list_links + 3-5 fetch_page +
# 1 lookup_cnpj + 1 submit_profile = ~10 steps. Margem em 12.
EXTRACTOR_AGENT_MAX_STEPS = int(os.getenv("EXTRACTOR_AGENT_MAX_STEPS", "12"))

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

    def extract(self, url: str, agent_enabled: bool = False) -> ExtractResult:
        """Dispatcher: agente (Sprint 4 do Cenário B) ou pipeline legacy.

        Quando `agent_enabled=True`, roda o agente Anthropic com 4 tools
        (fetch_page, list_links_matching, lookup_cnpj, submit_profile).
        Caso contrário, mantém o pipeline original (1 fetch da home + 1 LLM
        call). O caller (endpoint /profile/extract) decide o flag conforme
        env var AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED.
        """
        if agent_enabled:
            return self._extract_agent(url)
        return self._extract_legacy(url)

    def _extract_legacy(self, url: str) -> ExtractResult:
        """Pipeline original (pre-Sprint 4): 1 fetch da home + 1 LLM call."""
        try:
            text, source_title = self._fetch_url(url)
        except Exception as e:
            logger.error("fetch falhou para %s: %s", url, e)
            return self._empty_result(error=f"Não foi possível acessar o site: {e}")

        if not text.strip():
            return self._empty_result(error="O site não retornou conteúdo legível.")

        return self._result_from_text(text, source_title)

    def extract_from_text(self, text: str, source_title: str = "") -> ExtractResult:
        """Extrai CompanyProfile a partir de texto arbitrário (ex.: proposta antiga).

        Usado pelos endpoints /profile/extract-from-document e
        /profile/extract-from-library, que alimentam onboarding e enriquecimento
        da library. "AI drafts, human reviews": só RETORNA a sugestão de perfil
        + confiança, nunca salva. Sem fetch HTTP — o texto já vem pronto (PDF
        extraído ou content de um library_item).
        """
        if not text or not text.strip():
            return self._empty_result(error="Texto vazio ou ilegível.")
        return self._result_from_text(text, source_title)

    def _result_from_text(self, text: str, source_title: str) -> ExtractResult:
        """Núcleo compartilhado: _call_llm → _build_profile → low_confidence.

        Reaproveitado por _extract_legacy (após fetch da URL) e por
        extract_from_text (texto já disponível). Mantém a mesma regra de
        low_confidence (< 2 campos obrigatórios extraídos).
        """
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

    def _extract_agent(self, url: str) -> ExtractResult:
        """Pipeline agente (Sprint 4 do Cenário B): run_agent + 4 tools.

        Diferenças vs legacy:
          • Sem fetch eager — o agente decide quais páginas abrir via
            fetch_page e list_links_matching.
          • Pode enriquecer com BrasilAPI via lookup_cnpj.
          • Output via tool submit_profile (state.submitted_profile) em vez
            de JSON parseado do texto final.
        """
        from core.agent_runtime import resolve_agent_provider, run_agent
        from core.agent_tools import ExtractionState, build_profile_tools

        state = ExtractionState()
        tools = build_profile_tools(state)

        initial = [
            {
                "role": "user",
                "content": (
                    f"Extraia o perfil da empresa cujo site é: {url}\n\n"
                    "Comece buscando essa URL com fetch_page."
                ),
            },
        ]

        provider, model = resolve_agent_provider(
            "anthropic", ANTHROPIC_MODEL_AGENT_EXTRACTOR,
        )
        try:
            result = run_agent(
                system=EXTRACTOR_AGENT_SYSTEM,
                initial_messages=initial,
                tools=tools,
                model=model,
                provider=provider,
                max_steps=EXTRACTOR_AGENT_MAX_STEPS,
            )
        except Exception as e:
            logger.error("extract_agent: run_agent levantou: %s", e)
            return self._empty_result(error=f"agent_failure: {e}")

        if state.submitted_profile is None:
            # Agente terminou (max_steps, error, ou texto sem chamar submit)
            # — sem profile estruturado. Marcamos como low_confidence + erro
            # descritivo. O título da home (se foi buscada) ainda serve para
            # contexto do operador.
            source_title = state.fetched.get(url, {}).get("title", url)
            empty = self._empty_result(error="agent_no_submit")
            return ExtractResult(
                profile=empty.profile,
                confidence=empty.confidence,
                source_title=source_title,
                low_confidence=True,
                error=f"Agente terminou sem submeter perfil (stop_reason={result.stop_reason}).",
            )

        profile, confidence = self._build_profile(state.submitted_profile)
        source_title = state.fetched.get(url, {}).get("title", url)
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
            from core.llm_client import make_client
            if backend == "gemini":
                api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    return None
                client = make_client(
                    api_key=api_key,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                )
                model = "gemini-2.5-flash"
            else:
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    return None
                client = make_client(api_key=api_key)
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
            solution_summary=get("solution_summary") or "",
            descricao_atividades=get("descricao_atividades") or "",
            tamanho_empresa=get("tamanho_empresa") or "",
            trl=get("trl"),
            uf=(get("uf") or "").strip().upper(),
            ano_fundacao=get("ano_fundacao"),
            faturamento_anual=get("faturamento_anual"),
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
