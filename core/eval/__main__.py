"""CLI operacional do harness de avaliação.

Exemplos:
    python -m core.eval run matching
    python -m core.eval run matching --limit 1
    python -m core.eval run all --publish
    python -m core.eval gate extraction --publish

`run` produz diagnóstico e é local por padrão. `gate` produz decisão, exige a
suíte classificada como gate e nunca aceita subconjunto. A sintaxe histórica
`python -m core.eval <suite>` continua temporariamente como alias de `run`.
"""
from __future__ import annotations

import argparse
import sys

from core.environment import load_environment_profile
from core.eval.harness import run_suite
from core.eval.registry import SUITES, get_suite


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m core.eval", description=__doc__)
    subparsers = parser.add_subparsers(dest="intent", required=True)

    run = subparsers.add_parser("run", help="executa diagnóstico; local por padrão")
    run.add_argument("suite", choices=[*SUITES, "all"])
    run.add_argument("--limit", type=int, help="roda só os N primeiros casos (debug)")
    run.add_argument("--publish", action="store_true", help="publica a rodada no Langfuse")

    gate = subparsers.add_parser("gate", help="executa decisão completa e bloqueante")
    gate.add_argument("suite", choices=list(SUITES))
    gate.add_argument("--publish", action="store_true", help="publica a rodada no Langfuse")
    return parser


def _normalize_legacy_args(argv: list[str]) -> list[str]:
    if not argv or argv[0] in {"run", "gate", "-h", "--help"}:
        return argv
    if argv[0] not in {*SUITES, "all"}:
        return argv
    print(
        "[deprecated] use `python -m core.eval run <suite>`; "
        "a sintaxe histórica será removida.",
        file=sys.stderr,
    )
    normalized = ["run", *argv]
    if "--no-push" in normalized:
        normalized.remove("--no-push")  # run já é local por padrão
    return normalized


def main(argv: list[str] | None = None) -> int:
    load_environment_profile()  # CLI standalone: perfil antes dos consumidores
    args = _parser().parse_args(_normalize_legacy_args(list(argv or sys.argv[1:])))
    names = list(SUITES) if args.suite == "all" else [args.suite]
    statuses = []
    for name in names:
        suite = get_suite(name)
        assert suite is not None
        result = run_suite(
            suite,
            intent=args.intent,
            publish=args.publish,
            limit=getattr(args, "limit", None),
        )
        statuses.append(result["status"])

    if any(status == "error" for status in statuses):
        return 2
    if any(status in {"failed", "skipped"} for status in statuses):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
