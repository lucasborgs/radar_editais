"""Bootstrap das tabelas do checkpointer + Store da memória (Etapas 3 e 5).

Cria/atualiza, no schema dedicado `agent_memory`, as tabelas do AsyncPostgresSaver
(checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations) e do
AsyncPostgresStore (store, store_vectors, store_migrations) via `.setup()` —
idempotente, cada um segura a própria tabela de migrations.

Schema dedicado (migration 028): `agent_memory` fica FORA dos schemas que o PostgREST
do Supabase expõe (public/storage/graphql_public), então essa maquinaria que bypassa
RLS é invisível pela REST API por construção — sem precisar do band-aid antigo de RLS+
revoke tabela-a-tabela (a migration 027 ficou obsoleta). O isolamento multi-tenant vem
do thread_id (checkpointer) / namespace por workspace_id (Store).

Operacional: rode no deploy, depois de `pip install -e .` e das migrations, antes do
worker/API.

    python scripts/setup_checkpointer.py

Requer DATABASE_URL (DSN direto do Postgres). Sem ele, aborta — em dev/teste o runtime
cai para InMemorySaver/InMemoryStore e este script não é necessário.
"""
from __future__ import annotations

from radar.core.environment import assert_database_target, load_environment_profile

load_environment_profile()

import logging  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("setup_checkpointer")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.error(
            "DATABASE_URL ausente — nada a fazer (runtime usa InMemorySaver/InMemoryStore).",
        )
        return 1
    assert_database_target("agent memory schema setup")

    # Reusa a init real do runtime (cria pools no schema agent_memory + roda setup()
    # no loop dedicado). O search_path no schema dedicado já tranca contra o PostgREST.
    from radar.core.llm.agent_graph import (
        _get_memory_store,
        _get_writing_checkpointer,
        shutdown_writing_runtime,
    )

    try:
        saver = _get_writing_checkpointer()
        if saver is None:
            logger.error("Falha ao inicializar o checkpointer (ver logs acima).")
            return 1
        logger.info("AsyncPostgresSaver.setup() OK (%s)", type(saver).__name__)

        store = _get_memory_store()
        if store is None:
            logger.error("Falha ao inicializar o Store da memória (ver logs acima).")
            return 1
        logger.info("AsyncPostgresStore.setup() OK (%s)", type(store).__name__)

        logger.info("Checkpointer + Store prontos no schema agent_memory.")
        return 0
    finally:
        # Fecha os pools + para o loop dedicado para um exit limpo (sem tasks órfãs).
        shutdown_writing_runtime()


if __name__ == "__main__":
    sys.exit(main())
