"""EntityMatcher — matcher Karpathy-style genérico para entidades (investidores, programas).

Unifica os antigos investor_match.py e programa_match.py num único motor
parametrizável via EntityCatalog. Uso:

    from core.services.entity_matcher import EntityMatcher, catalog_investidores
    matches = EntityMatcher(catalog_investidores).match(profile, top_k=5)
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

from core.kg import kg_store
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)

# =============================================================================
# Utilities compartilhadas (make_client, parse)
# =============================================================================


def _make_client():
    """(client, model) conforme LLM_BACKEND. Levanta se sem credencial — o caller
    captura e degrada. Mesmo padrão usado por investor_match e programa_match
    (migrado para cá)."""
    from core.llm.llm_client import make_client

    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return make_client(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.5-flash"
    if backend == "ollama":
        return make_client(
            api_key="ollama", base_url="http://localhost:11434/v1"
        ), os.getenv("OLLAMA_MODEL", "llama3.2")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return make_client(api_key=api_key), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _parse(raw: str) -> list[dict]:
    """Extrai matches[] do JSON do LLM (tolera cercas markdown)."""
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip()
    try:
        data = json.loads(raw)
        m = data.get("matches", [])
        return m if isinstance(m, list) else []
    except json.JSONDecodeError:
        mm = re.search(r'"matches"\s*:\s*(\[.*\])', raw, re.DOTALL)
        if mm:
            try:
                return json.loads(mm.group(1))
            except Exception:
                pass
    logger.warning("entity_matcher: não parseou a resposta: %s", raw[:200])
    return []


# =============================================================================
# EntityCatalog + EntityMatcher
# =============================================================================


@dataclass
class EntityCatalog:
    loader: Callable[[], list[dict]]
    system_prompt: str
    user_prompt_template: str
    format_item: Callable[[dict], str]
    enrich: Callable[[dict, dict], dict]


class EntityMatcher:
    """Match genérico para entidades (investidores, programas, …).
    Karpathy-style: catálogo inteiro no prompt, 1 LLM call.
    """

    def __init__(self, catalog: EntityCatalog):
        self._catalog = catalog

    def match(self, profile: CompanyProfile, top_k: int = 5) -> list[dict]:
        """Retorna os top_k itens mais aderentes ao perfil. [] sem diretório,
        sem credencial LLM ou em falha (nunca levanta). Enriquece cada match
        com campos de display via self._catalog.enrich."""
        items = self._catalog.loader()
        if not items:
            return []
        try:
            client, model = _make_client()
        except Exception as e:
            logger.warning("entity_matcher: sem LLM (%s) — degradando para []", e)
            return []

        formatted = "\n".join(self._catalog.format_item(i) for i in items)
        prompt = self._catalog.user_prompt_template.format(
            profile=profile.to_context(),
            catalog=formatted,
            top_k=top_k,
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": self._catalog.system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            matches = _parse(resp.choices[0].message.content.strip())
        except Exception as e:
            logger.error("entity_matcher: erro LLM: %s", e)
            return []

        by_id = {i["id"]: i for i in items}
        for m in matches:
            raw = by_id.get(m.get("id"))
            if raw:
                self._catalog.enrich(m, raw)
        return matches[:top_k]


# =============================================================================
# Prompts — investidores (migrado de investor_match.py)
# =============================================================================

MATCH_SYSTEM_INVESTIDOR = """Você conecta uma STARTUP DEEP-TECH a INVESTIDORES (fundos de VC) a partir de um diretório.
NÃO é um gate: rankeie por aderência, SEM eliminar (fundo não desqualifica por CNPJ/porte).

Dois modos, conforme o campo `generalista` de cada fundo:
- Fundo COM TESE (generalista=false): pese o alinhamento de TESE/TEMA/SETOR entre a tese do
  fundo e o domínio da startup. Tema importa muito.
- Fundo GENERALISTA (generalista=true): tema é IRRELEVANTE (investe em qualquer setor). Pese
  o ESTÁGIO (o estágio da startup está em estagio_alvo do fundo?) e a tração. Um generalista
  casa se o estágio bate — não penalize por tema.

Responda APENAS JSON válido, sem markdown:
{"matches": [{"id": "...", "name": "...", "score": 0.0, "generalista": true|false,
  "match_dimensions": {"tese": "1 frase", "setor": "1 frase", "estagio": "1 frase"},
  "justificativa": "parágrafo curto"}]}"""

MATCH_USER_INVESTIDOR = """PERFIL DA STARTUP:
{profile}

DIRETÓRIO DE INVESTIDORES:
{catalog}

Retorne os {top_k} investidores mais aderentes no formato JSON especificado. score de 0.0 a 10.0."""


def _format_investidor_props(i: dict) -> str:
    """Uma entrada do diretório → linha no prompt."""
    gen = "GENERALISTA" if i.get("generalista") else "tese"
    themes = ", ".join(i.get("tese_themes", [])) or "—"
    kw = ", ".join(i.get("tese_keywords", []))
    line = (
        f'ID:{i["id"]} | {i["name"]} | modo:{gen} | '
        f'estagio:{",".join(i.get("estagio_alvo", []))} | '
        f'setores:{",".join(i.get("setores", []))} | temas:{themes} | '
        f'tese:{(i.get("tese") or "")[:140]}'
    )
    if kw:
        line += f" | kw:{kw}"
    return line


def _enrich_investidor(match: dict, raw: dict) -> dict:
    """Enriquece match com campos de display do fundo."""
    match["site"] = raw.get("site")
    match["lead_follow"] = raw.get("lead_follow")
    match["fund_status"] = raw.get("fund_status")
    match["estagio_alvo"] = raw.get("estagio_alvo", [])
    return match


# =============================================================================
# Prompts — programas (migrado de programa_match.py)
# =============================================================================

MATCH_SYSTEM_PROGRAMA = """Você conecta uma STARTUP DEEP-TECH a PROGRAMAS DE FOMENTO RECORRENTES (aceleração, incubação, subvenção, capacitação) a partir de um diretório.
NÃO é um gate: rankeie por aderência, SEM eliminar (programa não desqualifica por CNPJ/porte).

Pese, nesta ordem:
1. ESTÁGIO: o estágio da startup está em `estagio_alvo` do programa? É o sinal mais forte (programas são amplos por estágio/fase).
2. ELEGIBILIDADE: o porte/fase/exigências (`elegibilidade`) são compatíveis com a startup? (ex.: exige ICT parceira, faturamento máximo, fase de tração).
3. TEMA/SETOR: SÓ quando o programa tem `tese_themes`/`setores` específicos — alinhe com o domínio da startup. Programa multissetorial (tese_themes vazio) NÃO penaliza por tema.

Responda APENAS JSON válido, sem markdown:
{"matches": [{"id": "...", "name": "...", "score": 0.0,
  "match_dimensions": {"estagio": "1 frase", "elegibilidade": "1 frase", "tema": "1 frase"},
  "justificativa": "parágrafo curto"}]}"""

MATCH_USER_PROGRAMA = """PERFIL DA STARTUP:
{profile}

DIRETÓRIO DE PROGRAMAS:
{catalog}

Retorne os {top_k} programas mais aderentes no formato JSON especificado. score de 0.0 a 10.0."""


def _format_programa_props(p: dict) -> str:
    """Uma entrada do diretório → linha no prompt."""
    themes = ", ".join(p.get("tese_themes", [])) or "multissetorial"
    return (
        f'ID:{p["id"]} | {p["name"]} | tipo:{p.get("tipo", "")} | '
        f'operador:{p.get("operador", "")} | '
        f'estagio:{",".join(p.get("estagio_alvo", []))} | '
        f'setores:{",".join(p.get("setores", [])) or "—"} | temas:{themes} | '
        f'elegibilidade:{(p.get("elegibilidade") or "")[:160]}'
    )


def _enrich_programa(match: dict, raw: dict) -> dict:
    """Enriquece match com campos de display do programa."""
    match["site"] = raw.get("site")
    match["tipo"] = raw.get("tipo")
    match["beneficio"] = raw.get("beneficio")
    match["faq_url"] = raw.get("faq_url")
    match["status"] = raw.get("status")
    match["estagio_alvo"] = raw.get("estagio_alvo", [])
    return match


# =============================================================================
# Catálogos como instâncias de EntityCatalog
# =============================================================================

catalog_investidores = EntityCatalog(
    loader=kg_store.load_investidores,
    system_prompt=MATCH_SYSTEM_INVESTIDOR,
    user_prompt_template=MATCH_USER_INVESTIDOR,
    format_item=_format_investidor_props,
    enrich=_enrich_investidor,
)

catalog_programas = EntityCatalog(
    loader=kg_store.load_programas,
    system_prompt=MATCH_SYSTEM_PROGRAMA,
    user_prompt_template=MATCH_USER_PROGRAMA,
    format_item=_format_programa_props,
    enrich=_enrich_programa,
)
