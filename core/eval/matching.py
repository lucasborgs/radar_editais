"""Suíte de avaliação do matching — motor v3 (Fase 2 da spec v3-unified).

Roda o funil de produção (`core.services.match_v3.find_matching_opportunities`,
Stage 0→1→2) por empresa contra o golden de AFINIDADE DE CONTEÚDO em
`eval_data/golden/matching.json` + os hard negatives de ELEGIBILIDADE em
`matching_hard_negatives.json`. Pré-beta: mede CORREÇÃO ABSOLUTA do v3 — sem
célula de comparação com o motor v2 (deletado nesta fase; rollback = git revert).

Métricas (piso do gate da Fase 2):
  mrr        — posição do 1º positivo no ranking (piso: média ≥ 0.6)
  recall@10  — positivos do golden no top-10 (piso: média ≥ 0.55)
  noise@8    — matches no top-8 acima do PISO DE PRODUÇÃO (MIN_AFFINITY) que
               não são positivos NEM neutros (guarda-chuva) = falso-positivo
  hardneg    — hard negative de elegibilidade eliminado no Stage 1 (piso: 3/3)

`as_of` é PINADO em 2026-07-05 (data de curadoria do golden): o Stage 0 "vivo"
avaliado na data em que os positivos eram fato — staleness do corpus (editais
que fecharam depois) não vira falso-negativo. O teto estrutural de recall
continua existindo (positivos que já estavam mortos na data do golden — ver
PR #67); o piso do gate já desconta isso.

Determinístico dado o corpus: o lado empresa vem do texto do perfil (mesmo
chunking do motor, `use_hyde=False` — sem LLM), embeddings são a única rede.
"""
from __future__ import annotations

import datetime
import json
from functools import lru_cache
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "eval_data" / "golden" / "matching.json"
HARDNEG = ROOT / "eval_data" / "golden" / "matching_hard_negatives.json"

# Data de curadoria do golden — pina o Stage 0 (ver docstring).
AS_OF = datetime.date(2026, 7, 5)
TOP_K = 10          # ranking medido no top-10 (r@10 é métrica do gate)
SUITE_K = 8         # o produto exibe ~8 — janela do noise


@lru_cache(maxsize=1)
def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _hardneg() -> dict:
    return json.loads(HARDNEG.read_text(encoding="utf-8"))


def _file_key(native_id: str) -> str:
    """`finep:602` → `finep__602` (formato dos ids do golden)."""
    return native_id.replace(":", "__", 1)


# ---------------------------------------------------------------------------
# Suíte
# ---------------------------------------------------------------------------

def load_data() -> list[dict]:
    g = _golden()
    items: list[dict] = []
    for case in g["cases"]:
        company = case["company"]
        raw = g["profiles"].get(company)
        if raw is None:
            continue
        items.append({
            "input": {"case_kind": "ranking", "profile": raw, "top_k": TOP_K},
            "expected_output": {"relevant": case["relevant"], "neutral": g["neutral"]},
            "metadata": {"case_id": company},
        })
    for case in _hardneg()["cases"]:
        profile = (g["profiles"][case["profile_ref"]]
                   if "profile_ref" in case else case["profile"])
        items.append({
            "input": {"case_kind": "hardneg", "profile": profile, "edital": case["edital"]},
            "expected_output": {"expected_stage1": case["expected_stage1"]},
            "metadata": {"case_id": case["id"]},
        })
    return items


def task(*, item: Any, **_) -> dict:
    from core.services import match_v3

    inp = get_input(item)

    if inp.get("case_kind") == "hardneg":
        v = match_v3.stage1_verdict(inp["edital"], inp["profile"])
        if v is None:
            return {"error": f"edital {inp['edital']} não encontrado em entities"}
        return {"stage1": v}

    # Ranking: piso DESLIGADO (min_affinity=0) p/ medir mrr/recall no ranking
    # completo; o noise@8 reaplica o piso de produção via `affinity` retornada.
    matches = match_v3.find_matching_opportunities(
        inp["profile"], kinds=frozenset({"edital"}), as_of=AS_OF,
        top_k=inp.get("top_k", TOP_K), min_affinity=0.0, use_hyde=False,
    )
    return {
        "ranked": [_file_key(m.entity_id) for m in matches],
        "matches": [{
            "file_key": _file_key(m.entity_id), "name": m.name[:60],
            "affinity": round(m.affinity, 3), "score": round(m.score, 3),
        } for m in matches],
    }


# ---------------------------------------------------------------------------
# Evaluators (None = não se aplica ao item — o harness ignora)
# ---------------------------------------------------------------------------

def eval_mrr(*, output, expected_output, **_) -> Evaluation | None:
    """1/rank do primeiro positivo (0 se nenhum apareceu). None p/ hardneg e controle."""
    exp = expected_output or {}
    if "relevant" not in exp:
        return None
    relevant = set(exp["relevant"])
    if not relevant:
        return None  # empresa-controle (fintech): sem positivos
    ranked = output.get("ranked", []) if isinstance(output, dict) else []
    rr = 0.0
    for i, fk in enumerate(ranked, start=1):
        if fk in relevant:
            rr = 1.0 / i
            break
    return {"name": "mrr", "value": round(rr, 3)}


def eval_recall10(*, output, expected_output, **_) -> Evaluation | None:
    """recall@10 = positivos do golden presentes no top-10."""
    exp = expected_output or {}
    if "relevant" not in exp:
        return None
    relevant = set(exp["relevant"])
    if not relevant:
        return None
    ranked = output.get("ranked", []) if isinstance(output, dict) else []
    hit = relevant & set(ranked[:10])
    miss = sorted(relevant - set(ranked[:10]))
    return {"name": "recall_at_10", "value": round(len(hit) / len(relevant), 3),
            "comment": f"miss={miss}" if miss else "todos no top-10"}


def eval_noise(*, output, expected_output, **_) -> Evaluation | None:
    """Falso-positivo no top-8 ACIMA do piso de produção: fora de positivos E
    de neutros (guarda-chuva). Reaplica MIN_AFFINITY sobre o ranking sem piso."""
    from core.services.match_v3 import MIN_AFFINITY

    exp = expected_output or {}
    if "relevant" not in exp:
        return None
    relevant, neutral = set(exp.get("relevant", [])), set(exp.get("neutral", []))
    ms = output.get("matches", []) if isinstance(output, dict) else []
    shown = [m["file_key"] for m in ms if m["affinity"] >= MIN_AFFINITY][:SUITE_K]
    noise = [fk for fk in shown if fk not in relevant and fk not in neutral]
    return {"name": "noise", "value": len(noise), "comment": f"fp={noise}" if noise else ""}


def eval_hardneg(*, output, expected_output, **_) -> Evaluation | None:
    """Hard negative de elegibilidade: Stage 1 elimina quando (e só quando) o
    golden diz `inelegivel`. 1.0 = veredito correto."""
    from core.services import eligibility

    exp = expected_output or {}
    if "expected_stage1" not in exp:
        return None
    if not isinstance(output, dict) or "stage1" not in output:
        return {"name": "hardneg_pass", "value": 0.0,
                "comment": str(output.get("error") if isinstance(output, dict) else output)}
    got = output["stage1"]["status"]
    ok = (got == eligibility.INELEGIVEL) == (exp["expected_stage1"] == "inelegivel")
    return {"name": "hardneg_pass", "value": 1.0 if ok else 0.0,
            "comment": f"got={got} unsat={output['stage1'].get('unsat')}"}


def _prereqs() -> str | None:
    import os
    if not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY (embeddings do lado empresa)"
    if not os.getenv("DATABASE_URL"):
        return "requer DATABASE_URL (tabelas gold: entities/match_chunks)"
    try:
        from core.services.match_v3 import _get_snapshot
        snap = _get_snapshot()
        if not snap.opportunities:
            return "entities vazio — rode `python -m core.kg.gold` no Postgres local"
    except Exception as e:  # noqa: BLE001
        return f"tabelas gold inacessíveis ({e})"
    return None


SUITE = Suite(
    name="matching",
    description="Funil v3 (Stage 0-2) vs golden: mrr/recall@10/noise + hard negatives de elegibilidade.",
    load_data=load_data,
    task=task,
    evaluators=[eval_mrr, eval_recall10, eval_noise, eval_hardneg],
    prereqs=_prereqs,
)
