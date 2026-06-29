#!/usr/bin/env python3
"""Gate do Sprint 0 (arquitetura hipergrado).

Roda o Hyper-Extract de produção sobre TODO o corpus silver
(`data/silver/structured_docs/{source}/*.jsonl`), grava cada hipergrado em
`data/knowledge_graph/hypergraphs/{edital_id}.json` e agrega as estatísticas que
definem o critério de saída do Sprint 0:

  * `Outro`   == 0% dos nós (tipo removido do schema; LLM forçado a tipar)
  * `Edital`  colapso 1:1 — nº de nós Edital == nº de editais processados
    (o tipo é FIXO por design via _collapse_editais; um piso % seria a unidade
    errada — a fração cai conforme o grafo enriquece, penalizando grafo rico)
  * `Aplicação` presente (tipo central para match cross-domínio)
  * prazo/status extraídos como propriedade do nó `Edital` (amostra)

Uso:
    .venv/bin/python3.14 scripts/hyper_extract_gate.py
    .venv/bin/python3.14 scripts/hyper_extract_gate.py --limit 3 --source fapesp
    .venv/bin/python3.14 scripts/hyper_extract_gate.py --max-chars 30000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.retrieval import hyper_extractor as hx  # noqa: E402

REPORT_PATH = ROOT / "data" / "hyper_extract_output_v2" / "gate_report.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="máx. de editais (0 = todos)")
    ap.add_argument("--source", default="", help="filtra por fonte (finep/fapesp/fapesc)")
    ap.add_argument("--exclude", default="web", help="fontes a pular (CSV); default=web (staging Discovery)")
    ap.add_argument("--no-catalogs", action="store_true", help="pula ICT/investidor/programa")
    ap.add_argument("--catalogs-only", action="store_true", help="roda só os catálogos (sem editais)")
    ap.add_argument("--max-chars", type=int, default=0, help="override do teto de chars/edital")
    args = ap.parse_args()

    if args.max_chars:
        hx.HYPER_EXTRACT_MAX_CHARS = args.max_chars

    excluded = {s.strip() for s in args.exclude.split(",") if s.strip()}
    editais = list(hx.iter_silver_editais())
    if args.source:
        editais = [t for t in editais if t[0] == args.source]
    else:
        editais = [t for t in editais if t[0] not in excluded]
    if args.limit:
        editais = editais[: args.limit]
    if args.catalogs_only:
        editais = []

    print(f"Modelo: {hx.HYPER_EXTRACT_MODEL} | max_chars: {hx.HYPER_EXTRACT_MAX_CHARS}")
    print(f"Editais a processar: {len(editais)}\n")

    node_types: Counter = Counter()
    edge_types: Counter = Counter()
    total_nodes = 0
    total_edges = 0
    edital_samples: list[dict] = []
    per_edital: list[dict] = []
    t0 = time.time()

    for i, (source, edital_id, path) in enumerate(editais, 1):
        e_t0 = time.time()
        try:
            res = hx.run_hyper_extract(path, edital_id, source=source, build_index=False, write=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(editais)}] {source}/{edital_id}  ERRO: {exc}")
            per_edital.append({"source": source, "edital_id": edital_id, "error": str(exc)})
            continue

        node_types.update(res.node_type_counts)
        edge_types.update(res.edge_type_counts)
        total_nodes += len(res.nodes)
        total_edges += len(res.edges)
        for ed in res.editais:
            edital_samples.append({
                "edital_id": edital_id, "name": ed.get("name"),
                "prazo": ed.get("prazo"), "status": ed.get("status"),
                "valor": ed.get("valor"), "fonte": ed.get("fonte"),
            })
        per_edital.append({
            "source": source, "edital_id": edital_id,
            "nodes": len(res.nodes), "edges": len(res.edges),
            "editais": len(res.editais),
        })
        dt = time.time() - e_t0
        print(
            f"  [{i}/{len(editais)}] {source}/{edital_id}  "
            f"nós={len(res.nodes):3d} arestas={len(res.edges):3d} "
            f"editais={len(res.editais)} ({dt:.0f}s)"
        )

    # ── agregação ──────────────────────────────────────────────────────────
    def pct(c: int) -> float:
        return 100.0 * c / total_nodes if total_nodes else 0.0

    print("\n" + "=" * 60)
    print(f"TOTAL  nós={total_nodes}  arestas={total_edges}  "
          f"({time.time() - t0:.0f}s)")
    print("\nDistribuição de tipos de nó:")
    for t, c in node_types.most_common():
        print(f"  {t:14s} {c:4d}  {pct(c):5.1f}%")
    print("\nDistribuição de tipos de aresta:")
    for t, c in edge_types.most_common():
        print(f"  {t:14s} {c:4d}")

    # ── veredito do gate ───────────────────────────────────────────────────
    outro_pct = pct(node_types.get("Outro", 0))
    edital_pct = pct(node_types.get("Edital", 0))
    aplic_pct = pct(node_types.get("Aplicação", 0))
    with_prazo = sum(1 for e in edital_samples if e["prazo"])
    with_status = sum(1 for e in edital_samples if e["status"])
    n_editais = len(edital_samples)
    # Colapso 1:1: cada edital processado COM SUCESSO vira exatamente 1 nó Edital.
    # Pega perda (0 nós p/ um edital) ou duplicação (colapso falho). O nº de nós
    # Edital é fixo por design, então um piso percentual cairia com grafo rico.
    edital_nodes = node_types.get("Edital", 0)
    successful = sum(1 for e in per_edital if "error" not in e)

    checks = {
        "Outro < 5%": outro_pct < 5.0,
        "Edital 1:1 (nós == editais)": edital_nodes >= 1 and edital_nodes == successful,
        "Aplicação presente": aplic_pct > 0.0,
        "prazo extraído (>=1 Edital)": with_prazo >= 1,
        "status extraído (>=1 Edital)": with_status >= 1,
    }

    print("\n" + "=" * 60)
    print("GATE Sprint 0:")
    print(f"  Outro       : {outro_pct:.1f}%")
    print(f"  Edital 1:1  : {edital_nodes} nós / {successful} editais  ({edital_pct:.1f}%)")
    print(f"  Aplicação   : {aplic_pct:.1f}%")
    print(f"  nós Edital   : {n_editais}  (prazo: {with_prazo}, status: {with_status})")
    print()
    all_pass = True
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        all_pass = all_pass and ok

    print("\nAmostra de nós Edital (prazo / status / valor / fonte):")
    for e in edital_samples[:15]:
        print(f"  {e['edital_id']:>10} {str(e['name'])[:38]:38} | "
              f"{str(e['prazo'])[:12]:12} | {str(e['status'])[:10]:10} | "
              f"{str(e['valor'])[:18]:18} | {e['fonte']}")

    # ── catálogos do ecossistema (ICT / investidor / programa) ─────────────────
    catalog_stats: dict = {}
    # tipo de nó primário esperado por catálogo (sanity-check soft)
    primary_by_catalog = {"ict": "ICT", "investidores": "Investidor", "programas": "Programa"}
    if not args.no_catalogs:
        print("\n" + "=" * 60)
        print("CATÁLOGOS DO ECOSSISTEMA:")
        for graph_id, text in hx.iter_catalogs():
            c_t0 = time.time()
            try:
                res = hx.run_hyper_extract_catalog(text, graph_id, write=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  {graph_id}: ERRO {exc}")
                catalog_stats[graph_id] = {"error": str(exc)}
                continue
            nt = res.node_type_counts
            primary = primary_by_catalog.get(graph_id, "")
            print(f"  {graph_id}: nós={len(res.nodes)} arestas={len(res.edges)} "
                  f"({time.time() - c_t0:.0f}s) | {primary}={nt.get(primary, 0)}")
            print(f"      tipos: {dict(nt.most_common())}")
            catalog_stats[graph_id] = {
                "nodes": len(res.nodes), "edges": len(res.edges),
                "node_types": dict(nt), "primary": primary,
                "primary_count": nt.get(primary, 0),
            }
        # check soft: cada catálogo rendeu ao menos 1 nó do seu tipo primário
        for gid, st in catalog_stats.items():
            ok = st.get("primary_count", 0) >= 1
            print(f"  [{'PASS' if ok else 'WARN'}] {gid}: {st.get('primary')} "
                  f">= 1 ({st.get('primary_count', 0)})")

    report = {
        "model": hx.HYPER_EXTRACT_MODEL,
        "max_chars": hx.HYPER_EXTRACT_MAX_CHARS,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "node_types": dict(node_types),
        "edge_types": dict(edge_types),
        "gate": {k: bool(v) for k, v in checks.items()},
        "gate_pass": all_pass,
        "edital_samples": edital_samples,
        "per_edital": per_edital,
        "catalogs": catalog_stats,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRelatório → {REPORT_PATH}")
    print(f"\n{'>>> GATE PASSOU' if all_pass else '>>> GATE FALHOU'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
