"""Suíte `spike_kg` — diagnóstico do KG estrutura-consciente (SPEC §12).

Exercita a travessia/serialização do spike (`src/radar/core/kg/spike`) sobre
arestas sintéticas que reproduzem a topologia real (edital → setor → ICT →
agência). Avalia que a textualização PRESERVA a topologia (adjacência, tipo,
peso, caminhos de dedução) — o contrato central da SPEC §10.

Hermética: sem LLM, sem DB, sem rede. classification="diagnostic",
criteria=() — nenhum threshold, nenhum gate (postura de provenance/e2e_health).
"""
from __future__ import annotations

import json

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite
from radar.core.kg.spike import serialize, traverse

GOLDEN_PATH = ROOT / "data" / "evaluation" / "golden" / "spike_kg.json"


def _load_golden() -> list[dict]:
    if not GOLDEN_PATH.exists():
        return []
    payload = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return [
        {
            "input": c["input"],
            "expected_output": c["expected_output"],
            "metadata": c.get("metadata", {}),
        }
        for c in payload["cases"]
    ]


def load_data() -> list[dict]:
    return _load_golden()


def _task(*, item: dict, **_) -> dict:
    """Task: BFS + serialização + dedução de caminho sobre as arestas do caso."""
    inp = item["input"]
    edges = inp["edges"]
    seed = inp["seed"]
    nodes = inp.get("nodes", [])
    quality = inp.get("quality_nodes", [])
    communities = {c["community_id"]: c["members"] for c in inp.get("communities", [])}
    goal = inp.get("goal")

    sub = serialize.serialize_subgraph(
        seed, edges, nodes, quality, depth=inp.get("depth", 1), communities=communities,
    )
    paths = traverse.find_paths(edges, seed, goal, max_depth=4) if goal else []
    return {
        "edges_serialized": sub["edges"],
        "nodes_kinds": sorted({n["kind"] for n in sub["nodes"]}),
        "communities": sub["communities"],
        "deduction_paths": [p for p in paths if p],
        "n_deductions": len(paths),
    }


def _eval_topology_preserved(*, output, expected_output, **_) -> Evaluation:
    """Toda aresta esperada no subgrafo deve aparecer com type e weight intactos."""
    expected_edges = (expected_output or {}).get("expected_edges") or []
    serialized = {  # set de (source, type, target) — adjacência preservada
        (e["source"], e["type"], e["target"]) for e in output.get("edges_serialized", [])
    }
    missing = [
        (e["source"], e["type"], e["target"])
        for e in expected_edges
        if (e["source"], e["type"], e["target"]) not in serialized
    ]
    return {
        "name": "topology_preserved",
        "value": 1.0 if not missing else 0.0,
        "comment": f"missing={missing}" if missing else "topologia preservada",
    }


def _eval_deduction_path(*, output, expected_output, **_) -> Evaluation | None:
    """Caminho de dedução esperado existe entre seed e goal."""
    expected = (expected_output or {}).get("deduction_paths")
    if expected is None:
        return None
    n = output.get("n_deductions", 0)
    return {
        "name": "deduction_path",
        "value": 1.0 if n >= expected else 0.0,
        "comment": f"expected>= {expected}, actual={n}",
    }


def _prereqs() -> str | None:
    if not GOLDEN_PATH.exists():
        return f"golden ausente: {GOLDEN_PATH}"
    return None


SUITE = Suite(
    name="spike_kg",
    description="Diagnóstico do KG estrutura-consciente: serialização preserva topologia + dedução de caminho",
    load_data=load_data,
    task=_task,
    evaluators=[_eval_topology_preserved, _eval_deduction_path],
    prereqs=_prereqs,
    classification="diagnostic",
    version="1",
)
