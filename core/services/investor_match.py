"""Match-por-tese: conecta uma startup deep-tech a INVESTIDORES (Q3).

Motor ISOLADO da entidade `investidor`. NÃO toca o
matcher de edital (HybridMatch/KGMatch) — é um caminho paralelo, aditivo. Sem
GATE (fundo não desqualifica por CNPJ): rankeia por aderência, soft.

Dois modos, decididos pelo flag `generalista` do fundo (achado da curadoria):
  • Fundo COM TESE (generalista=false): match por TESE/TEMA/SETOR.
  • Fundo GENERALISTA (generalista=true): tema é irrelevante (investe em tudo) →
    match por ESTÁGIO + tração.

Catálogo é pequeno (~11-50 fundos) → cabe inteiro no prompt (estilo KGMatch
"Karpathy"): o LLM lê o diretório + perfil e rankeia numa passada. Degrada para
[] sem credencial LLM. Sem chamadas de rede além do LLM.
"""
from __future__ import annotations

import json
import logging
import os
import re

from core.kg import kg_store
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)


MATCH_SYSTEM = """Você conecta uma STARTUP DEEP-TECH a INVESTIDORES (fundos de VC) a partir de um diretório.
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

MATCH_USER = """PERFIL DA STARTUP:
{profile}

DIRETÓRIO DE INVESTIDORES:
{catalog}

Retorne os {top_k} investidores mais aderentes no formato JSON especificado. score de 0.0 a 10.0."""


def _make_client():
    """(client, model) conforme LLM_BACKEND. Levanta se sem credencial — o caller
    captura e degrada. Mesmo padrão de core.services.kg_match_service."""
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
        return make_client(api_key="ollama", base_url="http://localhost:11434/v1"), \
            os.getenv("OLLAMA_MODEL", "llama3.2")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return make_client(api_key=api_key), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _format_catalog(invs: list[dict]) -> str:
    """Diretório compacto p/ o prompt — uma linha por fundo, com o flag que decide
    o modo de match e a tese (substrato do match fino)."""
    lines = []
    for i in invs:
        gen = "GENERALISTA" if i.get("generalista") else "tese"
        themes = ", ".join(i.get("tese_themes", [])) or "—"
        kw = ", ".join(i.get("tese_keywords", []))
        lines.append(
            f'ID:{i["id"]} | {i["name"]} | modo:{gen} | '
            f'estagio:{",".join(i.get("estagio_alvo", []))} | '
            f'setores:{",".join(i.get("setores", []))} | temas:{themes} | '
            f'tese:{(i.get("tese") or "")[:140]}'
            + (f' | kw:{kw}' if kw else "")
        )
    return "\n".join(lines)


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
    logger.warning("investor_match: não parseou a resposta: %s", raw[:200])
    return []


def match_investidores(profile: CompanyProfile, top_k: int = 5) -> list[dict]:
    """Retorna os top_k investidores mais aderentes ao perfil. [] sem diretório,
    sem credencial LLM ou em falha (nunca levanta). Enriquece cada match com os
    campos de display do fundo (site, lead_follow, fund_status)."""
    invs = kg_store.load_investidores()
    if not invs:
        return []
    try:
        client, model = _make_client()
    except Exception as e:
        logger.warning("investor_match: sem LLM (%s) — degradando para []", e)
        return []

    prompt = MATCH_USER.format(
        profile=profile.to_context(),
        catalog=_format_catalog(invs),
        top_k=top_k,
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": MATCH_SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
        matches = _parse(resp.choices[0].message.content.strip())
    except Exception as e:
        logger.error("investor_match: erro LLM: %s", e)
        return []

    by_id = {i["id"]: i for i in invs}
    for m in matches:
        fund = by_id.get(m.get("id"))
        if fund:
            m["site"] = fund.get("site")
            m["lead_follow"] = fund.get("lead_follow")
            m["fund_status"] = fund.get("fund_status")
            m["estagio_alvo"] = fund.get("estagio_alvo", [])
    return matches[:top_k]
