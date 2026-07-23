"""Harness de captura da projeção gold (RT01-T02).

Executa o ingest REAL (`radar.core.kg.gold.ingest_all`) sobre as fixtures de
`tests/fixtures/gold_equivalence/`, interceptando SOMENTE os seams de
infraestrutura (banco, LLM tagger, constraints producer, embeddings) — a
lógica de mapeamento por fonte (`_ingest_editais`, `_ingest_programas`,
`_ingest_icts`, `_ingest_investidores`, `normalize_setores`, `classify_section`
etc.) roda sem modificação. NÃO reimplementa nada do gold: se o código real
não puder rodar só com os stubs abaixo, isso é um sinal de que a task deve
parar, não que o harness deve compensar.

Uso num teste:

    from tests.helpers.gold_projection import run_capture

    def test_algo(monkeypatch):
        projection, stats = run_capture(monkeypatch)
        ...

Regeneração do baseline (`baseline_projection.json`):

    cd <worktree>
    PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/python -c \\
        "from tests.helpers.gold_projection import regenerate_baseline; regenerate_baseline()"
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from radar.core.kg import equivalence

DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "gold_equivalence"

# Modelo/dimensionalidade do stub de embedding — deliberadamente distinto de
# qualquer nome de modelo real (spec: "snapshot NÃO se apresenta como saída
# real de modelo").
STUB_EMBEDDING_MODEL = "stub-embedder-v1"
STUB_EMBEDDING_DIM = 8


def _synthetic_vector(hash_hex: str | None, dim: int) -> list[float]:
    """Vetor sintético determinístico derivado de um hash hex — nunca um
    embedding real. Cada componente vem de 2 hex chars do hash (ciclando se
    `dim` exceder `len(hash_hex)/2`), normalizado para [-1, 1]."""
    if not hash_hex:
        return [0.0] * dim
    out: list[float] = []
    n = len(hash_hex)
    for i in range(dim):
        start = (i * 2) % n
        chunk = hash_hex[start:start + 2]
        if len(chunk) < 2:
            chunk = (chunk + hash_hex[:2])[:2]
        val = int(chunk, 16) / 255.0 * 2 - 1
        out.append(round(val, 6))
    return out


class _NullCtx:
    """Context manager no-op — cobre `conn.transaction()`/`conn.cursor()`.
    O `cur`/`conn` que ele produz nunca é usado para `.execute()` real: toda
    escrita passa pelos stubs de `_upsert_entity`/`_upsert_rel`/
    `_replace_match_chunks`, que ignoram o argumento `cur`."""

    def __enter__(self) -> _NullCtx:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeConnection:
    def transaction(self) -> _NullCtx:
        return _NullCtx()

    def cursor(self) -> _NullCtx:
        return _NullCtx()


class _FakeConnectCtx:
    def __enter__(self) -> _FakeConnection:
        return _FakeConnection()

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakePsycopg:
    """Substitui `gold.psycopg` inteiro — `ingest_all` nunca abre uma conexão
    de rede real."""

    @staticmethod
    def connect(dsn: str, autocommit: bool = True) -> _FakeConnectCtx:
        return _FakeConnectCtx()


class GoldCaptureHarness:
    """Estado mutável de uma captura: acumula a `GoldProjection` conforme os
    stubs são chamados pelo `gold.ingest_all` real.

    Correlação produtor→entidade (hash do input do tagger/constraints/
    embedding de entidade) usa filas FIFO: cada `_tag_edital`/
    `produce_from_text`/`embed_query` é sempre chamado imediatamente antes do
    `_upsert_entity` correspondente no código real (mesmo bloco `try`, mesma
    thread, sem chamada concorrente entre push e pop) — confirmado lendo
    `gold.py` linha a linha para as 4 fontes + `_get_agency`. Isso só é seguro
    porque os stubs nunca lançam exceção entre o push e o pop; fixtures mal
    formadas que fizessem o código real lançar entre essas duas chamadas
    quebrariam a correlação (limitação conhecida, aceitável para fixtures
    curadas e herméticas).
    """

    def __init__(self) -> None:
        self.projection = equivalence.GoldProjection()
        self._id_to_key: dict[str, str] = {}
        self._embed_query_queue: list[str | None] = []
        self._tagger_queue: list[str | None] = []
        self._constraints_queue: list[str | None] = []
        self._programa_native_to_id: dict[str, str] = {}

    # -- seam: embeddings -----------------------------------------------

    def stub_embed_query(self, text: str) -> list[float]:
        h = equivalence.sha256_of(text)
        self._embed_query_queue.append(h)
        return _synthetic_vector(h, STUB_EMBEDDING_DIM)

    def stub_embed_match_chunks(self, chunks: list[dict]) -> list[list[float]]:
        return [
            _synthetic_vector(equivalence.sha256_of(c.get("text")), STUB_EMBEDDING_DIM)
            for c in chunks
        ]

    # -- seam: LLM tagger -------------------------------------------------

    def stub_tag_edital(self, thematic_text: str, *, client: Any, model: str) -> tuple[list[str], list[str]]:
        h = equivalence.sha256_of(thematic_text)
        self._tagger_queue.append(h)
        # Saída FIXA e determinística — nunca uma chamada LLM real (spec:
        # snapshot não pode se apresentar como saída real de modelo).
        return (["Multissetorial"], ["stub-tag"])

    # -- seam: constraints producer ---------------------------------------

    def stub_produce_from_text(
        self, text: str, *, client: Any = None, model: str | None = None
    ) -> tuple[list[dict], list[str], list[str], list[str]]:
        h = equivalence.sha256_of(text)
        self._constraints_queue.append(h)
        if not (text or "").strip():
            return [], [], [], []
        return (
            [{"tipo": "porte", "op": "in", "valor": ["mei", "me", "epp"]}],
            ["Empresa brasileira em operação."],
            [],
            ["startups"],
        )

    # -- seam: banco --------------------------------------------------------

    def stub_upsert_entity(self, cur: Any, **f: Any) -> str:
        kind, source, native_id = f["kind"], f["source"], f["native_id"]

        embedding = f.get("embedding")
        embedding_hash = self._embed_query_queue.pop(0) if self._embed_query_queue else None
        embedding_model = STUB_EMBEDDING_MODEL if embedding is not None else None
        embedding_dim = len(embedding) if embedding is not None else None

        tagger_hash = None
        if kind == "edital":
            tagger_hash = self._tagger_queue.pop(0) if self._tagger_queue else None

        constraints_hash = None
        if kind in ("edital", "programa"):
            constraints_hash = self._constraints_queue.pop(0) if self._constraints_queue else None

        deadline = f.get("deadline")
        deadline_iso = deadline.isoformat() if hasattr(deadline, "isoformat") else deadline
        verificado = f.get("verificado_em")
        verificado_iso = verificado.isoformat() if hasattr(verificado, "isoformat") else verificado

        record = equivalence.make_entity_record(
            kind=kind, source=source, native_id=native_id,
            name=f.get("name"), description=f.get("description"),
            mecanismo=f.get("mecanismo"), formato=f.get("formato"),
            setores=f.get("setores"), tecnologias_tags=f.get("tecnologias_tags"),
            status=f.get("status"), deadline=deadline_iso, uf=f.get("uf"),
            ticket_min=f.get("ticket_min"), ticket_max=f.get("ticket_max"),
            constraints=f.get("constraints"), requisitos_texto=f.get("requisitos_texto"),
            curated=f.get("curated", False), verificado_em=verificado_iso,
            metadata=f.get("metadata"),
            embedding_input_hash=embedding_hash, embedding_model=embedding_model,
            embedding_dim=embedding_dim,
            tagger_input_hash=tagger_hash, constraints_input_hash=constraints_hash,
        )
        key = self.projection.add_entity(record)
        synthetic_id = f"synthetic:{key}"
        self._id_to_key[synthetic_id] = key
        if kind == "programa":
            self._programa_native_to_id[native_id] = synthetic_id
        return synthetic_id

    def stub_upsert_rel(
        self, cur: Any, source_id: str, target_id: str, rtype: str, properties: dict | None = None
    ) -> None:
        record = equivalence.make_relation_record(
            source_entity_key=self._id_to_key.get(source_id, source_id),
            target_entity_key=self._id_to_key.get(target_id, target_id),
            type=rtype,
            properties=properties,
        )
        self.projection.add_relation(record)

    def stub_replace_match_chunks(
        self, cur: Any, entity_id: str, chunks: list[dict], embeddings: list[list[float]]
    ) -> int:
        ek = self._id_to_key.get(entity_id, entity_id)
        records = [
            equivalence.make_chunk_record(
                idx=i,
                section_path=c.get("section_path"),
                kind=c.get("kind"),
                text=c["text"],
                embedding_input_hash=equivalence.sha256_of(c.get("text")),
                embedding_model=STUB_EMBEDDING_MODEL,
                embedding_dim=STUB_EMBEDDING_DIM,
            )
            for i, c in enumerate(chunks)
        ]
        self.projection.set_match_chunks(ek, records)
        return len(records)

    def stub_programa_id_map(self, conn: Any) -> dict[str, str]:
        return dict(self._programa_native_to_id)

    def stub_existing_hash(self, cur: Any, source: str, native_id: str) -> str | None:
        # Captura sempre "fresh": nunca há entidade pré-existente, então o
        # skip-by-unchanged-hash do gold nunca dispara.
        return None

    # -- aplicação dos patches ----------------------------------------------

    def apply_patches(self, monkeypatch: Any, fixtures_dir: Path) -> None:
        from radar.core.kg import constraints_producer, gold
        from radar.core.retrieval import embedder

        # paths — os 3 exigidos (STRUCTURED_DIR é materializado à parte de
        # SILVER_DIR no momento do import de gold.py, então precisa de patch
        # próprio).
        monkeypatch.setattr(gold, "SILVER_DIR", fixtures_dir / "silver")
        monkeypatch.setattr(gold, "STRUCTURED_DIR", fixtures_dir / "silver" / "structured_docs")
        monkeypatch.setattr(gold, "BRONZE_DIR", fixtures_dir / "bronze")

        # banco: nenhuma escrita real. `_get_agency` NÃO é interceptado à
        # parte — sua única escrita passa por `_upsert_entity` (já stubado) e
        # seu embedding por `embed_query` (já stubado); interceptar os dois
        # já isola `_get_agency` transitivamente sem duplicar a lógica de
        # upsert de agência aqui.
        monkeypatch.setattr(gold, "_upsert_entity", self.stub_upsert_entity)
        monkeypatch.setattr(gold, "_upsert_rel", self.stub_upsert_rel)
        monkeypatch.setattr(gold, "_replace_match_chunks", self.stub_replace_match_chunks)
        monkeypatch.setattr(gold, "_programa_id_map", self.stub_programa_id_map)
        monkeypatch.setattr(gold, "_existing_hash", self.stub_existing_hash)
        monkeypatch.setattr(gold, "psycopg", _FakePsycopg())
        monkeypatch.setenv("DATABASE_URL", "postgresql://fixture:fixture@localhost/fixture")

        # LLM tagger + constraints producer — stubs determinísticos.
        monkeypatch.setattr(gold, "_tag_edital", self.stub_tag_edital)
        monkeypatch.setattr(constraints_producer, "produce_from_text", self.stub_produce_from_text)

        # embeddings — stubs determinísticos, vetor sintético, nunca a API real.
        monkeypatch.setattr(embedder, "embed_query", self.stub_embed_query)
        monkeypatch.setattr(gold, "_embed_match_chunks", self.stub_embed_match_chunks)

        # limpa caches de módulo entre execuções (obrigatório: os caches não
        # sabem que BRONZE_DIR/SILVER_DIR mudaram).
        gold._programa_detection.cache_clear()
        gold._load_bronze.cache_clear()
        gold._section_rules.cache_clear()


def run_capture(
    monkeypatch: Any, fixtures_dir: Path | None = None, *, sources: list[str] | None = None
) -> tuple[equivalence.GoldProjection, dict]:
    """Roda `gold.ingest_all()` real sobre as fixtures, sob os stubs acima, e
    devolve `(GoldProjection, stats)`. `monkeypatch` é o fixture pytest do
    teste chamador (ou um `pytest.MonkeyPatch()` standalone para uso fora de
    um teste, ver `regenerate_baseline`)."""
    from radar.core.kg import gold

    fixtures_dir = fixtures_dir or DEFAULT_FIXTURES_DIR
    harness = GoldCaptureHarness()
    harness.apply_patches(monkeypatch, fixtures_dir)
    stats = gold.ingest_all(sources=sources, skip_unchanged=True)
    return harness.projection, dict(stats)


def regenerate_baseline(fixtures_dir: Path | None = None) -> dict:
    """Recaptura a projeção e sobrescreve `baseline_projection.json`.

    Comando (a partir da raiz do worktree, venv do projeto):

        PYTHONPATH=src /Users/lucasborges/radar_editais/.venv/bin/python -c \\
            "from tests.helpers.gold_projection import regenerate_baseline; regenerate_baseline()"
    """
    import pytest

    fixtures_dir = fixtures_dir or DEFAULT_FIXTURES_DIR
    with pytest.MonkeyPatch.context() as mp:
        # Fora de um teste pytest, o autouse `_ensure_llm_keys` do conftest.py
        # não roda — replica aqui só o suficiente para `make_client(...)` não
        # levantar KeyError. Nunca chega a sair da máquina (LLM real está
        # stubado por `apply_patches`).
        if not os.environ.get("OPENAI_API_KEY"):
            mp.setenv("OPENAI_API_KEY", "test-dummy-openai_api_key")
        projection, _stats = run_capture(mp, fixtures_dir)
    out_path = fixtures_dir / "baseline_projection.json"
    out_path.write_text(projection.to_json() + "\n", encoding="utf-8")
    return projection.to_dict()


__all__ = [
    "DEFAULT_FIXTURES_DIR",
    "STUB_EMBEDDING_MODEL",
    "STUB_EMBEDDING_DIM",
    "GoldCaptureHarness",
    "run_capture",
    "regenerate_baseline",
]
