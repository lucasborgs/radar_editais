"""core/kg/source_docs.py — Documento Canônico (§12.3) durável por edital.

O FS do worker não é fonte de verdade durável: um rebuild de imagem sem volume
persistente apaga `data/bronze/` e os PDFs FINEP. Como o `chunk_edital` (lazy)
lê o conteúdo-fonte via adapters que leem o disco, isso fazia o chunking
produzir 0 chunks (job "succeeded" com 0).

Este módulo persiste o Documento Canônico (o contrato agnóstico §12.3 —
`[{doc_name, units}]`) no Postgres, gravado no scrape (com o disco fresco). O
chunk_edital passa a ler daqui e o disco vira só cache/fallback. Espelha o seam
de core/kg/kg_store (artefatos do grafo duráveis para prod).

Postgres-only: sem Supabase é no-op (load → None, save → no-op). Dev local usa o
fallback de disco do adapter, que não é efêmero. Spec:
docs/specs/durable-source-docs.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

from radar.pipeline.adapters.base import CanonicalDoc

logger = logging.getLogger(__name__)

_TABLE = "edital_source_docs"


def _pg_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))


def canonical_hash(doc: CanonicalDoc) -> str:
    """md5 determinístico do Documento Canônico (mesmo espírito do
    structurer._source_hash). Estável à ordem dos documentos."""
    h = hashlib.md5()
    for entry in sorted(doc, key=lambda d: d.get("doc_name", "")):
        h.update((entry.get("doc_name") or "").encode("utf-8", "ignore"))
        h.update(json.dumps(
            entry.get("metadata") or {}, sort_keys=True, ensure_ascii=False,
        ).encode("utf-8", "ignore"))
        for unit in entry.get("units") or []:
            h.update((unit or "").encode("utf-8", "ignore"))
    return h.hexdigest()


def active_documents(doc: CanonicalDoc) -> CanonicalDoc:
    """Remove versões explicitamente superadas do data plane factual.

    Ausência de metadata mantém compatibilidade com documentos legados.
    """
    return [
        entry for entry in doc
        if (entry.get("metadata") or {}).get("authority_state") != "superseded"
    ]


# ---------------------------------------------------------------------------
# Leitura / escrita
# ---------------------------------------------------------------------------

def load(edital_id: str) -> CanonicalDoc | None:
    """Documento Canônico durável de um edital (prefixado). None se ausente,
    Supabase não configurado, ou qualquer erro de lookup (degrada para disco)."""
    if not _pg_configured():
        return None
    try:
        from radar.core.infra.db import get_supabase_service
        resp = (
            get_supabase_service()
            .table(_TABLE)
            .select("canonical_doc")
            .eq("edital_id", edital_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning("source_docs.load: falha lendo %s: %s", edital_id, e)
        return None
    rows = resp.data or []
    if not rows:
        return None
    doc = rows[0].get("canonical_doc")
    return doc if doc else None


def save(edital_id: str, source: str, doc: CanonicalDoc) -> bool:
    """Upsert do Documento Canônico durável. Retorna True se gravou, False em
    no-op (sem Supabase / doc vazio) ou falha. Não levanta — persistir o doc não
    pode quebrar o chunk path (o `doc` já está em memória para a run atual; o
    cron é a rede de segurança para runs futuras)."""
    if not doc or not _pg_configured():
        return False
    try:
        from radar.core.infra.db import get_supabase_service
        get_supabase_service().table(_TABLE).upsert(
            {
                "edital_id": edital_id,
                "source": source,
                "canonical_doc": doc,
                "content_hash": canonical_hash(doc),
            },
            on_conflict="edital_id",
        ).execute()
    except Exception as e:
        logger.warning("source_docs.save: falha gravando %s: %s", edital_id, e)
        return False
    logger.info("source_docs: %s persistido (%d docs)", edital_id, len(doc))
    return True


# ---------------------------------------------------------------------------
# Escrita proativa (cron / backfill manual)
# ---------------------------------------------------------------------------

def persist_all_current() -> int:
    """Persiste o Documento Canônico de todos os editais vigentes (index) a
    partir do disco (bronze recém-scraped). Chamado pelo cron após scrape+build,
    com o disco fresco. Retorna quantos foram persistidos.

    Por-edital tolerante a falha: um adapter que não extrai (PDF corrompido,
    URL morta no bronze) é logado e pulado — não derruba o lote."""
    if not _pg_configured():
        logger.info("source_docs.persist_all_current: Supabase ausente — no-op")
        return 0

    from radar.core.kg import entity_catalog  # tardio: evita ciclo no import do módulo
    from radar.core.kg.edital_id import native_id_of, source_of
    from radar.pipeline.adapters.base import get_adapter

    editais = [{"id": c["id"]} for c in entity_catalog.list_editais(limit=10_000)]
    n_ok = 0
    for e in editais:
        edital_id = e.get("id")
        if not edital_id:
            continue
        try:
            source = source_of(edital_id)
            native = native_id_of(edital_id)
            doc = get_adapter(source).to_documents(native)
            if not doc:
                logger.info(
                    "source_docs.persist_all_current: %s sem conteúdo no disco (pulado)",
                    edital_id,
                )
                continue
            if save(edital_id, source, doc):
                n_ok += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "source_docs.persist_all_current: falha em %s: %s", edital_id, exc,
            )
    logger.info("source_docs.persist_all_current: %d/%d editais persistidos",
                n_ok, len(editais))
    return n_ok


if __name__ == "__main__":
    # Backfill manual: popula o durável a partir do disco atual. Útil logo após
    # um scrape para publicar o Documento Canônico para prod sem esperar o cron.
    from radar.core.environment import assert_database_target, load_environment_profile

    load_environment_profile()
    logging.basicConfig(level=logging.INFO)
    assert_database_target("source docs backfill")
    count = persist_all_current()
    print(json.dumps({"persisted": count}))
