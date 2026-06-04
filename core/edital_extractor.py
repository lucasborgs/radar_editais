"""Extrator de editais — texto bruto da fonte → `EditalExtraction` (schema v2).

Semente do extrator de PRODUÇÃO (Fase 3): a mesma função serve (a) ao bootstrap
do golden, (b) à suíte de avaliação `extraction`, e (c) — quando promovido — ao
ETL, substituindo os normalizadores hand-written.

Princípios do schema (ver domain/edital_extraction.py): abstenção explícita,
`evidence` verbatim, `inferred` proibido em campos de elegibilidade, `themes`
cru (canonicalização é passo seguinte).

Coleta de input cru por fonte: FINEP = texto dos PDFs; FAPESP = `texto_cru` do
adapter; WEB = página re-fetchada. O específico-de-fonte vive AQUI (no input),
não no schema — a extração-para-schema é única e agnóstica.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path

from config import BRONZE_DIR
from domain.edital_extraction import EditalExtraction

logger = logging.getLogger(__name__)

RAW_CAP = 12000  # corta o input cru para controlar tokens


# ---------------------------------------------------------------------------
# Coleta de input cru por fonte
# ---------------------------------------------------------------------------

def _latest(pattern: str) -> Path | None:
    files = sorted(glob.glob(str(BRONZE_DIR / pattern)))
    return Path(files[-1]) if files else None


def _gather_finep(n: int | None) -> list[dict]:
    f = _latest("finep_raw/*.json")
    if not f:
        return []
    out = []
    for r in json.loads(f.read_text(encoding="utf-8"))[:n]:
        pdf = "\n".join((r.get("pdf_texts") or {}).values())
        raw = "\n".join(filter(None, [r.get("titulo", ""), r.get("descricao", ""), pdf]))
        out.append({"source": "finep", "native_id": str(r.get("chamada_id", "")),
                    "title": r.get("titulo", ""), "raw": raw[:RAW_CAP]})
    return out


def _gather_fapesp(n: int | None) -> list[dict]:
    f = _latest("fapesp_raw/*.json")
    if not f:
        return []
    out = []
    for r in json.loads(f.read_text(encoding="utf-8"))[:n]:
        raw = "\n".join(filter(None, [r.get("titulo", ""), r.get("texto_cru", "")]))
        nid = r.get("url", "").rstrip("/").split("/")[-1] or r.get("titulo", "")[:40]
        out.append({"source": "fapesp", "native_id": nid,
                    "title": r.get("titulo", ""), "raw": raw[:RAW_CAP]})
    return out


def _gather_web(n: int | None) -> list[dict]:
    f = _latest("discovery_raw/discovery_*.json")
    if not f:
        return []
    out = []
    for r in json.loads(f.read_text(encoding="utf-8"))[:n]:
        raw = ""
        try:
            from core.agent_tools.profile_tools import _fetch_and_parse
            raw = _fetch_and_parse(r.get("link", "")).get("text", "")
        except Exception as e:
            logger.warning("re-fetch falhou (%s): %s", r.get("link"), e)
        if not raw:
            raw = "\n".join(filter(None, [r.get("titulo", ""), r.get("descricao", "")]))
        out.append({"source": r.get("source", "web"), "native_id": r.get("native_id", ""),
                    "title": r.get("titulo", ""), "raw": raw[:RAW_CAP]})
    return out


GATHERERS = {"finep": _gather_finep, "fapesp": _gather_fapesp, "web": _gather_web}


def gather_source(source: str, n: int | None = None) -> list[dict]:
    """Inputs crus `{source, native_id, title, raw}` de uma fonte (n=None = todos)."""
    if source not in GATHERERS:
        raise KeyError(f"fonte sem gatherer: {source!r}")
    return GATHERERS[source](n)


def raw_by_native_id(source: str) -> dict[str, dict]:
    """Índice `{native_id: {title, raw}}` para a suíte casar golden ↔ input."""
    return {it["native_id"]: it for it in gather_source(source)}


# ---------------------------------------------------------------------------
# Extração via LLM
# ---------------------------------------------------------------------------

_SYSTEM = """Você extrai campos estruturados de um edital de fomento brasileiro a
partir do texto bruto da fonte, para alimentar um sistema de matching.

REGRAS (críticas):
- Para cada campo DECISÃO, devolva {{"value", "state", "evidence"}}:
    state="stated" → o texto AFIRMA explicitamente. evidence = SUBSTRING VERBATIM
                     do texto (copie o trecho exato, não resuma).
    state="absent" → o texto NÃO informa. value=null, evidence=null. NÃO INVENTE.
  NÃO use "inferred" para os campos de elegibilidade (eligible_entities, themes,
  trl_range, mechanism): ou está escrito (stated) ou não está (absent).
- "Não consta" é resposta válida e desejável. Alucinar elegibilidade é o pior erro.
- themes: extraia o termo CRU como aparece (ex.: "micromobilidade"), não normalize.
- counterpart: capture o PERCENTUAL quando houver (5% ≠ 50%).
- eligibility_constraints: restrições organizacionais DURAS (região, idade da
  empresa, faturamento, CNAE, consórcio) — estruture aqui, NÃO enterre só no texto.
- Campos CONTEXTO (title, objective, key_requirements, funding_amount,
  project_duration_months) são valor direto ou null.

Responda APENAS com JSON no formato fornecido."""

_USER = """FORMATO DE SAÍDA (use EXATAMENTE estas chaves no nível raiz — NÃO agrupe
por categoria; preencha cada campo a partir do texto):
{schema}

TEXTO DA FONTE ({source}):
{raw}

Extraia o JSON. Lembre: abstenha (state="absent", value=null) quando não constar."""

_SKELETON = {
    "eligible_entities": {"value": ["empresas"], "state": "stated|absent",
                          "evidence": "SUBSTRING VERBATIM da fonte ou null"},
    "themes": {"value": ["tema CRU verbatim, ex.: micromobilidade"],
               "state": "stated|absent", "evidence": "..."},
    "trl_range": {"value": {"min": 4, "max": 6}, "state": "stated|absent", "evidence": "..."},
    "mechanism": {"value": "subvencao|reembolsavel|premio|bolsa|encomenda",
                  "state": "stated|absent", "evidence": "..."},
    "counterpart": {"value": {"required": True, "percentage": 20},
                    "state": "stated|absent", "evidence": "..."},
    "requires_ict_partner": {"value": True, "state": "stated|absent", "evidence": "..."},
    "title": "título ou null",
    "objective": "objetivo em 2-3 frases ou null",
    "key_requirements": ["requisito 1", "requisito 2"],
    "funding_amount": {"min": 500000, "max": 2000000},
    "project_duration_months": 24,
    "eligibility_constraints": [
        {"type": "region|company_age|revenue|cnae|consortium",
         "description": "o requisito em texto curto",
         "state": "stated", "evidence": "substring verbatim"},
    ],
}


def extract_edital(source: str, native_id: str, raw: str, *, model: str | None = None) -> EditalExtraction:
    """Extrai um `EditalExtraction` do texto bruto via LLM (JSON mode + validação)."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = model or os.getenv("OPENAI_MODEL_PRO", "gpt-4o")

    user = _USER.format(schema=json.dumps(_SKELETON, ensure_ascii=False, indent=2),
                        source=source, raw=raw)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    data["source"] = source
    data["native_id"] = native_id
    return EditalExtraction.model_validate(data)
