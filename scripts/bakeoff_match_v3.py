"""Bake-off offline v2 vs v3 — Fase 1.5 da spec docs/specs/v3-unified.md (GATE).

Script exploratório, NÃO um harness de produção: não registra suíte em
core/eval/registry (a suíte real do v3 é da Fase 2). NÃO altera
hypergraph_match.py nem gold.py. É leitura pura no Supabase local (a única
escrita do bake-off — o re-ingest de editais para aplicar 2 fixes de dados —
já foi feita separadamente; ver commit da Fase 1.5 no WIKI/constraints_producer).

Pipeline v3 simulado (§7 da spec):
    Stage 0 — SQL "vivo" (status/deadline)
    Stage 1 — eligibility.py (unsat elimina; unknown nunca elimina)
    Stage 2 — sum-of-max cosine (company_chunks × match_chunks)

Uso:
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
    OPENAI_API_KEY=... python scripts/bakeoff_match_v3.py --out out.json

    --cells raw,hyde,boost,threshold,hardneg,contextual   (default: todas menos contextual)
    --contextual-confirm   roda a célula contextual mesmo sem --cells listá-la explicitamente
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import unicodedata
from datetime import date
from functools import lru_cache

import numpy as np
import psycopg

from config import ROOT
from core.kg.gold import _pack_chunks
from core.retrieval.embedder import embed_texts
from core.services import eligibility
from domain.user_profile import CompanyProfile

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bakeoff")

GOLDEN_PATH = ROOT / "eval_data" / "golden" / "matching.json"
HARDNEG_PATH = ROOT / "eval_data" / "golden" / "matching_hard_negatives.json"
V2_BASELINE_PATH = ROOT / "eval_results" / "20260705_192151_matching.json"
if not V2_BASELINE_PATH.exists():
    # eval_results/ é gitignored — não existe no worktree; cai p/ o repo principal
    # (ver docs/specs/v3-unified.md Fase 1.5: reuso, não re-execução, do run v2).
    _main_repo_fallback = ROOT.parent.parent.parent / "eval_results" / "20260705_192151_matching.json"
    if _main_repo_fallback.exists():
        V2_BASELINE_PATH = _main_repo_fallback
TOP_K_SUITE = 8


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")


def _deburr(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _file_key(native_id: str) -> str:
    return native_id.replace(":", "__", 1)


# ---------------------------------------------------------------------------
# DB: entidades vivas (Stage 0) + match_chunks
# ---------------------------------------------------------------------------

def _parse_vec(raw) -> np.ndarray | None:
    """psycopg devolve `vector` como STRING crua ("[a,b,c]") — sem adapter
    pgvector registrado na conexão, não há parsing automático p/ float[]."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return np.array([float(x) for x in raw.strip("[]").split(",")], dtype=np.float64)
    return np.asarray(raw, dtype=np.float64)


def load_editais(conn) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        select id, native_id, name, status, deadline, constraints, setores
        from entities
        where kind in ('edital','programa')
        order by native_id
    """)
    cols = [d.name for d in cur.description]
    rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    for r in rows:
        r["id"] = str(r["id"])  # normaliza p/ str — chave usada em todo o resto do script
    return rows


def stage0_alive(entities: list[dict]) -> tuple[list[dict], list[dict]]:
    """SQL 'vivo' replicado em Python (mesma condição do WHERE do Stage 0)."""
    alive, dead = [], []
    today = date.today()
    for e in entities:
        ok_status = e["status"] is None or e["status"] in ("aberta", "ativa")
        ok_deadline = e["deadline"] is None or e["deadline"] >= today
        (alive if (ok_status and ok_deadline) else dead).append(e)
    return alive, dead


def load_match_chunks(conn, entity_ids: list[str]) -> dict[str, list[tuple[str, list[str], str | None, np.ndarray]]]:
    """entity_id -> [(chunk_id, section_path, kind, embedding)]. Um SELECT único."""
    if not entity_ids:
        return {}
    cur = conn.cursor()
    cur.execute(
        "select entity_id, id, section_path, kind, text, embedding from match_chunks where entity_id = any(%s)",
        (entity_ids,),
    )
    out: dict[str, list] = {}
    for eid, cid, section_path, kind, text, emb in cur.fetchall():
        out.setdefault(str(eid), []).append((str(cid), section_path, kind, text, _parse_vec(emb)))
    return out


# ---------------------------------------------------------------------------
# Golden
# ---------------------------------------------------------------------------

def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def load_hard_negatives() -> dict:
    return json.loads(HARDNEG_PATH.read_text(encoding="utf-8"))


def profile_from_dict(raw: dict) -> CompanyProfile:
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    return CompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


# ---------------------------------------------------------------------------
# Lado empresa: chunking (mesmo _pack_chunks do gold.py) + embed
# ---------------------------------------------------------------------------

def _profile_blocks(profile: CompanyProfile) -> list[dict]:
    text = profile.to_context()
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    return [{"section_path": [], "kind": "paragraph", "text": p} for p in paras]


def company_chunk_texts(profile: CompanyProfile) -> list[str]:
    blocks = _profile_blocks(profile)
    chunks = _pack_chunks(blocks)
    texts = [c["text"] for c in chunks if c["text"].strip()]
    return texts or [profile.to_context()]


# ---------------------------------------------------------------------------
# Stage 2 — sum-of-max cosine
# ---------------------------------------------------------------------------

def _cosine_matrix(company_embs: np.ndarray, entity_embs: np.ndarray) -> np.ndarray:
    """company_embs: (n_company, d); entity_embs: (n_entity, d) -> (n_company, n_entity) cosine."""
    cn = company_embs / (np.linalg.norm(company_embs, axis=1, keepdims=True) + 1e-9)
    en = entity_embs / (np.linalg.norm(entity_embs, axis=1, keepdims=True) + 1e-9)
    return cn @ en.T


def score_entities(
    company_embs: np.ndarray,
    entity_chunks: dict[str, list],  # entity_id -> [(chunk_id, section_path, kind, text, embedding)]
) -> dict[str, float]:
    """sum-of-max por chunk da empresa (família ColBERT) — nunca max global."""
    scores: dict[str, float] = {}
    for eid, chunks in entity_chunks.items():
        embs = np.array([c[4] for c in chunks if c[4] is not None])
        if embs.size == 0:
            scores[eid] = 0.0
            continue
        sims = _cosine_matrix(company_embs, embs)  # (n_company, n_chunks)
        scores[eid] = float(sims.max(axis=1).sum())
    return scores


# ---------------------------------------------------------------------------
# Stage 1 — eligibility
# ---------------------------------------------------------------------------

def stage1_filter(alive: list[dict], profile: CompanyProfile) -> tuple[list[dict], dict[str, dict]]:
    """Retorna (sobreviventes, verdicts) — verdicts[native_id] = evaluate_opportunity(...)."""
    survivors, verdicts = [], {}
    for e in alive:
        v = eligibility.evaluate_opportunity(e["constraints"], profile)
        verdicts[e["native_id"]] = v
        if v["status"] != eligibility.INELEGIVEL:
            survivors.append(e)
    return survivors, verdicts


# ---------------------------------------------------------------------------
# Setores (célula boost) — heurística barata, sem LLM
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _setor_labels() -> list[str]:
    from core.kg import schema
    return list(schema.setores_taxonomia().get("labels") or [])


def infer_company_setores(profile: CompanyProfile) -> list[str]:
    text = _deburr(" ".join([profile.one_liner or "", profile.solution_summary or "",
                              profile.descricao_atividades or ""])).lower()
    hits = [lbl for lbl in _setor_labels() if _deburr(lbl).lower() in text]
    return hits or ["Multissetorial"]


# ---------------------------------------------------------------------------
# Métricas de ranking
# ---------------------------------------------------------------------------

def rank_metrics(scores: dict[str, float], relevant_fk: set[str], neutral_fk: set[str]) -> dict:
    ranked_native = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
    ranked_fk = [_file_key(n) for n in ranked_native]

    out = {"ranked_top10": ranked_fk[:10]}
    if not relevant_fk:
        noise = [fk for fk in ranked_fk[:TOP_K_SUITE] if fk not in relevant_fk and fk not in neutral_fk]
        out["noise_at_suite_k"] = len(noise)
        return out

    for k in (5, TOP_K_SUITE, 10):
        topk = set(ranked_fk[:k])
        hit = relevant_fk & topk
        out[f"recall_at_{k}"] = round(len(hit) / len(relevant_fk), 3)

    rr = 0.0
    for i, fk in enumerate(ranked_fk, start=1):
        if fk in relevant_fk:
            rr = 1.0 / i
            break
    out["mrr"] = round(rr, 3)

    noise = [fk for fk in ranked_fk[:TOP_K_SUITE] if fk not in relevant_fk and fk not in neutral_fk]
    out["noise_at_suite_k"] = len(noise)
    miss = sorted(relevant_fk - set(ranked_fk))
    out["miss_not_in_pool"] = miss  # positivos que nem sequer sobreviveram Stage0+1
    return out


# ---------------------------------------------------------------------------
# v2 baseline (reuso do eval_results mais recente — hipergrafos v2 não existem
# mais em disco; rebuild custaria LLM$ novo só para uma comparação. Ver handoff.)
# ---------------------------------------------------------------------------

def v2_baseline(golden: dict) -> dict:
    if not V2_BASELINE_PATH.exists():
        return {"error": f"{V2_BASELINE_PATH} não encontrado"}
    data = json.loads(V2_BASELINE_PATH.read_text(encoding="utf-8"))
    cases = golden["cases"]
    neutral_fk = set(golden.get("neutral", []))
    per_case = {}
    for case, item in zip(cases, data["item_results"], strict=True):
        relevant_fk = set(case["relevant"])
        ranked_fk = item["output"].get("ranked", [])
        m: dict = {}
        if not relevant_fk:
            noise = [fk for fk in ranked_fk[:TOP_K_SUITE] if fk not in relevant_fk and fk not in neutral_fk]
            m["noise_at_suite_k"] = len(noise)
        else:
            for k in (5, TOP_K_SUITE):
                topk = set(ranked_fk[:k])
                m[f"recall_at_{k}"] = round(len(relevant_fk & topk) / len(relevant_fk), 3)
            m["recall_at_10"] = "n/d (execução armazenada limitada a top_k=8)"
            rr = 0.0
            for i, fk in enumerate(ranked_fk, start=1):
                if fk in relevant_fk:
                    rr = 1.0 / i
                    break
            m["mrr"] = round(rr, 3)
            noise = [fk for fk in ranked_fk[:TOP_K_SUITE] if fk not in relevant_fk and fk not in neutral_fk]
            m["noise_at_suite_k"] = len(noise)
        per_case[case["company"]] = m
    return {
        "source_file": str(V2_BASELINE_PATH.name),
        "note": (
            "Reexecução do v2 NÃO foi feita nesta sessão — data/knowledge_graph/hypergraphs/ "
            "está vazio (0 arquivos) neste ambiente; reconstruir custaria chamadas LLM novas "
            "(2+/chunk x ~150 editais) só para produzir um número comparável. Reusado o run "
            "mais recente já registrado (2026-07-05) sobre o MESMO golden."
        ),
        "aggregate_mean_recall_at_8_original_run": data.get("aggregate", {}).get("mean_recall_at_k"),
        "per_case": per_case,
    }


# ---------------------------------------------------------------------------
# Célula: threshold sweep (precision/recall) — pool de (case, entity) v3 cru
# ---------------------------------------------------------------------------

def threshold_sweep(pooled: list[tuple[float, int]]) -> list[dict]:
    """pooled = [(score, label)], label 1=relevante 0=negativo (neutro já excluído)."""
    if not pooled:
        return []
    scores_sorted = sorted({round(s, 4) for s, _ in pooled})
    out = []
    for thr in scores_sorted:
        tp = sum(1 for s, y in pooled if s >= thr and y == 1)
        fp = sum(1 for s, y in pooled if s >= thr and y == 0)
        fn = sum(1 for s, y in pooled if s < thr and y == 1)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        out.append({"threshold": thr, "precision": precision, "recall": recall, "tp": tp, "fp": fp, "fn": fn})
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(cells: set[str]) -> dict:
    golden = load_golden()
    neutral_fk = set(golden.get("neutral", []))
    conn = psycopg.connect(_dsn())

    entities = load_editais(conn)
    alive, dead = stage0_alive(entities)
    logger.info("Stage 0: %d/%d editais/programas vivos", len(alive), len(entities))

    report: dict = {"stage0": {"alive": len(alive), "dead": len(dead)}, "cells": {}}

    # ---- (a) Stage 0+1 recall + causas de perda -----------------------------
    stage01_rows = []
    for case in golden["cases"]:
        profile = profile_from_dict(golden["profiles"][case["company"]])
        relevant_fk = set(case["relevant"])
        if not relevant_fk:
            continue
        survivors, verdicts = stage1_filter(alive, profile)
        survivors_fk = {_file_key(e["native_id"]) for e in survivors}
        for fk in sorted(relevant_fk):
            native = fk.replace("__", ":", 1)
            entry = next((e for e in entities if e["native_id"] == native), None)
            if entry is None:
                stage01_rows.append({"company": case["company"], "edital": fk, "causa": "AUSENTE em entities (gap de dado)"})
                continue
            if fk not in {_file_key(e["native_id"]) for e in alive}:
                causa = f"Stage0 morto (status={entry['status']!r}, deadline={entry['deadline']})"
            elif fk not in survivors_fk:
                v = verdicts.get(native, {})
                causa = f"Stage1 unsat: {v.get('unsat')}"
            else:
                causa = None
            if causa:
                stage01_rows.append({"company": case["company"], "edital": fk, "causa": causa})
    total_positivos = sum(len(c["relevant"]) for c in golden["cases"])
    perdidos = len(stage01_rows)
    report["stage0_1_recall"] = {
        "total_positivos_golden": total_positivos,
        "perdidos": perdidos,
        "recall_pct": round((total_positivos - perdidos) / total_positivos, 3) if total_positivos else None,
        "detalhe": stage01_rows,
    }

    # ---- (b) ranking v3 cru + v2 baseline -----------------------------------
    if "raw" in cells:
        alive_ids = [e["id"] for e in alive]
        match_chunks = load_match_chunks(conn, alive_ids)
        alive_by_id = {e["id"]: e for e in alive}

        raw_results = {}
        pooled_for_threshold: list[tuple[float, int]] = []
        for case in golden["cases"]:
            profile = profile_from_dict(golden["profiles"][case["company"]])
            relevant_fk = set(case["relevant"])
            survivors, _ = stage1_filter(alive, profile)
            survivor_chunks = {e["id"]: match_chunks.get(e["id"], []) for e in survivors}
            texts = company_chunk_texts(profile)
            embs = np.array(embed_texts(texts))
            scores_by_id = score_entities(embs, survivor_chunks)
            scores_by_native = {alive_by_id[eid]["native_id"]: s for eid, s in scores_by_id.items()}
            m = rank_metrics(scores_by_native, relevant_fk, neutral_fk)
            raw_results[case["company"]] = m
            for eid, s in scores_by_id.items():
                fk = _file_key(alive_by_id[eid]["native_id"])
                if fk in neutral_fk:
                    continue
                pooled_for_threshold.append((s, 1 if fk in relevant_fk else 0))
        report["cells"]["v3_raw"] = raw_results
        report["cells"]["v2_baseline"] = v2_baseline(golden)

        if "threshold" in cells:
            report["cells"]["threshold_sweep"] = threshold_sweep(pooled_for_threshold)

        if "boost" in cells:
            boost_results = {}
            for case in golden["cases"]:
                profile = profile_from_dict(golden["profiles"][case["company"]])
                relevant_fk = set(case["relevant"])
                survivors, _ = stage1_filter(alive, profile)
                survivor_chunks = {e["id"]: match_chunks.get(e["id"], []) for e in survivors}
                texts = company_chunk_texts(profile)
                embs = np.array(embed_texts(texts))
                scores_by_id = score_entities(embs, survivor_chunks)
                company_setores = set(infer_company_setores(profile))
                boosted = {}
                for eid, s in scores_by_id.items():
                    ent_setores = set(alive_by_id[eid].get("setores") or [])
                    boosted[eid] = s * 1.1 if (company_setores & ent_setores) else s
                scores_by_native = {alive_by_id[eid]["native_id"]: s for eid, s in boosted.items()}
                boost_results[case["company"]] = rank_metrics(scores_by_native, relevant_fk, neutral_fk)
                boost_results[case["company"]]["company_setores_inferidos"] = sorted(company_setores)
            report["cells"]["v3_boost_setores"] = boost_results

        if "hyde" in cells:
            from core.retrieval.hyde import generate_hyde_doc
            hyde_results = {}
            for case in golden["cases"]:
                profile = profile_from_dict(golden["profiles"][case["company"]])
                relevant_fk = set(case["relevant"])
                survivors, _ = stage1_filter(alive, profile)
                survivor_chunks = {e["id"]: match_chunks.get(e["id"], []) for e in survivors}
                query = profile.one_liner or profile.solution_summary or profile.nome
                hyde_doc = generate_hyde_doc(query)
                texts = [hyde_doc] if hyde_doc else company_chunk_texts(profile)
                embs = np.array(embed_texts(texts))
                scores_by_id = score_entities(embs, survivor_chunks)
                scores_by_native = {alive_by_id[eid]["native_id"]: s for eid, s in scores_by_id.items()}
                hyde_results[case["company"]] = rank_metrics(scores_by_native, relevant_fk, neutral_fk)
                hyde_results[case["company"]]["hyde_fallback_cru"] = not bool(hyde_doc)
            report["cells"]["v3_hyde"] = hyde_results

    # ---- (c) contextual (custo — só editais referenciados no golden) -------
    if "contextual" in cells:
        from core.contextual_retrieval import contextualize_chunks

        referenced_fk = set(neutral_fk)
        for case in golden["cases"]:
            referenced_fk |= set(case["relevant"])
        referenced_native = [fk.replace("__", ":", 1) for fk in referenced_fk]
        ref_entities = [e for e in entities if e["native_id"] in referenced_native]
        ref_ids = [e["id"] for e in ref_entities]
        ref_chunks = load_match_chunks(conn, ref_ids)
        n_chunks = sum(len(v) for v in ref_chunks.values())
        logger.warning("contextual: %d editais referenciados, %d chunks (1 LLM call/chunk)", len(ref_ids), n_chunks)

        contextual_embs: dict[str, list] = {}
        for eid, chunks in ref_chunks.items():
            blocks = [{"section_path": sp, "kind": k, "text": t} for _cid, sp, k, t, _e in chunks]
            ctx_texts = contextualize_chunks(blocks)
            new_embs = embed_texts(ctx_texts)
            contextual_embs[eid] = [
                (c[0], c[1], c[2], c[3], np.array(ne)) for c, ne in zip(chunks, new_embs, strict=True)
            ]

        alive_by_id = {e["id"]: e for e in alive}
        ctx_results = {}
        for case in golden["cases"]:
            profile = profile_from_dict(golden["profiles"][case["company"]])
            relevant_fk = set(case["relevant"])
            survivors, _ = stage1_filter(alive, profile)
            survivor_ids = {e["id"] for e in survivors} & set(contextual_embs.keys())
            survivor_chunks = {eid: contextual_embs[eid] for eid in survivor_ids}
            texts = company_chunk_texts(profile)
            embs = np.array(embed_texts(texts))
            scores_by_id = score_entities(embs, survivor_chunks)
            scores_by_native = {alive_by_id[eid]["native_id"]: s for eid, s in scores_by_id.items()}
            ctx_results[case["company"]] = rank_metrics(scores_by_native, relevant_fk, neutral_fk)
        report["cells"]["v3_contextual"] = ctx_results
        report["cells"]["contextual_cost_estimate"] = {"n_editais": len(ref_ids), "n_chunks": n_chunks}

    # ---- (d) hard negatives --------------------------------------------------
    if "hardneg" in cells:
        hn = load_hard_negatives()
        results = []
        entities_by_native = {e["native_id"]: e for e in entities}
        for case in hn["cases"]:
            if "profile_ref" in case:
                profile = profile_from_dict(golden["profiles"][case["profile_ref"]])
            else:
                profile = profile_from_dict(case["profile"])
            entry = entities_by_native.get(case["edital"])
            if entry is None:
                results.append({"id": case["id"], "error": f"edital {case['edital']} não encontrado"})
                continue
            v = eligibility.evaluate_opportunity(entry["constraints"], profile)
            results.append({
                "id": case["id"],
                "edital": case["edital"],
                "expected": case["expected_stage1"],
                "got_status": v["status"],
                "unsat": v["unsat"],
                "unknown": v["unknown"],
                "pass": (v["status"] == eligibility.INELEGIVEL) == (case["expected_stage1"] == "inelegivel"),
            })
        report["cells"]["hard_negatives"] = results

    conn.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Bake-off offline v2 vs v3 (Fase 1.5, GATE)")
    ap.add_argument("--cells", default="raw,boost,hyde,threshold,hardneg",
                     help="raw,boost,hyde,threshold,hardneg,contextual (vírgula)")
    ap.add_argument("--out", default=None, help="path do JSON de saída (default: stdout)")
    args = ap.parse_args()
    cells = set(c.strip() for c in args.cells.split(",") if c.strip())
    report = run(cells)
    out_str = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(out_str, encoding="utf-8")
        print(f"escrito em {args.out}")
    else:
        print(out_str)


if __name__ == "__main__":
    main()
