"""Match-por-aderência: conecta uma startup a PROGRAMAS RECORRENTES (entidade).

Motor ISOLADO da entidade `programa` (espelha core.services.investor_match). NÃO
toca HybridMatch/KGMatch nem o investor_match — caminho paralelo, aditivo. Sem
GATE (programa não desqualifica por CNPJ): rankeia por aderência, soft.

Programas são em geral AMPLOS (multissetorial, early-stage), então o sinal forte é
o ESTÁGIO (estagio_alvo) + a compatibilidade de ELEGIBILIDADE (porte/fase/ICT) +,
quando o programa tem tese, o TEMA/SETOR. Catálogo pequeno (~10) cabe inteiro no
prompt. Degrada para [] sem credencial LLM. Sem rede além do LLM.
"""
from __future__ import annotations

import json
import logging
import os
import re

from core.kg import kg_store
from domain.user_profile import CompanyProfile

logger = logging.getLogger(__name__)


MATCH_SYSTEM = """Você conecta uma STARTUP DEEP-TECH a PROGRAMAS DE FOMENTO RECORRENTES (aceleração, incubação, subvenção, capacitação) a partir de um diretório.
NÃO é um gate: rankeie por aderência, SEM eliminar (programa não desqualifica por CNPJ/porte).

Pese, nesta ordem:
1. ESTÁGIO: o estágio da startup está em `estagio_alvo` do programa? É o sinal mais forte (programas são amplos por estágio/fase).
2. ELEGIBILIDADE: o porte/fase/exigências (`elegibilidade`) são compatíveis com a startup? (ex.: exige ICT parceira, faturamento máximo, fase de tração).
3. TEMA/SETOR: SÓ quando o programa tem `tese_themes`/`setores` específicos — alinhe com o domínio da startup. Programa multissetorial (tese_themes vazio) NÃO penaliza por tema.

Responda APENAS JSON válido, sem markdown:
{"matches": [{"id": "...", "name": "...", "score": 0.0,
  "match_dimensions": {"estagio": "1 frase", "elegibilidade": "1 frase", "tema": "1 frase"},
  "justificativa": "parágrafo curto"}]}"""

MATCH_USER = """PERFIL DA STARTUP:
{profile}

DIRETÓRIO DE PROGRAMAS:
{catalog}

Retorne os {top_k} programas mais aderentes no formato JSON especificado. score de 0.0 a 10.0."""


def _make_client():
    """(client, model) conforme LLM_BACKEND. Levanta se sem credencial — o caller
    captura e degrada. Mesmo padrão de core.services.investor_match."""
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


def _format_catalog(progs: list[dict]) -> str:
    """Diretório compacto p/ o prompt — uma linha por programa, com os campos que
    decidem o match (estágio, tipo, elegibilidade, tema/setor quando houver)."""
    lines = []
    for p in progs:
        themes = ", ".join(p.get("tese_themes", [])) or "multissetorial"
        lines.append(
            f'ID:{p["id"]} | {p["name"]} | tipo:{p.get("tipo", "")} | '
            f'operador:{p.get("operador", "")} | '
            f'estagio:{",".join(p.get("estagio_alvo", []))} | '
            f'setores:{",".join(p.get("setores", [])) or "—"} | temas:{themes} | '
            f'elegibilidade:{(p.get("elegibilidade") or "")[:160]}'
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
    logger.warning("programa_match: não parseou a resposta: %s", raw[:200])
    return []


def match_programas(profile: CompanyProfile, top_k: int = 5) -> list[dict]:
    """Retorna os top_k programas mais aderentes ao perfil. [] sem diretório, sem
    credencial LLM ou em falha (nunca levanta). Enriquece cada match com campos de
    display do programa (site, tipo, beneficio, faq_url, status)."""
    progs = kg_store.load_programas()
    if not progs:
        return []
    try:
        client, model = _make_client()
    except Exception as e:
        logger.warning("programa_match: sem LLM (%s) — degradando para []", e)
        return []

    prompt = MATCH_USER.format(
        profile=profile.to_context(),
        catalog=_format_catalog(progs),
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
        logger.error("programa_match: erro LLM: %s", e)
        return []

    by_id = {p["id"]: p for p in progs}
    for m in matches:
        prog = by_id.get(m.get("id"))
        if prog:
            m["site"] = prog.get("site")
            m["tipo"] = prog.get("tipo")
            m["beneficio"] = prog.get("beneficio")
            m["faq_url"] = prog.get("faq_url")
            m["status"] = prog.get("status")
            m["estagio_alvo"] = prog.get("estagio_alvo", [])
    return matches[:top_k]
