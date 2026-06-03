"""CLI do harness de avaliação: `python -m core.eval <suite>`.

Exemplos:
    python -m core.eval matching            # roda a suíte de matching
    python -m core.eval all                 # todas as suítes registradas
    python -m core.eval matching --no-push   # força fallback local (ignora Langfuse)
    python -m core.eval matching --limit 1   # subconjunto (debug)

Com LANGFUSE_PUBLIC_KEY/SECRET_KEY no ambiente, cada rodada vira um Experiment
no Langfuse (scores comparáveis entre commits, link na UI). Sem elas, grava
eval_results/<ts>_<suite>.json localmente.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from core.eval.harness import run_suite
from core.eval.registry import SUITES, get_suite


def main() -> int:
    load_dotenv()  # CLI standalone: credenciais antes dos imports que as leem
    parser = argparse.ArgumentParser(prog="python -m core.eval", description=__doc__)
    parser.add_argument("suite", choices=[*SUITES, "all"], help="suíte a rodar ou 'all'")
    parser.add_argument("--no-push", action="store_true",
                        help="força o fallback local mesmo com Langfuse configurado")
    parser.add_argument("--limit", type=int, help="roda só os N primeiros casos")
    args = parser.parse_args()

    names = list(SUITES) if args.suite == "all" else [args.suite]
    skipped = 0
    for name in names:
        suite = get_suite(name)
        assert suite is not None
        result = run_suite(suite, push=not args.no_push, limit=args.limit)
        if result.get("skipped"):
            skipped += 1
    return 1 if skipped == len(names) else 0


if __name__ == "__main__":
    sys.exit(main())
