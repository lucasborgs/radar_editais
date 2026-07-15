#!/usr/bin/env python3
"""Inicializa a sentinela após migrations; não use como troca rotineira de ambiente."""

from __future__ import annotations

import argparse

from core.environment import (
    Environment,
    initialize_environment_metadata,
    load_environment_profile,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=[item.value for item in Environment if item.value != "unknown"], required=True)
    parser.add_argument("--project-ref", required=True)
    args = parser.parse_args()
    load_environment_profile()
    initialize_environment_metadata(Environment(args.environment), args.project_ref)
    print(f"Sentinela inicializada: environment={args.environment}, project_ref={args.project_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
