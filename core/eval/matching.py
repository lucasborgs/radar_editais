"""Suíte de avaliação do matching — agora sobre o HIPERGRADO (Sprint 1 F3).

Roda `find_matching_editais` (match cross-domínio geométrico) por empresa contra o
golden de AFINIDADE DE CONTEÚDO em `eval_data/golden/matching.json`. Mede:

  recall@k  — quantos positivos do golden aparecem no top-k (sinal: o nicho da
              empresa é encontrado?). `None` p/ empresas-controle (sem positivos).
  noise     — matches no top-k que NÃO são positivos NEM neutros (guarda-chuva) =
              falso-positivo. fintech (controle) deve ter ruído baixo.

Critério = afinidade (Tema/Tec/Aplicação); elegibilidade dura é camada SEPARADA, fora
deste golden (ver core/services/hypergraph_match.AFFINITY_TYPES). Gate p/ remover o
HybridMatch legado + calibrar o threshold. NÃO há LLM no julgamento — o match é
geométrico; a única LLM é a extração one-shot dos nós da empresa.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "eval_data" / "golden" / "matching.json"
TOP_K = 8  # o produto exibe ~8 (find_matching_editais/tool default)


@lru_cache(maxsize=1)
def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _extract_nodes(profile_raw: dict) -> list[dict]:
    from core.retrieval.hyper_extractor import run_hyper_extract_company
    from domain.user_profile import CompanyProfile
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    prof = CompanyProfile(**{k: v for k, v in profile_raw.items() if k in allowed})
    return run_hyper_extract_company(prof.to_context(), company_id="eval").nodes


# ---------------------------------------------------------------------------
# Suíte
# ---------------------------------------------------------------------------

def load_data() -> list[dict]:
    g = _golden()
    profiles, neutral = g["profiles"], g["neutral"]
    frozen = g.get("frozen_nodes", {})
    items = []
    for case in g["cases"]:
        company = case["company"]
        raw = profiles.get(company)
        if raw is None:
            continue
        items.append({
            # `nodes` congelados (extraídos 1x) tornam o gate DETERMINÍSTICO e sem LLM:
            # isola o MOTOR da variação de extração (que tem suíte própria). Ausentes →
            # task re-extrai. Re-congelar = rodar scripts/freeze_matching_nodes ou editar.
            "input": {"profile": raw, "nodes": frozen.get(company), "top_k": TOP_K},
            "expected_output": {"relevant": case["relevant"], "neutral": neutral},
            "metadata": {"case_id": company},
        })
    return items


def task(*, item: Any, **_) -> dict:
    from core.services import hypergraph_match
    inp = get_input(item)
    nodes = inp.get("nodes") or _extract_nodes(inp["profile"])
    if not nodes:
        return {"error": "perfil não gerou nós", "ranked": []}
    matches = hypergraph_match.find_matching_editais(nodes, top_k=inp.get("top_k", TOP_K))
    return {
        "ranked": [m.file_key for m in matches],
        "matches": [{"file_key": m.file_key, "score": round(m.score, 3),
                     "name": m.name[:60], "n_paths": m.n_paths} for m in matches],
    }


def eval_recall(*, output, expected_output, **_) -> Evaluation | None:
    """recall@k = positivos do golden presentes no top-k. None p/ controle (sem positivos)."""
    relevant = set((expected_output or {}).get("relevant", []))
    if not relevant:
        return None
    ranked = set(output.get("ranked", [])) if isinstance(output, dict) else set()
    hit = relevant & ranked
    miss = sorted(relevant - ranked)
    return {"name": "recall_at_k", "value": round(len(hit) / len(relevant), 3),
            "comment": f"miss={miss}" if miss else "todos no top-k"}


def eval_noise(*, output, expected_output, **_) -> Evaluation:
    """Falso-positivo: matches no top-k fora de positivos E de neutros (guarda-chuva)."""
    exp = expected_output or {}
    relevant, neutral = set(exp.get("relevant", [])), set(exp.get("neutral", []))
    ranked = output.get("ranked", []) if isinstance(output, dict) else []
    noise = [fk for fk in ranked if fk not in relevant and fk not in neutral]
    return {"name": "noise", "value": len(noise), "comment": f"fp={noise}" if noise else ""}


def _prereqs() -> str | None:
    import os
    if not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY (embedding dos nós; nós congelados → sem extração)"
    from core.retrieval.hyper_extractor import HYPERGRAPHS_DIR
    if not any(HYPERGRAPHS_DIR.glob("*__*.json")):
        return "hipergrados do ecossistema ausentes — rode o extractor de produção"
    return None


SUITE = Suite(
    name="matching",
    description="recall@k do match por hipergrado (afinidade de conteúdo) + ruído, vs golden curado.",
    load_data=load_data,
    task=task,
    evaluators=[eval_recall, eval_noise],
    prereqs=_prereqs,
)
