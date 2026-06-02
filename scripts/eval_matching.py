#!/usr/bin/env python3
"""
Eval harness do matching (Front 2).

Nunca validamos se o `/match` rankeia bem — é o topo do funil; se erra o
edital, todo o resto (brief, escrita) é desperdício. Este script roda o
HybridMatch para perfis-semente (iFlorestal + sintéticos contrastantes),
julga o top-K com a rúbrica única (fit temático 0-2, elegibilidade 0-2,
vigência sim/não) e reporta precisão@K + se o edital esperado aparece no topo.

Cobre também os endurecimentos do Front 2:
  • vigência: nenhum expirado deve aparecer no top-K (conta violações).
  • fallback "nenhum elegível": resultados com eligible=False são contados
    como inelegíveis no top-K (sinal, não máscara).

Pré-requisitos: OPENAI_API_KEY (juiz + Stage 2), índice do KG construído
(knowledge_graph/index.json + wiki pages). NÃO precisa de Supabase/DB.

Uso:
    python scripts/eval_matching.py --cases tests/fixtures/eval_matching.json \
        --top-k 5 --out eval_matching_results.json
    python scripts/eval_matching.py --report eval_matching_results.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# CLI standalone: carrega .env antes dos imports que leem credenciais.
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval_matching")


def _build_profile(raw: dict):
    from domain.user_profile import CompanyProfile
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    return CompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


def _edital_summary(svc, edital_id: str) -> str:
    """Resumo enxuto do edital para o juiz da rúbrica."""
    card = svc.get_edital_by_id(edital_id) or {}
    parts = [f"id: {edital_id}", f"título: {card.get('title', '')}"]
    if card.get("objective"):
        parts.append(f"objetivo: {card['objective']}")
    if card.get("themes"):
        parts.append(f"temas: {', '.join(card.get('themes') or [])}")
    if card.get("eligible_entities"):
        parts.append(f"público-alvo: {', '.join(card.get('eligible_entities') or [])}")
    if card.get("mechanism"):
        parts.append(f"mecanismo: {card['mechanism']}")
    if card.get("trl_range"):
        parts.append(f"TRL: {card['trl_range']}")
    if card.get("deadline"):
        parts.append(f"deadline: {card['deadline']}")
    return "\n".join(parts)


def _run_case(svc, profile, case: dict, top_k: int) -> dict:
    from core.matching_eval import (
        RubricVerdict,
        expected_in_top,
        judge_match_rubric,
        precision_at_k,
    )
    from core.temporal import temporal_context

    matches = svc.match(profile, top_k=top_k)
    profile_context = profile.to_context()
    result_ids = [m["id"] for m in matches]

    verdicts: list[RubricVerdict] = []
    n_expired = 0
    n_ineligible = 0
    rows = []
    for m in matches:
        eid = m["id"]
        ctx = temporal_context(eid)
        vigente = not (ctx.expired if ctx else False)
        if not vigente:
            n_expired += 1
        if m.get("eligible") is False:
            n_ineligible += 1
        fit, elig, rationale = judge_match_rubric(profile_context, _edital_summary(svc, eid))
        v = RubricVerdict(fit_tematico=fit, elegibilidade=elig, vigente=vigente, rationale=rationale)
        verdicts.append(v)
        rows.append({
            "edital_id": eid, "score": m.get("score"),
            "eligible_flag": m.get("eligible", True),
            "fit_tematico": fit, "elegibilidade": elig, "vigente": vigente,
            "is_hit": v.is_hit(), "rationale": rationale,
        })

    expected = case.get("expected_top", []) or []
    n_expected = case.get("expected_top_n", 3)
    expected_hit = (
        all(expected_in_top(result_ids, e, n_expected) for e in expected)
        if expected else None
    )

    out = {
        "case_id": case["id"],
        "n_results": len(matches),
        "precision_at_3": round(precision_at_k(verdicts, 3), 3),
        "precision_at_5": round(precision_at_k(verdicts, 5), 3),
        "n_expired_in_topk": n_expired,
        "n_ineligible_in_topk": n_ineligible,
        "result_ids": result_ids,
        "rows": rows,
    }
    if expected_hit is not None:
        out["expected_hit"] = expected_hit
        out["expected_top"] = expected
    return out


def _print_report(results: list[dict], aggregate: dict) -> None:
    print("\n=== EVAL DE MATCHING — POR PERFIL ===")
    header = f"{'caso':<20} {'P@3':>5} {'P@5':>5} {'exp':>4} {'exp?':>5} {'expir':>6} {'inel':>5}"
    print(header)
    print("-" * len(header))
    for r in results:
        exp = r.get("expected_hit")
        exp_s = "—" if exp is None else ("sim" if exp else "NÃO")
        print(
            f"{r['case_id']:<20} "
            f"{r['precision_at_3']:>5.2f} "
            f"{r['precision_at_5']:>5.2f} "
            f"{len(r.get('expected_top', [])):>4} "
            f"{exp_s:>5} "
            f"{r['n_expired_in_topk']:>6} "
            f"{r['n_ineligible_in_topk']:>5}"
        )
    print("\n=== AGREGADO ===")
    for k, v in aggregate.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")
    total_expired = aggregate.get("total_expired_in_topk", 0)
    if total_expired:
        print(f"\n⚠️  {total_expired} edital(is) EXPIRADO(s) no top-K — vigência violada!")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="tests/fixtures/eval_matching.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out", default="eval_matching_results.json")
    parser.add_argument("--report", help="Re-imprime um resultado salvo (sem rodar)")
    args = parser.parse_args()

    if args.report:
        payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
        _print_report(payload["results"], payload["aggregate"])
        return 0

    from core.hybrid_match_service import HybridMatchService
    from core.matching_eval import aggregate_matching_runs

    data = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    svc = HybridMatchService()

    results: list[dict] = []
    for case in data["cases"]:
        profile_raw = data["profiles"].get(case["profile"])
        if profile_raw is None:
            logger.warning("caso %s: perfil '%s' inexistente — pulando", case["id"], case["profile"])
            continue
        profile = _build_profile(profile_raw)
        print(f"→ rodando match para {case['id']}...", file=sys.stderr)
        try:
            results.append(_run_case(svc, profile, case, args.top_k))
        except Exception as e:
            logger.error("caso %s falhou: %s", case["id"], e)
            results.append({"case_id": case["id"], "error": str(e),
                            "precision_at_3": 0.0, "precision_at_5": 0.0})

    aggregate = aggregate_matching_runs(results)
    Path(args.out).write_text(
        json.dumps({"results": results, "aggregate": aggregate}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_report(results, aggregate)
    print(f"\nResultados salvos em {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
