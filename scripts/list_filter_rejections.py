"""
Lê o snapshot mais recente de rejeições do filtro PME e agrupa por fonte/motivo.

Fonte: `knowledge_graph/.filter_rejections.jsonl` — substituído a cada
`build_knowledge_graph.main`. Cada linha é um JSON com:
  {logged_at, source, edital_id, title, decision, reason, deadline}

Uso típico (depois de rodar `python pipeline/build_knowledge_graph.py`):

    python scripts/list_filter_rejections.py                  # tabela agregada
    python scripts/list_filter_rejections.py --source finep
    python scripts/list_filter_rejections.py --show-titles    # lista detalhada
    python scripts/list_filter_rejections.py --decision unclear --show-titles
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import KNOWLEDGE_GRAPH_DIR  # noqa: E402

LOG_PATH = KNOWLEDGE_GRAPH_DIR / ".filter_rejections.jsonl"


def _load_rejections() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    out: list[dict] = []
    with LOG_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _aggregate(rows: list[dict]) -> dict[tuple[str, str, str], int]:
    """Agrega por (source, decision, reason_categoria) → count.

    Categoria: trunca `reason` no primeiro `:` para agrupar (ex.: `exclusor:bolsa
    de doutorado` e `exclusor:auxílio à pesquisa regular` viram `exclusor:*`).
    Mantém prefixo categórico (`programa` / `publico` / `exclusor` / `sem-sinal`).
    """
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in rows:
        source = r.get("source") or "?"
        decision = r.get("decision") or "?"
        reason = r.get("reason") or ""
        category = reason.split(":", 1)[0] if ":" in reason else reason
        counts[(source, decision, category)] += 1
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="filtra por fonte")
    parser.add_argument("--decision", default=None, choices=["reject", "unclear"],
                        help="filtra por decisão")
    parser.add_argument("--show-titles", action="store_true",
                        help="lista título+motivo de cada rejeição")
    args = parser.parse_args()

    rows = _load_rejections()
    if args.source:
        rows = [r for r in rows if r.get("source") == args.source]
    if args.decision:
        rows = [r for r in rows if r.get("decision") == args.decision]

    if not rows:
        print(f"Sem rejeições no log ({LOG_PATH.name}). Rode build_knowledge_graph primeiro.")
        return 0

    if args.show_titles:
        for r in rows:
            print(f"  [{r.get('source')}] {r.get('decision'):8} {r.get('reason'):40} "
                  f"{r.get('edital_id'):20} {r.get('title', '')[:70]}")
        print(f"\nTotal: {len(rows)} rejeições")
        return 0

    counts = _aggregate(rows)
    print(f"{'source':<10} {'decision':<10} {'categoria':<14} {'count':>6}")
    print("-" * 44)
    for (source, decision, category), n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{source:<10} {decision:<10} {category:<14} {n:>6}")
    print(f"\nTotal: {len(rows)} rejeições no snapshot atual ({LOG_PATH.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
