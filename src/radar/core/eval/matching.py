"""Suíte de avaliação do matching — motor v3 (Fase 2 da spec v3-unified).

Roda o funil de produção (`radar.core.services.match_v3.find_matching_opportunities`,
Stage 0→1→2) por empresa contra o golden de AFINIDADE DE CONTEÚDO em
`eval_data/golden/matching.json` + os hard negatives de ELEGIBILIDADE em
`matching_hard_negatives.json`. Pré-beta: mede CORREÇÃO ABSOLUTA do v3 — sem
célula de comparação com o motor v2 (deletado nesta fase; rollback = git revert).

Métricas (piso do gate da Fase 2):
  mrr        — posição do 1º positivo no ranking (piso: média ≥ 0.6)
  recall@10  — positivos do golden no top-10 (piso: média ≥ 0.55)
  false_positives@8 — resultados do top-8 explicitamente julgados irrelevantes
  unjudged@8 — resultados do top-8 ainda sem julgamento humano; impedem gate
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

from radar.core.config import ROOT
from radar.core.eval.harness import Criterion, Evaluation, Suite, get_input

GOLDEN = ROOT / "eval_data" / "golden" / "matching.json"
HARDNEG = ROOT / "eval_data" / "golden" / "matching_hard_negatives.json"

# Data de curadoria do golden — pina o Stage 0 (ver docstring).
AS_OF = datetime.date(2026, 7, 5)
TOP_K = 10          # ranking medido no top-10 (r@10 é métrica do gate)
SUITE_K = 8         # o produto exibe ~8 — janela de julgamento


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
            "expected_output": {
                "relevant": case["relevant"],
                "neutral": g["neutral"],
                "confirmed_irrelevant": case.get("confirmed_irrelevant", []),
            },
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
    from radar.core.services import match_v3

    inp = get_input(item)

    if inp.get("case_kind") == "hardneg":
        v = match_v3.stage1_verdict(inp["edital"], inp["profile"])
        if v is None:
            return {"error": f"edital {inp['edital']} não encontrado em entities"}
        return {"stage1": v}

    # Ranking: piso DESLIGADO (min_affinity=0) p/ medir mrr/recall no ranking
    # completo; as métricas do top-8 reaplicam o piso via `affinity` retornada.
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


def _shown_at_production_floor(output: Any) -> list[str]:
    from radar.core.services.match_v3 import MIN_AFFINITY

    matches = output.get("matches", []) if isinstance(output, dict) else []
    return [
        match["file_key"]
        for match in matches
        if match["affinity"] >= MIN_AFFINITY
    ][:SUITE_K]


def eval_false_positives(*, output, expected_output, **_) -> Evaluation | None:
    """Conta apenas irrelevâncias confirmadas por julgamento humano."""
    exp = expected_output or {}
    if "relevant" not in exp:
        return None
    irrelevant = set(exp.get("confirmed_irrelevant", []))
    false_positives = [fk for fk in _shown_at_production_floor(output) if fk in irrelevant]
    return {
        "name": "false_positives_at_8",
        "value": len(false_positives),
        "comment": f"confirmed={false_positives}" if false_positives else "",
    }


def eval_unjudged(*, output, expected_output, **_) -> Evaluation | None:
    """Resultado sem rótulo não é falso positivo, mas impede aprovação."""
    exp = expected_output or {}
    if "relevant" not in exp:
        return None
    judged = (
        set(exp.get("relevant", []))
        | set(exp.get("neutral", []))
        | set(exp.get("confirmed_irrelevant", []))
    )
    unjudged = [fk for fk in _shown_at_production_floor(output) if fk not in judged]
    return {
        "name": "unjudged_at_8",
        "value": len(unjudged),
        "comment": f"unjudged={unjudged}" if unjudged else "",
    }


def eval_hardneg(*, output, expected_output, **_) -> Evaluation | None:
    """Hard negative de elegibilidade: Stage 1 elimina quando (e só quando) o
    golden diz `inelegivel`. 1.0 = veredito correto."""
    from radar.core.services import eligibility

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
        from radar.core.services.match_v3 import _get_snapshot
        snap = _get_snapshot()
        if not snap.opportunities:
            return "entities vazio — rode `python -m radar.core.kg.gold` no Postgres local"
    except Exception as e:  # noqa: BLE001
        return f"tabelas gold inacessíveis ({e})"
    return None


def _expected_cases() -> int:
    return len(_golden()["cases"]) + len(_hardneg()["cases"])


def _expected_case_ids() -> list[str]:
    return [case["company"] for case in _golden()["cases"]] + [
        case["id"] for case in _hardneg()["cases"]
    ]


SUITE = Suite(
    name="matching",
    description="Funil v3 vs golden: ranking, falsos positivos confirmados e hard negatives.",
    load_data=load_data,
    task=task,
    evaluators=[eval_mrr, eval_recall10, eval_false_positives, eval_unjudged, eval_hardneg],
    prereqs=_prereqs,
    classification="candidate",
    version="2",
    criteria=[
        Criterion("mean_mrr", "gte", 0.60, "Piso aceito de posição do primeiro positivo."),
        Criterion("mean_recall_at_10", "gte", 0.55, "Piso aceito de recall no top-10."),
        Criterion("mean_hardneg_pass", "eq", 1.0, "Todos os hard negatives devem ser eliminados."),
        Criterion(
            "mean_false_positives_at_8", "eq", 0,
            "Nenhum falso positivo confirmado é aceitável no top-8.",
        ),
        Criterion(
            "mean_unjudged_at_8", "eq", 0,
            "Todo resultado exibido no top-8 deve possuir julgamento humano.",
        ),
    ],
    metric_directions={
        "mean_false_positives_at_8": "lower_is_better",
        "mean_unjudged_at_8": "lower_is_better",
    },
    dataset_paths=[GOLDEN, HARDNEG],
    expected_cases=_expected_cases,
    expected_case_ids=_expected_case_ids,
    manifest_env=["EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS", "MATCH_V3_MIN_AFFINITY"],
    manifest_config={
        "as_of": AS_OF.isoformat(),
        "ranking_top_k": TOP_K,
        "judgment_window": SUITE_K,
        "use_hyde": False,
    },
)
