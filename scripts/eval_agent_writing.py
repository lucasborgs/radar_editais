#!/usr/bin/env python3
"""
Eval harness do agente de escrita (Front 1.5).

Habilitador da aposentadoria do legacy (Front 1): roda o agente sobre um
conjunto fixo de (perfil, edital, instrução de seção) e mede a rúbrica de
seção, estabelecendo o BASELINE que o path agente precisa igualar/superar
para justificar remover o 1-shot legacy.

Métricas por caso (rúbrica única da spec):
  • nº de afirmações sobre o edital + % com respaldo em chunk (grounding)
  • nº de erros factuais (juiz offline — mesma lente do critic)
  • conclusão do save (o agente persistiu a seção em ≤ N turnos?)
  • coerência interna (0 contradições entre seções do doc final)

Métricas operacionais auxiliares: latência por caso, nº de tool calls.

Pré-requisitos para rodar de verdade (toca DB + OpenAI/Anthropic):
  • OPENAI_API_KEY (juízes + agente no fallback OpenAI) e, idealmente,
    ANTHROPIC_API_KEY (agente Anthropic).
  • DATABASE_URL + Supabase service env (retrieval de chunks + persistência).
  • Editais da fixture indexados em edital_chunks (scripts/reindex_edital.py).
  • Um workspace de eval (--workspace UUID) para as sessões.

Uso:
    python scripts/eval_agent_writing.py --workspace <uuid> \
        --cases tests/fixtures/eval_cases.json --out eval_writing.json
    python scripts/eval_agent_writing.py --report eval_writing.json   # só re-imprime
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# CLI standalone: carrega .env antes dos imports que leem credenciais.
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval_agent_writing")


def _load_cases(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "cases" not in data or "profiles" not in data:
        raise ValueError("fixture inválida: precisa de chaves 'profiles' e 'cases'")
    return data


def _build_profile(raw: dict):
    from domain.user_profile import CompanyProfile
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    return CompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


def _run_case(db, workspace_id: str, profile, case: dict, max_turns: int) -> dict:
    """Roda um caso fim-a-fim e devolve o dict de métricas."""
    from core.agent_tools.critic_agent import _build_proposal_context  # reuso
    from core.retriever import format_chunks_for_prompt, retrieve_chunks
    from core.temporal import render_temporal_block
    from core.writing_eval import (
        extract_edital_claims,
        judge_factual_errors,
        score_grounding,
    )
    from core.writing_session import WritingSession

    edital_id = case["edital_id"]
    instruction = case["instruction"]
    section_hint = case.get("section")

    session = WritingSession(
        db=db, workspace_id=workspace_id, profile=profile, edital_id=edital_id,
    )
    # turn() roda sempre o agente (Front 1: legacy aposentado) — nada a forçar.

    saved_title: str | None = None
    turns_to_save: int | None = None
    n_tool_calls = 0
    t0 = time.monotonic()

    for t in range(1, max_turns + 1):
        before = dict(session._doc_sections)
        msg = instruction if t == 1 else (
            "Finalize e salve o rascunho desta seção com save_draft."
        )
        result = session.turn(msg, section_hint=section_hint)
        n_tool_calls += len(result.get("tool_trace") or [])
        # Detecta save por mudança em _doc_sections (robusto a título exato).
        changed = [k for k, v in session._doc_sections.items() if before.get(k) != v]
        if changed:
            saved_title = changed[-1]
            turns_to_save = t
            break

    latency_ms = (time.monotonic() - t0) * 1000.0

    saved = saved_title is not None
    draft = session._doc_sections.get(saved_title, "") if saved else ""

    # Chunks do edital para grounding + juiz factual (mesma query do critic).
    try:
        chunks = retrieve_chunks(
            db, session._scope_edital_ids, query=(draft[:500] or section_hint or ""), k=5,
        )
        edital_context = format_chunks_for_prompt(chunks, edital_ids=session._scope_edital_ids)
    except Exception as e:
        logger.warning("[%s] retrieve_chunks falhou: %s", case["id"], e)
        chunks, edital_context = [], ""

    claims = extract_edital_claims(draft) if draft else []

    # Grounding por-claim: a evidência de cada afirmação é recuperada com a
    # PRÓPRIA afirmação como query, restrita ao edital PRIMÁRIO (não análogos)
    # — uma claim sobre o edital desta proposta só conta como grounded se um
    # chunk DESTE edital a sustenta.
    def _retrieve_for_claim(query: str, k: int) -> list[dict]:
        return retrieve_chunks(db, [edital_id], query=query, k=k)

    grounding = score_grounding(claims, retrieve_fn=_retrieve_for_claim, k=5)
    temporal_block = render_temporal_block(edital_id)
    proposal_context = _build_proposal_context(session, saved_title or section_hint or "")
    errors = judge_factual_errors(
        saved_title or section_hint or "", draft, edital_context,
        proposal_context, temporal_block,
    ) if draft else []

    return {
        "case_id": case["id"],
        "edital_id": edital_id,
        "section": saved_title or section_hint,
        "saved": saved,
        "turns_to_save": turns_to_save,
        "n_tool_calls": n_tool_calls,
        "latency_ms": round(latency_ms, 1),
        "n_claims": grounding.n_claims,
        "n_grounded": grounding.n_grounded,
        "pct_grounded": round(grounding.pct_grounded, 3),
        "n_factual_errors": len(errors),
        "factual_errors": errors,
        "draft_chars": len(draft),
        "_session_id": session.session_id,
        "_sections_snapshot": dict(session._doc_sections),
    }


def _print_report(results: list[dict], aggregate: dict) -> None:
    print("\n=== EVAL DE ESCRITA — POR CASO ===")
    header = f"{'caso':<34} {'save':>4} {'turns':>5} {'claims':>6} {'%grnd':>6} {'errs':>4} {'lat(ms)':>8}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['case_id']:<34} "
            f"{'sim' if r['saved'] else 'NÃO':>4} "
            f"{str(r['turns_to_save'] or '-'):>5} "
            f"{r['n_claims']:>6} "
            f"{r['pct_grounded'] * 100:>5.0f}% "
            f"{r['n_factual_errors']:>4} "
            f"{r['latency_ms']:>8.0f}"
        )
    print("\n=== AGREGADO (BASELINE) ===")
    for k, v in aggregate.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="tests/fixtures/eval_cases.json")
    parser.add_argument("--workspace", help="UUID do workspace de eval (sessões)")
    parser.add_argument("--out", default="eval_writing.json")
    parser.add_argument("--max-turns", type=int, default=4,
                        help="Máx de turnos por caso para o agente salvar a seção")
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="Pausa (s) entre casos — drena a janela de TPM da "
                             "OpenAI em tiers de rate limit baixo")
    parser.add_argument("--report", help="Re-imprime um resultado salvo (sem rodar)")
    args = parser.parse_args()

    if args.report:
        payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
        _print_report(payload["results"], payload["aggregate"])
        return 0

    if not args.workspace:
        print("ERRO: --workspace <uuid> é obrigatório para rodar o eval.", file=sys.stderr)
        return 2

    from core.db import get_supabase_service
    from core.writing_eval import aggregate_writing_runs, judge_internal_coherence

    data = _load_cases(Path(args.cases))
    db = get_supabase_service()

    import time as _time

    results: list[dict] = []
    for i, case in enumerate(data["cases"]):
        profile_raw = data["profiles"].get(case["profile"])
        if profile_raw is None:
            logger.warning("caso %s referencia perfil inexistente '%s' — pulando",
                           case["id"], case["profile"])
            continue
        if i > 0 and args.sleep > 0:
            print(f"  (pausa {args.sleep:.0f}s entre casos)...", file=sys.stderr)
            _time.sleep(args.sleep)
        profile = _build_profile(profile_raw)
        print(f"→ rodando {case['id']} (edital={case['edital_id']})...", file=sys.stderr)
        try:
            r = _run_case(db, args.workspace, profile, case, args.max_turns)
        except Exception as e:
            logger.error("caso %s falhou: %s", case["id"], e)
            r = {"case_id": case["id"], "edital_id": case["edital_id"],
                 "saved": False, "error": str(e), "n_claims": 0, "n_grounded": 0,
                 "n_factual_errors": 0}
        results.append(r)

    # Coerência interna: por sessão (doc final). Roda 1× por caso salvo.
    for r in results:
        snap = r.pop("_sections_snapshot", None)
        if snap:
            coh = judge_internal_coherence(snap)
            r["coherent"] = coh.coherent
            r["contradictions"] = coh.contradictions

    aggregate = aggregate_writing_runs(results)
    Path(args.out).write_text(
        json.dumps({"results": results, "aggregate": aggregate}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_report(results, aggregate)
    print(f"\nResultados salvos em {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
