#!/usr/bin/env python3
"""Wrapper com fail-closed para ``supabase db push|reset``.

O push aceita uma sentinela ainda ausente para resolver o bootstrap da migration
040, mas valida ambiente/host e as confirmações de produção antes de executar.
"""

from __future__ import annotations

import argparse
import os
import subprocess

from radar.core.environment import (
    Environment,
    assert_database_target,
    database_identity,
    initialize_environment_metadata,
    load_environment_profile,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("push", "reset"))
    args = parser.parse_args()
    load_environment_profile()
    identity = database_identity()

    if args.command == "reset" and not identity.is_local:
        parser.error("db reset é permitido somente contra Supabase local")

    assert_database_target(
        f"supabase db {args.command}",
        allow_uninitialized_sentinel=True,
    )
    supabase_command = ["supabase", "db", args.command]
    if args.command == "push" and not identity.is_local:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            parser.error("DATABASE_URL é obrigatório para migration remota")
        # A URL explícita torna o deploy reproduzível no runner e não depende
        # de estado persistente criado por `supabase link`.
        supabase_command.extend(["--db-url", database_url])
    subprocess.run(supabase_command, check=True)

    # reset reaplica seed local; push cria a tabela vazia no primeiro deploy.
    project_ref = identity.project_ref or "local"
    initialize_environment_metadata(Environment(identity.environment), project_ref)
    print(
        f"supabase db {args.command}: OK — "
        f"environment={identity.environment.value}, project_ref={project_ref}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
