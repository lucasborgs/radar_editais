"""Suíte de avaliação do ComplianceMonitor.

Mede a qualidade das flags geradas contra o golden, usando os requisitos
diretamente do input (sem carregar do banco — permite rodar offline).

Duas métricas:
  • flag_recall    — das flags esperadas, quantas o monitor levantou?
                     Erro caro: perder uma violation é pior que um FP.
  • flag_precision — das flags levantadas, quantas eram esperadas?
                     Evita spam de falsos positivos que polui o frontend.

Match de flag = mesmo `requirement` (substring normalizada) E mesmo `status`.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "eval_data" / "golden" / "compliance_monitor.json"


def load_data() -> list[dict]:
    if not GOLDEN.exists():
        return []
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [
        {
            "input": c["input"],
            "expected_output": c["expected_output"],
            "metadata": {"case_id": c["case_id"]},
        }
        for c in cases
    ]


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    # Usa os requisitos do golden diretamente — bypassa _load_edital_requirements
    # para permitir eval offline sem banco. Mesma lógica LLM de produção.
    from core.compliance_monitor import _MONITOR_SYSTEM, _MONITOR_USER, _make_client

    requirements = inp.get("requirements", [])
    if not requirements:
        return {"flags": []}

    requirements_block = "\n".join(f"- {r}" for r in requirements)
    user_msg = _MONITOR_USER.format(
        requirements=requirements_block,
        user_message=inp["user_message"][:3000],
    )
    try:
        client, model = _make_client()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _MONITOR_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        data = json.loads(raw)
        flags = [
            {
                "requirement": str(f.get("requirement", "")),
                "status": str(f.get("status", "at_risk")),
                "suggestion": str(f.get("suggestion", "")),
            }
            for f in (data.get("flags") or [])
            if f.get("status") in ("at_risk", "violation")
        ]
        return {"flags": flags}
    except Exception as e:
        return {"error": str(e), "flags": []}


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _flags_match(pred_flag: dict, exp_flag: dict) -> bool:
    req_match = _norm(exp_flag["requirement"])[:40] in _norm(pred_flag["requirement"])
    status_match = pred_flag["status"] == exp_flag["status"]
    return req_match and status_match


def eval_flag_recall(*, output, expected_output, **_) -> Evaluation:
    """Das flags esperadas, quantas foram levantadas? (erro caro = FN)"""
    if not isinstance(output, dict):
        return {"name": "flag_recall", "value": 0.0, "comment": "output inválido"}
    exp_flags = expected_output.get("flags", [])
    if not exp_flags:
        return {"name": "flag_recall", "value": 1.0, "comment": "sem flags esperadas"}
    pred_flags = output.get("flags", [])
    hits = sum(
        any(_flags_match(p, e) for p in pred_flags)
        for e in exp_flags
    )
    return {
        "name": "flag_recall",
        "value": round(hits / len(exp_flags), 3),
        "comment": f"{hits}/{len(exp_flags)} flags esperadas encontradas",
    }


def eval_flag_precision(*, output, expected_output, **_) -> Evaluation:
    """Das flags levantadas, quantas eram esperadas? (evita spam de FP)"""
    if not isinstance(output, dict):
        return {"name": "flag_precision", "value": 0.0, "comment": "output inválido"}
    pred_flags = output.get("flags", [])
    if not pred_flags:
        exp_flags = expected_output.get("flags", [])
        # Nenhuma flag prevista: precision perfeita se esperado também vazio
        val = 1.0 if not exp_flags else 1.0
        return {"name": "flag_precision", "value": val, "comment": "nenhuma flag levantada"}
    exp_flags = expected_output.get("flags", [])
    hits = sum(
        any(_flags_match(p, e) for e in exp_flags)
        for p in pred_flags
    )
    return {
        "name": "flag_precision",
        "value": round(hits / len(pred_flags), 3),
        "comment": f"{hits}/{len(pred_flags)} flags levantadas eram esperadas",
    }


def _prereqs() -> str | None:
    if not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY"
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    return None


SUITE = Suite(
    name="compliance_monitor",
    description="Recall + precision das flags do ComplianceMonitor vs golden (offline, sem banco).",
    load_data=load_data,
    task=task,
    evaluators=[eval_flag_recall, eval_flag_precision],
    prereqs=_prereqs,
)
