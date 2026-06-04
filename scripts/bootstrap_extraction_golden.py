#!/usr/bin/env python3
"""Bootstrap do golden de extração (Fase 2.3).

Pega N editais de cada fonte (FINEP=texto de PDF, FAPESP=texto cru do adapter,
WEB=página re-fetchada), roda um modelo forte contra o schema `EditalExtraction`
(JSON mode + validação pydantic) e grava um **rascunho** de golden para o humano
CORRIGIR. O golden corrigido (não este rascunho) é a verdade que a suíte
`extraction` vai medir.

A LLM é instruída a ABSTER (`state=absent`) quando o campo não consta — não
inventar — e a citar `evidence` (trecho da fonte) quando afirma algo. É isso que
torna "não consta" um estado medível, em vez de meio-crédito cego no scoring.

Uso:
    python scripts/bootstrap_extraction_golden.py --dry-run         # só junta inputs
    python scripts/bootstrap_extraction_golden.py --n 5             # roda extração (custa API)
    python scripts/bootstrap_extraction_golden.py --source web --n 3
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from config import BRONZE_DIR  # noqa: E402
from domain.edital_extraction import EditalExtraction  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bootstrap_golden")

OUT = ROOT / "eval_data" / "golden" / "extraction_draft.json"
RAW_CAP = 12000  # corta o input cru para controlar tokens


# ---------------------------------------------------------------------------
# Coleta de input cru por fonte
# ---------------------------------------------------------------------------

def _latest(pattern: str) -> Path | None:
    files = sorted(glob.glob(str(BRONZE_DIR / pattern)))
    return Path(files[-1]) if files else None


def _gather_finep(n: int) -> list[dict]:
    f = _latest("finep_raw/*.json")
    if not f:
        return []
    recs = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for r in recs[:n]:
        pdf = "\n".join((r.get("pdf_texts") or {}).values())
        raw = "\n".join(filter(None, [r.get("titulo", ""), r.get("descricao", ""), pdf]))
        out.append({"source": "finep", "native_id": str(r.get("chamada_id", "")),
                    "title": r.get("titulo", ""), "raw": raw[:RAW_CAP]})
    return out


def _gather_fapesp(n: int) -> list[dict]:
    f = _latest("fapesp_raw/*.json")
    if not f:
        return []
    recs = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for r in recs[:n]:
        raw = "\n".join(filter(None, [r.get("titulo", ""), r.get("texto_cru", "")]))
        nid = r.get("url", "").rstrip("/").split("/")[-1] or r.get("titulo", "")[:40]
        out.append({"source": "fapesp", "native_id": nid,
                    "title": r.get("titulo", ""), "raw": raw[:RAW_CAP]})
    return out


def _gather_web(n: int) -> list[dict]:
    f = _latest("discovery_raw/discovery_*.json")
    if not f:
        return []
    recs = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for r in recs[:n]:
        # input fiel = página re-fetchada (não o descricao raso do bronze)
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


_GATHERERS = {"finep": _gather_finep, "fapesp": _gather_fapesp, "web": _gather_web}


# ---------------------------------------------------------------------------
# Extração via LLM (rascunho)
# ---------------------------------------------------------------------------

_SYSTEM = """Você extrai campos estruturados de um edital de fomento brasileiro a
partir do texto bruto da fonte, para alimentar um sistema de matching.

REGRAS (críticas):
- Para cada campo DECISÃO, devolva {{"value", "state", "evidence"}}:
    state="stated"   → o texto AFIRMA explicitamente o valor (cite o trecho em evidence).
    state="inferred" → você DEDUZIU de pistas indiretas (evidence = a pista).
    state="absent"   → o texto NÃO informa. value=null, evidence=null. NÃO INVENTE.
- "Não consta" é uma resposta válida e desejável. Alucinar elegibilidade é o pior erro.
- Campos CONTEXTO (title, objective, key_requirements) são valor direto ou null.

Responda APENAS com JSON no schema fornecido."""

_USER = """FORMATO DE SAÍDA (use EXATAMENTE estas chaves no nível raiz — NÃO agrupe
por categoria; preencha cada campo a partir do texto):
{schema}

TEXTO DA FONTE ({source}):
{raw}

Extraia o JSON. Lembre: abstenha (state="absent", value=null) quando não constar."""


def _extract(item: dict) -> dict:
    import os

    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.getenv("OPENAI_MODEL_PRO", "gpt-4o")

    # Esqueleto com as CHAVES REAIS no topo. O modelo espelha a estrutura que
    # recebe — agrupar por categoria fazia ele aninhar tudo e o validador caía
    # nos defaults (absent). Aqui as chaves são exatamente os campos do schema.
    skeleton = {
        "eligible_entities": {"value": ["empresas"], "state": "stated|inferred|absent",
                              "evidence": "trecho da fonte ou null"},
        "themes": {"value": ["tema"], "state": "...", "evidence": "..."},
        "eligible_sectors": {"value": ["setor"], "state": "...", "evidence": "..."},
        "trl_range": {"value": {"min": 4, "max": 6}, "state": "...", "evidence": "..."},
        "mechanism": {"value": "subvencao|reembolsavel|premio|...", "state": "...",
                      "evidence": "..."},
        "counterpart_required": {"value": True, "state": "...", "evidence": "..."},
        "title": "título ou null",
        "objective": "objetivo em 2-3 frases ou null",
        "key_requirements": ["requisito 1", "requisito 2"],
        "status": "ABERTA|ENCERRADA ou null",
        "deadline": "data ou null",
    }
    user = _USER.format(schema=json.dumps(skeleton, ensure_ascii=False, indent=2),
                        source=item["source"], raw=item["raw"])
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    data["source"] = item["source"]
    data["native_id"] = item["native_id"]
    return EditalExtraction.model_validate(data).model_dump()


def _abstention_summary(extractions: list[dict]) -> dict:
    from domain.edital_extraction import DECISION_FIELDS
    counts: dict[str, dict] = {f: {"stated": 0, "inferred": 0, "absent": 0} for f in DECISION_FIELDS}
    for e in extractions:
        for f in DECISION_FIELDS:
            st = (e.get(f) or {}).get("state", "absent")
            counts[f][st] = counts[f].get(st, 0) + 1
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=5, help="editais por fonte")
    p.add_argument("--source", choices=list(_GATHERERS), help="só uma fonte")
    p.add_argument("--dry-run", action="store_true", help="só junta inputs, sem LLM")
    args = p.parse_args()

    sources = [args.source] if args.source else list(_GATHERERS)
    items: list[dict] = []
    for s in sources:
        got = _GATHERERS[s](args.n)
        print(f"  [{s}] {len(got)} inputs (chars médios: "
              f"{sum(len(g['raw']) for g in got)//max(len(got),1)})", file=sys.stderr)
        items.extend(got)

    if args.dry_run:
        print(f"\nDRY-RUN: {len(items)} inputs coletados. Amostra:")
        for it in items:
            print(f"  [{it['source']}] {it['native_id'][:30]:30s} {it['title'][:50]:50s} "
                  f"raw={len(it['raw'])} chars")
        return 0

    golden: list[dict] = []
    for it in items:
        print(f"→ extraindo [{it['source']}] {it['native_id'][:40]}...", file=sys.stderr)
        try:
            extraction = _extract(it)
        except Exception as e:
            logger.error("extração falhou (%s/%s): %s", it["source"], it["native_id"], e)
            continue
        golden.append({
            "source": it["source"], "native_id": it["native_id"], "title": it["title"],
            "input_excerpt": it["raw"][:400], "extraction": extraction,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRascunho do golden: {OUT}  ({len(golden)} editais)")
    print("\nAbstenção por campo DECISÃO (stated/inferred/absent):")
    for f, c in _abstention_summary([g["extraction"] for g in golden]).items():
        print(f"  {f:20s} {c}")
    print("\n>>> REVISE e corrija eval_data/golden/extraction_draft.json antes de usar como verdade.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
