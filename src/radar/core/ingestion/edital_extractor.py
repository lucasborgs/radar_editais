"""Extrator avaliado de editais — texto bruto da fonte → `EditalExtraction`.

A função serve ao bootstrap do golden e à suíte `extraction`. O ingest gold
atual possui produtores próprios e não chama este módulo.

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

from radar.core.config import BRONZE_DIR
from radar.domain.edital_extraction import EditalExtraction

logger = logging.getLogger(__name__)

# Documento INTEIRO na janela do modelo (gpt-4o: 128k tokens). O default cobre
# os PDFs FINEP (~129k chars ≈ 32k tokens) com folga; só corta o doc patológico.
# Extração é 1×/edital offline → pagar o doc completo é aceitável (sem truncar
# campos de seções finais). Ajustável via env. ~250k chars ≈ 62k tokens.
RAW_CAP = int(os.getenv("EXTRACTION_RAW_CAP", "250000"))


# ---------------------------------------------------------------------------
# Coleta de input cru por fonte
# ---------------------------------------------------------------------------

def _latest(pattern: str) -> Path | None:
    files = sorted(glob.glob(str(BRONZE_DIR / pattern)))
    return Path(files[-1]) if files else None


# Legendas de PDFs que NÃO são o edital (anexos, avisos, resultados, boilerplate
# jurídico). Os campos de extração vivem no edital, não nestes — e somá-los
# triplica tokens (uma chamada FINEP traz o edital repetido por rerratificação +
# vários anexos), estourando o rate-limit sem ganho de sinal.
_FINEP_NON_EDITAL = ("anexo", "aviso", "resultado", "outorga", "termo de",
                     "declaração", "lista de", "modelo de", "especificaç")


def _finep_edital_text(r: dict) -> str:
    """Texto do EDITAL principal de uma chamada FINEP (descarta anexos/duplicatas).

    Mantém só PDFs com legenda de edital (edital/rerratificação), remove anexos e
    documentos administrativos, dedupa idênticos e fica com o mais longo (o corpo
    do edital; rerratificações de mesmo tamanho colapsam). Fallback: maior PDF.
    """
    pt = r.get("pdf_texts") or {}
    if not pt:
        return ""
    leg = r.get("pdf_legendas") or []
    names = list(pt.keys())
    edital: list[str] = []
    for i, name in enumerate(names):
        label = (leg[i] if i < len(leg) else name).lower()
        if any(x in label for x in _FINEP_NON_EDITAL):
            continue
        if "edital" in label or "rerratific" in label or "documento" in label:
            edital.append(pt[name])
    candidates = list(dict.fromkeys(edital)) or [max(pt.values(), key=len)]
    return max(candidates, key=len)


def _gather_finep(n: int | None) -> list[dict]:
    f = _latest("finep_raw/*.json")
    if not f:
        return []
    out = []
    for r in json.loads(f.read_text(encoding="utf-8"))[:n]:
        raw = "\n".join(filter(None, [r.get("titulo", ""), r.get("descricao", ""),
                                      _finep_edital_text(r)]))
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
    # Pós unificação (Opção A): páginas web (seed manual + Descoberta) vivem em
    # web_raw/ no schema web. O `texto_cru` já é o corpo completo da página
    # (a Descoberta faz full-fetch na ingestão), então não há re-fetch aqui.
    f = _latest("web_raw/*.json")
    if not f:
        return []
    out = []
    for r in json.loads(f.read_text(encoding="utf-8"))[:n]:
        raw = "\n".join(filter(None, [
            r.get("title", ""), r.get("texto_cru", "") or r.get("descricao", ""),
        ]))
        out.append({"source": "web", "native_id": r.get("url_hash", ""),
                    "title": r.get("title", ""), "raw": raw[:RAW_CAP]})
    return out


GATHERERS = {"finep": _gather_finep, "fapesp": _gather_fapesp, "web": _gather_web}


def gather_source(source: str, n: int | None = None) -> list[dict]:
    """Inputs crus `{source, native_id, title, raw}` de uma fonte (n=None = todos)."""
    if source not in GATHERERS:
        raise KeyError(f"fonte sem gatherer: {source!r}")
    return GATHERERS[source](n)


def raw_by_native_id(source: str) -> dict[str, dict]:
    """Índice `{native_id: {title, raw}}` para a suíte casar golden ↔ input.

    O scan FAPESP mais recente deixa de listar chamadas encerradas. Para que um
    golden continue reproduzível, o índice compõe todos os snapshots bronze e
    deixa o registro mais novo prevalecer. A ingestão normal continua lendo o
    snapshot mais recente por `gather_source`.
    """
    if source != "fapesp":
        return {it["native_id"]: it for it in gather_source(source)}

    index: dict[str, dict] = {}
    for path in sorted((BRONZE_DIR / "fapesp_raw").glob("*.json")):
        for record in json.loads(path.read_text(encoding="utf-8")):
            raw = "\n".join(filter(None, [
                record.get("titulo", ""), record.get("texto_cru", ""),
            ]))
            native_id = (
                record.get("url", "").rstrip("/").split("/")[-1]
                or record.get("titulo", "")[:40]
            )
            index[native_id] = {
                "source": "fapesp",
                "native_id": native_id,
                "title": record.get("titulo", ""),
                "raw": raw[:RAW_CAP],
            }
    return index


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


def _make_client():
    """Resolve (client, model) por `LLM_BACKEND` — mesmo contrato de structurer/
    kg_match_service, mas com os defaults da EXTRAÇÃO (slot OPENAI_MODEL_PRO).

    Sem env, é idêntico a hoje: gpt-4o no endpoint canônico OpenAI. `LLM_BACKEND=gemini`
    aponta o slot pro AI Studio (modelo via GEMINI_MODEL); `=ollama` pro local.
    `max_retries=6`: em tiers de TPM baixo o lote sequencial bate o rate-limit/min
    e o SDK respeita o Retry-After re-tentando sozinho.
    """
    from radar.core.llm.llm_client import make_client
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return make_client(
            api_key=api_key, max_retries=6,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if backend == "ollama":
        return make_client(api_key="ollama", max_retries=6,
                           base_url="http://localhost:11434/v1"), \
            os.getenv("OLLAMA_MODEL", "llama3.2")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return make_client(api_key=api_key, max_retries=6), os.getenv("OPENAI_MODEL_PRO", "gpt-4o-mini")


def extract_edital(source: str, native_id: str, raw: str, *, model: str | None = None) -> EditalExtraction:
    """Extrai um `EditalExtraction` do texto bruto via LLM (JSON mode + validação)."""
    client, default_model = _make_client()
    model = model or default_model

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
