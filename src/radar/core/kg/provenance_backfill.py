"""core/kg/provenance_backfill.py — RT01-T12: backfill amostral e shadow metrics.

Backfill DETERMINÍSTICO de proveniência para registros gold legados (silver
anterior a RT01-T04/T05, `entities.provenance = '{}'` / `match_chunks` com
coordenadas NULL). Sem LLM, sem embedding, sem rede — script sequencial
único, sem scheduler/tabela/paralelismo (plano
docs/execution/radar-data-trust/plans/01-provenance/RT01-T12-sample-backfill.md,
spec docs/specs/radar-data-trust-01-provenance.md §9.1/§11).

Regras de honestidade (por origem, um bloco por tipo de fato):

  requisitos_texto.<i> (editais)  → resolve `evidence_resolver.resolve_quote`
                                     contra o silver ATUAL; `stated` só quando
                                     `exact`/`document_only`; sem match →
                                     path fica de fora (legacy).
  status / mecanismo (editais)    → re-derivado com as regras atuais
                                     (`gold._normalize_status`/
                                     `_infer_mecanismo_from_text`) sobre os
                                     inputs atuais; `inferred` só quando o
                                     valor re-derivado é IGUAL ao armazenado;
                                     diferente → path fica de fora (nunca
                                     conserta o valor).
  ICT (name/metadata.url)         → `stated`, âncora do registro EMBRAPII
                                     atual (mesma construção da T07), só se o
                                     registro casar pela chave natural.
  investidor/programa (copiados)  → `unknown` com âncora do catálogo atual
                                     (mesma construção da T08), só se o
                                     registro casar pela chave natural.
  match_chunks (coords NULL)      → re-empacota o silver atual (`_pack_chunks`)
                                     e casa por TEXTO EXATO e ÚNICO com o
                                     chunk armazenado; sem match exato e único
                                     → fica NULL.

Escopo deliberadamente restrito ao que a task nomeia: ICT recebe só
name/metadata.url (não uf/setores/tags — esses são `inferred/deterministic`
sobre `areas_raw`, fora da tabela de fatos desta task); investidor/programa
recebem só os campos copiados verbatim do catálogo (`unknown`), não os
derivados (setores/tags/status/ticket/mecanismo/formato) — esses exigiriam a
mesma checagem de igualdade que `status`/`mecanismo` de edital, que a task
não nomeou para esta origem; backfill-los seria extrapolar o pedido.

`producer.kind=BACKFILL` (name="rt01_t12_backfill", version="1") em TUDO que
este script grava — nunca reivindica o produtor histórico (llm/adapter/
deterministic) que gerou o valor original. Nenhuma outra coluna do gold é
tocada (só `entities.provenance` e as 4 colunas de coordenada de
`match_chunks`); path/coordenada já preenchidos nunca são sobrescritos —
tanto por checagem na orquestração (não entram no payload) quanto pelo guard
SQL (`document is null`) e pelo comportamento das funções `decide_*`
(retornam `None` quando `already_present`). Reexecução converge para 0
escritas.

`--sample N` (default 5) limita quantas entidades POR (origem, kind) recebem
escrita em `--execute`, em ordem determinística (`native_id` asc) — a mesma
seleção em toda reexecução. O relatório shadow (`--dry-run`) sempre cobre a
população COMPLETA, não só a amostra: "medir, não prometer backfill total".

Uso:
    python -m radar.core.kg.provenance_backfill                  # dry-run
    python -m radar.core.kg.provenance_backfill --execute --sample 5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg

from radar.core.environment import assert_database_target
from radar.core.kg import gold, provenance_writer, schema
from radar.core.kg.evidence_resolver import resolve_quote
from radar.domain.provenance import (
    DerivationInfo,
    FactProvenance,
    FactState,
    LocatorQuality,
    ProducerInfo,
    ProducerKind,
)

logger = logging.getLogger(__name__)

BACKFILL_PRODUCER_NAME = "rt01_t12_backfill"
BACKFILL_PRODUCER_VERSION = "1"
DEFAULT_SAMPLE = 5


def _producer() -> ProducerInfo:
    return ProducerInfo(kind=ProducerKind.BACKFILL, name=BACKFILL_PRODUCER_NAME, version=BACKFILL_PRODUCER_VERSION)


# ===========================================================================
# Decisões puras (sem I/O) — uma função por regra de honestidade
# ===========================================================================


def decide_requisito(
    text: str,
    *,
    blocks: list[dict],
    source: str,
    stem: str,
    edital_id: str,
    silver_source_hash: str | None,
    source_url: str | None,
    already_present: bool,
) -> dict | None:
    """`requisitos_texto.<i>` — dict `FactProvenance` quando `stated`
    (exact/document_only) via `resolve_quote`; `None` quando já coberto ou
    não resolvível (fica legacy)."""
    if already_present:
        return None
    result = resolve_quote(
        text, blocks, source=source, native_id=stem, edital_id=edital_id,
        silver_source_hash=silver_source_hash, source_url=source_url,
    )
    ref = result.evidence_ref
    if ref is None or ref.locator_quality not in (LocatorQuality.EXACT, LocatorQuality.DOCUMENT_ONLY):
        return None
    fp = FactProvenance(state=FactState.STATED, evidence_refs=[ref], producer=_producer())
    return fp.model_dump(mode="json")


def decide_status(*, rederived: str | None, stored: str | None, already_present: bool) -> dict | None:
    """`status` — `inferred` só quando o valor re-derivado bate com o
    armazenado; diferente/ausente → path fica de fora (nunca conserta)."""
    if already_present or rederived is None or stored is None or rederived != stored:
        return None
    fp = FactProvenance(
        state=FactState.INFERRED, producer=_producer(),
        derivation=DerivationInfo(rule="_normalize_status:v1", inputs=["bronze.status", "deadline"]),
    )
    return fp.model_dump(mode="json")


def decide_mecanismo(*, rederived: str | None, stored: str | None, already_present: bool) -> dict | None:
    """`mecanismo` — mesma regra de `decide_status`, sobre
    `_infer_mecanismo_from_text`."""
    if already_present or rederived is None or stored is None or rederived != stored:
        return None
    fp = FactProvenance(
        state=FactState.INFERRED, producer=_producer(),
        derivation=DerivationInfo(
            rule="_infer_mecanismo_from_text:v1", inputs=["bronze.descricao_bronze", "silver.thematic_sections"]
        ),
    )
    return fp.model_dump(mode="json")


def decide_catalog_anchor_paths(
    *,
    kind: str,
    record: dict,
    document: str | None,
    source_url: str | None,
    native_id: str | None,
    existing_paths: set[str],
) -> dict[str, dict]:
    """ICT: `name`/`metadata.url` → `stated`. investidor/programa: campos
    copiados verbatim do catálogo → `unknown`. Ambos ancorados no registro
    ATUAL (mesma construção T07/T08); só paths ausentes de `existing_paths` e
    com valor não vazio no registro. `document=None` (registro sem arquivo
    versionado identificável) → nenhum path (não fabrica âncora sem hash)."""
    if document is None:
        return {}
    if kind == "ict":
        anchor = provenance_writer.build_ict_record_anchor(
            record=record, document=document, source_url=source_url, native_id=native_id,
        )
        candidates = {"name": record.get("name"), "metadata.url": record.get("url")}
        state = FactState.STATED
    elif kind == "investidor":
        anchor = provenance_writer.build_curated_catalog_anchor(record, document=document, source_url=source_url)
        candidates = {
            "name": record.get("name"), "description": record.get("tese"),
            "metadata.site": record.get("site"),
        }
        state = FactState.UNKNOWN
    elif kind == "programa":
        anchor = provenance_writer.build_curated_catalog_anchor(record, document=document, source_url=source_url)
        candidates = {
            "name": record.get("name"), "metadata.operador": record.get("operador"),
            "metadata.beneficio": record.get("beneficio"), "metadata.elegibilidade": record.get("elegibilidade"),
        }
        state = FactState.UNKNOWN
    else:
        return {}
    out: dict[str, dict] = {}
    for path, value in candidates.items():
        if value and path not in existing_paths:
            fp = FactProvenance(state=state, evidence_refs=[anchor], producer=_producer())
            out[path] = fp.model_dump(mode="json")
    return out


def decide_chunk_coords(
    *, stored_text: str, repacked_chunks: list[dict], silver_source_hash: str | None, already_filled: bool,
) -> dict | None:
    """Coords de UM `match_chunk` legado — só quando o texto armazenado casa
    EXATO com UM único chunk reempacotado do silver atual; 0 ou >1 matches →
    `None` (ambíguo/sem match, nunca escolhe silenciosamente)."""
    if already_filled:
        return None
    matches = [c for c in repacked_chunks if c.get("text") == stored_text]
    if len(matches) != 1:
        return None
    coords = provenance_writer.chunk_storage_coords(matches[0], silver_source_hash)
    return coords or None


# ===========================================================================
# Relatório
# ===========================================================================


@dataclass
class OriginReport:
    entities_total: int = 0
    entities_legacy: int = 0
    paths_already_covered: int = 0
    paths_stated: int = 0
    paths_inferred: int = 0
    paths_unknown: int = 0
    locator_exact: int = 0
    locator_document_only: int = 0
    unresolved: int = 0
    chunks_null_coords: int = 0
    chunks_backfillable: int = 0
    entities_sampled: int = 0
    entities_written: int = 0
    paths_written: int = 0
    chunks_written: int = 0

    def as_dict(self) -> dict:
        return {
            "entities_total": self.entities_total,
            "entities_legacy": self.entities_legacy,
            "paths_already_covered": self.paths_already_covered,
            "paths": {
                "stated": self.paths_stated,
                "inferred": self.paths_inferred,
                "unknown": self.paths_unknown,
            },
            "locators": {"exact": self.locator_exact, "document_only": self.locator_document_only},
            "unresolved": self.unresolved,
            "chunks_null_coords": self.chunks_null_coords,
            "chunks_backfillable": self.chunks_backfillable,
            "write": {
                "entities_sampled": self.entities_sampled,
                "entities_written": self.entities_written,
                "paths_written": self.paths_written,
                "chunks_written": self.chunks_written,
            },
        }


def _record_path_metric(report: OriginReport, value: dict | None) -> None:
    if value is None:
        report.unresolved += 1
        return
    state = value["state"]
    if state == "stated":
        report.paths_stated += 1
    elif state == "inferred":
        report.paths_inferred += 1
    elif state == "unknown":
        report.paths_unknown += 1
    refs = value.get("evidence_refs") or []
    if refs:
        quality = refs[0]["locator_quality"]
        if quality == "exact":
            report.locator_exact += 1
        elif quality == "document_only":
            report.locator_document_only += 1


# ===========================================================================
# Leitura (I/O real — banco + silver/bronze em disco)
# ===========================================================================


def _fetch_rows(cur, kind: str) -> list[dict]:
    cur.execute(
        "select id, source, native_id, status, mecanismo, requisitos_texto, provenance "
        "from public.entities where kind=%s order by native_id",
        (kind,),
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _fetch_chunks(cur) -> list[dict]:
    cur.execute(
        "select mc.id, mc.entity_id, mc.idx, mc.text, mc.document, "
        "       e.kind, e.source, e.native_id "
        "from public.match_chunks mc join public.entities e on e.id = mc.entity_id "
        "where e.kind in ('edital','programa') order by e.source, e.native_id, mc.idx"
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


@dataclass
class _CatalogRecord:
    record: dict
    document: str | None
    source_url: str | None


def _load_investidor_catalog() -> dict[str, _CatalogRecord]:
    path = gold.SILVER_DIR / "investidores.json"
    if not path.exists():
        return {}
    recs = json.loads(path.read_text(encoding="utf-8")).get("investidores", [])
    return {
        r["id"]: _CatalogRecord(r, provenance_writer.CURATED_INVESTIDORES_DOCUMENT, r.get("site") or None)
        for r in recs if r.get("id")
    }


def _load_programa_catalog() -> dict[str, _CatalogRecord]:
    path = gold.SILVER_DIR / "programas.json"
    if not path.exists():
        return {}
    recs = json.loads(path.read_text(encoding="utf-8")).get("programas", [])
    return {
        r["id"]: _CatalogRecord(r, provenance_writer.CURATED_PROGRAMAS_DOCUMENT, r.get("site") or None)
        for r in recs if r.get("id")
    }


def _load_ict_catalog() -> dict[str, _CatalogRecord]:
    ict_dir = gold.BRONZE_DIR / "ict_raw"
    if not ict_dir.exists():
        return {}
    out: dict[str, _CatalogRecord] = {}
    # EMBRAPII (unidades credenciadas) + PNIPE (laboratórios como capacidades —
    # spec docs/specs/ict-pnipe-capabilities.md): mesmo formato de registro
    # versionado, IDs nativos distintos por fonte.
    for source, glob_pat in (("embrapii", "embrapii_*.json"), ("pnipe", "pnipe_*.json")):
        files = sorted(ict_dir.glob(glob_pat))
        if not files:
            continue
        document = files[-1].name
        recs = json.loads(files[-1].read_text(encoding="utf-8"))
        for r in recs:
            slug = r.get("slug") or schema.slugify(r.get("name") or "")
            out[f"{source}:{slug}"] = _CatalogRecord(r, document, r.get("url"))
    return out


def _programa_description(record: dict) -> str:
    return gold._ws(" ".join(x for x in [record.get("descricao"), record.get("beneficio")] if x))  # noqa: SLF001


def _repack_chunks_for_entity(
    row: dict, *, programa_catalog: dict[str, _CatalogRecord],
) -> tuple[list[dict], str | None]:
    """Reempacota o silver ATUAL para `row` (kind edital/programa), mesma
    construção de `gold._ingest_editais`/`_ingest_programas`. Retorna
    `(chunks, silver_source_hash_ref)`."""
    if row["kind"] == "edital":
        source, native_id = row["source"], row["native_id"]
        stem = native_id.split(":", 1)[1] if native_id.startswith(f"{source}:") else native_id
        blocks = gold._read_silver_blocks(source, stem)  # noqa: SLF001
        if not blocks:
            return [], None
        thematic = [b for b in blocks if gold.classify_section(b.get("section_path"), b.get("kind")) == "thematic"]
        src_hash = gold._read_silver_hash(source, stem)  # noqa: SLF001
        hash_ref = f"md5:{src_hash}" if src_hash else None
        return gold._pack_chunks(thematic), hash_ref  # noqa: SLF001
    if row["kind"] == "programa":
        cat = programa_catalog.get(row["native_id"])
        if cat is None:
            return [], None
        desc = _programa_description(cat.record)
        blocks = [{"section_path": [cat.record.get("name") or ""], "kind": "paragraph", "text": desc}]
        return gold._pack_chunks(blocks), None  # noqa: SLF001
    return [], None


# ===========================================================================
# Orquestração
# ===========================================================================


def run_backfill(
    conn, *, execute: bool, sample: int, prestate_path: str | None = None,
    defer_editais: bool = False,
) -> dict:
    """Orquestra o backfill sobre `conn` (psycopg connection ou fake com a
    mesma interface `cursor()/execute()/fetchall()/description`).
    `execute=False`: calcula tudo, não escreve — relatório shadow completo
    (população inteira, não limitada por `sample`). `execute=True`: escreve
    só os `sample` primeiros registros (ordem determinística por
    `native_id`) de cada (origem, kind), precedido do despejo de pré-estado
    em `prestate_path`.

    `defer_editais=True` (decisão do proprietário, RT01-T12 rework
    2026-07-24): as origens de edital (`gold.EDITAL_SOURCES`) ainda são
    MEDIDAS no relatório shadow, mas NÃO são escritas — o backfill de edital
    fica adiado até haver ganho real de citação (o backfill atual produz
    `stated=0`, sem citação de página). O adiamento é explícito por esta
    flag, não por ausência de dados no ambiente."""
    if execute:
        assert_database_target("provenance backfill (execute)")

    sample = max(sample, 0)
    origins: dict[str, OriginReport] = {}
    prestate: list[dict] = []
    # (origin, entity_id, {path: fp_dict})
    pending_entity_writes: list[tuple[str, str, dict]] = []
    # (origin, chunk_id, coords)
    pending_chunk_writes: list[tuple[str, str, dict]] = []

    investidor_catalog = _load_investidor_catalog()
    programa_catalog = _load_programa_catalog()
    ict_catalog = _load_ict_catalog()

    with conn.cursor() as cur:
        edital_rows = _fetch_rows(cur, "edital")
        investidor_rows = _fetch_rows(cur, "investidor")
        programa_rows = _fetch_rows(cur, "programa")
        ict_rows = _fetch_rows(cur, "ict")
        chunk_rows = _fetch_chunks(cur)

    sampled_entity_ids: set[str] = set()

    # -- editais: requisitos_texto + status + mecanismo ---------------------
    for source in gold.EDITAL_SOURCES:
        rows = [r for r in edital_rows if r["source"] == source]
        if not rows:
            continue
        report = origins.setdefault(source, OriginReport())
        report.entities_total += len(rows)
        sampled_ids = {r["id"] for r in rows[:sample]}
        report.entities_sampled += len(sampled_ids)
        sampled_entity_ids |= sampled_ids

        for row in rows:
            provenance = row["provenance"] or {}
            if not provenance:
                report.entities_legacy += 1
            existing = set(provenance.keys())

            stem = row["native_id"].split(":", 1)[1] if row["native_id"].startswith(f"{source}:") else row["native_id"]
            blocks = gold._read_silver_blocks(source, stem)  # noqa: SLF001
            src_hash = gold._read_silver_hash(source, stem)  # noqa: SLF001
            hash_ref = f"md5:{src_hash}" if src_hash else None
            md = gold._edital_metadata(source, stem, blocks)  # noqa: SLF001
            thematic_text = "\n".join(
                gold._ws(b.get("text") or "") for b in blocks  # noqa: SLF001
                if gold.classify_section(b.get("section_path"), b.get("kind")) == "thematic"
            ).strip()

            new_paths: dict[str, dict] = {}

            for i, req in enumerate(row["requisitos_texto"] or []):
                path = f"requisitos_texto.{i}"
                if path in existing:
                    report.paths_already_covered += 1
                    continue
                fp = decide_requisito(
                    req, blocks=blocks, source=source, stem=stem, edital_id=row["native_id"],
                    silver_source_hash=hash_ref, source_url=None, already_present=False,
                )
                _record_path_metric(report, fp)
                if fp is not None:
                    new_paths[path] = fp

            if row["status"] is not None:
                if "status" in existing:
                    report.paths_already_covered += 1
                else:
                    fp_status = decide_status(rederived=md["status"], stored=row["status"], already_present=False)
                    _record_path_metric(report, fp_status)
                    if fp_status is not None:
                        new_paths["status"] = fp_status

            if row["mecanismo"] is not None:
                if "mecanismo" in existing:
                    report.paths_already_covered += 1
                else:
                    rederived_mecanismo = gold._infer_mecanismo_from_text(  # noqa: SLF001
                        md["descricao_bronze"] or thematic_text
                    )
                    fp_mecanismo = decide_mecanismo(
                        rederived=rederived_mecanismo, stored=row["mecanismo"], already_present=False
                    )
                    _record_path_metric(report, fp_mecanismo)
                    if fp_mecanismo is not None:
                        new_paths["mecanismo"] = fp_mecanismo

            # `defer_editais`: métricas acima já foram registradas (medição
            # honesta); só a ESCRITA é adiada para as origens de edital.
            if new_paths and row["id"] in sampled_ids and not defer_editais:
                pending_entity_writes.append((source, row["id"], new_paths))
                prestate.append({"table": "entities", "id": row["id"], "provenance": provenance})

    # -- ICTs / investidores / programas (âncora de catálogo) ---------------
    for kind, rows, catalog in (
        ("ict", ict_rows, ict_catalog),
        ("investidor", investidor_rows, investidor_catalog),
        ("programa", programa_rows, programa_catalog),
    ):
        if not rows:
            continue
        origin = "embrapii" if kind == "ict" else "curadoria"
        report = origins.setdefault(origin, OriginReport())
        report.entities_total += len(rows)
        sampled_ids = {r["id"] for r in rows[:sample]}
        report.entities_sampled += len(sampled_ids)
        sampled_entity_ids |= sampled_ids

        candidate_paths = {
            "ict": ["name", "metadata.url"],
            "investidor": ["name", "description", "metadata.site"],
            "programa": ["name", "metadata.operador", "metadata.beneficio", "metadata.elegibilidade"],
        }[kind]

        for row in rows:
            provenance = row["provenance"] or {}
            if not provenance:
                report.entities_legacy += 1
            existing = set(provenance.keys())
            report.paths_already_covered += len(existing & set(candidate_paths))

            cat = catalog.get(row["native_id"])
            if cat is None:
                # registro não existe mais no catálogo/bronze atual — não há
                # âncora possível; cada path ainda descoberto conta como não
                # resolvível (fica legacy).
                report.unresolved += len([p for p in candidate_paths if p not in existing])
                continue

            new_paths = decide_catalog_anchor_paths(
                kind=kind, record=cat.record, document=cat.document, source_url=cat.source_url,
                native_id=row["native_id"], existing_paths=existing,
            )
            for fp in new_paths.values():
                _record_path_metric(report, fp)
            unresolved_here = len([p for p in candidate_paths if p not in existing and p not in new_paths])
            report.unresolved += unresolved_here
            if new_paths and row["id"] in sampled_ids:
                pending_entity_writes.append((origin, row["id"], new_paths))
                prestate.append({"table": "entities", "id": row["id"], "provenance": provenance})

    # -- match_chunks legados (coords NULL) ----------------------------------
    by_entity: dict[str, list[dict]] = {}
    for c in chunk_rows:
        by_entity.setdefault(c["entity_id"], []).append(c)

    for entity_id, rows in by_entity.items():
        anchor_row = rows[0]
        origin = anchor_row["source"] if anchor_row["kind"] == "edital" else "curadoria"
        report = origins.setdefault(origin, OriginReport())
        null_rows = [r for r in rows if r["document"] is None]
        report.chunks_null_coords += len(null_rows)
        if not null_rows:
            continue
        repacked, hash_ref = _repack_chunks_for_entity(anchor_row, programa_catalog=programa_catalog)
        in_sample = entity_id in sampled_entity_ids
        for chunk_row in null_rows:
            coords = decide_chunk_coords(
                stored_text=chunk_row["text"], repacked_chunks=repacked,
                silver_source_hash=hash_ref, already_filled=False,
            )
            if coords is None:
                report.unresolved += 1
                continue
            report.chunks_backfillable += 1
            if in_sample and not (defer_editais and origin in gold.EDITAL_SOURCES):
                pending_chunk_writes.append((origin, chunk_row["id"], coords))
                prestate.append({
                    "table": "match_chunks", "id": chunk_row["id"],
                    "document": chunk_row["document"], "page": None,
                    "silver_block_idx": None, "source_hash": None,
                })

    # -- escrita (só em modo execute) ----------------------------------------
    if execute and (pending_entity_writes or pending_chunk_writes):
        if prestate_path:
            with open(prestate_path, "w", encoding="utf-8") as fh:
                json.dump(prestate, fh, ensure_ascii=False, indent=2, default=str)
        with conn.cursor() as cur:
            for origin, entity_id, new_paths in pending_entity_writes:
                cur.execute(
                    "update public.entities set provenance = provenance || %s::jsonb where id = %s",
                    (json.dumps(new_paths, ensure_ascii=False), entity_id),
                )
                origins[origin].entities_written += 1
                origins[origin].paths_written += len(new_paths)
            for origin, chunk_id, coords in pending_chunk_writes:
                cur.execute(
                    "update public.match_chunks set document=%(document)s, page=%(page)s, "
                    "silver_block_idx=%(silver_block_idx)s, source_hash=%(source_hash)s "
                    "where id=%(id)s and document is null",
                    {**coords, "id": chunk_id},
                )
                origins[origin].chunks_written += 1
        if hasattr(conn, "commit"):
            conn.commit()

    return {
        "mode": "execute" if execute else "dry_run",
        "sample_per_origin_kind": sample,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "origins": {k: v.as_dict() for k, v in sorted(origins.items())},
    }


# ===========================================================================
# CLI
# ===========================================================================


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Backfill amostral de proveniência (RT01-T12)")
    ap.add_argument("--execute", action="store_true", help="escreve no banco (default: dry-run)")
    ap.add_argument(
        "--sample", type=int, default=DEFAULT_SAMPLE,
        help="entidades por (origem, kind) escritas em --execute (default: 5)",
    )
    ap.add_argument(
        "--defer-editais", action="store_true",
        help="mede mas NÃO escreve as origens de edital (decisão RT01-T12: "
             "backfill de edital adiado até haver ganho real de citação)",
    )
    args = ap.parse_args()

    prestate_path = None
    if args.execute:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prestate_path = os.path.join(
            os.environ.get("RT01_T12_SCRATCH_DIR", "/private/tmp/radar-editais-rt01-t12"),
            f"backfill_prestate_{ts}.json",
        )

    with psycopg.connect(gold._dsn(), autocommit=True) as conn:  # noqa: SLF001
        report = run_backfill(
            conn, execute=args.execute, sample=args.sample, prestate_path=prestate_path,
            defer_editais=args.defer_editais,
        )

    if prestate_path and os.path.exists(prestate_path):
        report["prestate_file"] = prestate_path
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
