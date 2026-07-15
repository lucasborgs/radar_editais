"""Golden dos quatro casos motivadores do Explorar.

Default hermético valida rota. Com ``EVAL_EXPLORE_CONNECTED=true`` executa o
pipeline real (retrieval, síntese/agente e catálogo) e julga conteúdo contra o
golden semântico do NotebookLM, com a adaptação temporal da FINEP.
"""
from __future__ import annotations

import json
import os
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "eval_data" / "golden" / "explore.json"


def load_data() -> list[dict]:
    if not GOLDEN.exists():
        return []
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [
        {
            "input": {"query": c["query"], "target": c["target"]},
            "expected_output": {
                "route": c["expected_route"],
                "reference_answer": c["reference_answer"],
            },
            "metadata": {
                "case_id": c["id"], "assertions": c["assertions"],
                "evidence": c["evidence"],
            },
        }
        for c in payload["cases"]
    ]


def task(*, item: Any, **_) -> dict:
    from core.services.explore_routing import RouteContext, route_message

    inp = get_input(item)
    target = inp["target"]
    decision = route_message(RouteContext(
        message=inp["query"], target_type=target["type"], target_id=target["id"],
    ))
    output = {"route": decision.intent.value, "decision": decision.to_dict()}
    if os.getenv("EVAL_EXPLORE_CONNECTED", "false").lower() != "true":
        return output

    from core.services.explore_agent import ExploreAgent

    kwargs = (
        {"edital_ids": [target["id"]]}
        if target["type"] == "edital"
        else {"node_id": target["id"], "node_type": target["type"]}
    )
    answer, meta = ExploreAgent().explore_with_meta(inp["query"], **kwargs)
    output.update({
        "answer": answer,
        "route": (meta.get("route_decision") or {}).get("intent", output["route"]),
        "called_tools": meta.get("called_tools", []),
    })
    return output


def eval_route(*, output, expected_output, **_) -> Evaluation:
    expected = (expected_output or {}).get("route")
    actual = (output or {}).get("route") if isinstance(output, dict) else None
    return {"name": "route_accuracy", "value": 1.0 if actual == expected else 0.0,
            "comment": f"expected={expected}, actual={actual}"}


def eval_tool_contract(*, output, expected_output, **_) -> Evaluation | None:
    if not isinstance(output, dict) or "answer" not in output:
        return None
    route = (expected_output or {}).get("route")
    expected_tool = (
        "search_edital_factual"
        if route in {"EDITAL_FACT", "EDITAL_FACT_ENUMERATIVE"}
        else "get_investidor" if route == "ENTITY_FACT" else None
    )
    called = output.get("called_tools") or []
    passed = expected_tool is None or expected_tool in called
    return {
        "name": "tool_contract", "value": 1.0 if passed else 0.0,
        "comment": f"expected={expected_tool}, called={called}",
    }


def eval_answer_contract(*, output, expected_output, metadata, **_) -> Evaluation | None:
    """Juiz semântico de required/forbidden/conditional, só no modo conectado."""
    if not isinstance(output, dict) or not output.get("answer"):
        return None
    from core.llm.llm_client import make_client

    assertions = (metadata or {}).get("assertions") or {}
    payload = {
        "reference": (expected_output or {}).get("reference_answer"),
        "required": assertions.get("required") or [],
        "forbidden": assertions.get("forbidden") or [],
        "conditional": assertions.get("conditional") or [],
        "answer": output["answer"],
    }
    client = make_client(api_key=os.environ["OPENAI_API_KEY"], max_retries=2)
    response = client.chat.completions.create(
        model=os.getenv("EVAL_EXPLORE_JUDGE_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        messages=[
            {
                "role": "system",
                "content": (
                    "Avalie semanticamente a resposta. Retorne JSON puro com "
                    "required_ok, forbidden_ok, conditional_ok (booleanos), "
                    "missing (lista) e violations (lista). Forbidden só falha "
                    "quando a resposta realmente faz a afirmação proibida."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0,
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    verdict = json.loads(response.choices[0].message.content or "{}")
    passed = all(verdict.get(key) is True for key in (
        "required_ok", "forbidden_ok", "conditional_ok",
    ))
    return {
        "name": "answer_contract", "value": 1.0 if passed else 0.0,
        "comment": json.dumps(verdict, ensure_ascii=False),
    }


def _prereqs() -> str | None:
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    if os.getenv("EVAL_EXPLORE_CONNECTED", "false").lower() == "true":
        if not (os.getenv("DATABASE_URL") and os.getenv("SUPABASE_URL")
                and os.getenv("SUPABASE_SERVICE_KEY") and os.getenv("OPENAI_API_KEY")):
            return "modo conectado requer DATABASE_URL+SUPABASE+OPENAI"
    return None


SUITE = Suite(
    name="explore",
    description="Rota hermética ou E2E conectado dos quatro casos factuais golden.",
    load_data=load_data,
    task=task,
    evaluators=[eval_route, eval_tool_contract, eval_answer_contract],
    prereqs=_prereqs,
    version="2",
    dataset_paths=[GOLDEN],
    expected_cases=4,
    expected_case_ids=[
        "finep-745-itens-financiaveis",
        "fapesc-31-2026-admissibilidade",
        "barn-verticais",
        "barn-tese",
    ],
    manifest_env=["EVAL_EXPLORE_CONNECTED", "EVAL_EXPLORE_JUDGE_MODEL"],
)
