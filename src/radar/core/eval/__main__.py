"""CLI operacional do harness de avaliação.

Exemplos:
    python -m radar.core.eval run matching
    python -m radar.core.eval run matching --limit 1
    python -m radar.core.eval run all --publish
    python -m radar.core.eval gate extraction --publish

`run` produz diagnóstico e é local por padrão. `gate` produz decisão, exige a
suíte classificada como gate e nunca aceita subconjunto. A sintaxe histórica
`python -m radar.core.eval <suite>` continua temporariamente como alias de `run`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from radar.core.environment import load_environment_profile
from radar.core.eval.harness import run_suite
from radar.core.eval.registry import SUITES, get_suite


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m radar.core.eval", description=__doc__)
    subparsers = parser.add_subparsers(dest="intent", required=True)

    run = subparsers.add_parser("run", help="executa diagnóstico; local por padrão")
    run.add_argument("suite", choices=[*SUITES, "all"])
    run.add_argument("--limit", type=int, help="roda só os N primeiros casos (debug)")
    run.add_argument("--publish", action="store_true", help="publica a rodada no Langfuse")

    gate = subparsers.add_parser("gate", help="executa decisão completa e bloqueante")
    gate.add_argument("suite", choices=list(SUITES))
    gate.add_argument("--publish", action="store_true", help="publica a rodada no Langfuse")

    smoke = subparsers.add_parser(
        "smoke-cache",
        help="smoke remoto opt-in de prompt cache (duas chamadas sync + streaming)",
    )
    smoke.add_argument(
        "--allow-remote",
        action="store_true",
        help="confirma quatro chamadas remotas sintéticas e de custo mínimo",
    )
    smoke.add_argument("--model", help="sobrescreve OPENAI_MODEL apenas para o smoke")
    smoke.add_argument("--prefix-tokens", type=int, default=1280)
    return parser


def _normalize_legacy_args(argv: list[str]) -> list[str]:
    if not argv or argv[0] in {"run", "gate", "-h", "--help"}:
        return argv
    if argv[0] not in {*SUITES, "all"}:
        return argv
    print(
        "[deprecated] use `python -m radar.core.eval run <suite>`; "
        "a sintaxe histórica será removida.",
        file=sys.stderr,
    )
    normalized = ["run", *argv]
    if "--no-push" in normalized:
        normalized.remove("--no-push")  # run já é local por padrão
    return normalized


def _load_eval_environment() -> bool:
    """Carrega o perfil e recusa um ``ENV_FILE`` explícito que não existe."""
    explicit = os.getenv("ENV_FILE")
    if explicit and not Path(explicit).expanduser().is_file():
        print(f"ENV_FILE não encontrado: {explicit}", file=sys.stderr)
        return False
    load_environment_profile()
    return True


def main(argv: list[str] | None = None) -> int:
    if not _load_eval_environment():
        return 2
    args = _parser().parse_args(_normalize_legacy_args(list(argv or sys.argv[1:])))
    if args.intent == "smoke-cache":
        if not args.allow_remote:
            print("recusado: smoke-cache exige --allow-remote (quatro chamadas pagas mínimas).")
            return 2
        from radar.core.eval.prompt_cache_smoke import DEFAULT_MODEL, report, run

        selected_model = args.model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        try:
            print(report(run(model=args.model, prefix_tokens=args.prefix_tokens), model=selected_model))
        except (RuntimeError, ValueError) as exc:
            print(f"smoke-cache não executado: {exc}", file=sys.stderr)
            return 2
        return 0
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
