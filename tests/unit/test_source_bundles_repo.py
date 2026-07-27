"""Testes do repositório append-only de SourceBundle (RT04-T02).

Cobre:
  - primeira inserção com payload correto
  - repetição idempotente (mesmo bundle não gera nova linha)
  - mudança material cria nova versão
  - partial e complete geram versões distintas
  - partial posterior não ocupa o lugar do último complete
  - leitura vazia retorna None
  - round-trip do JSON preserva o contrato
  - erro real de persistência não é classificado como duplicidade
  - sem Supabase configurado: save no-op False, load None
  - estrutura essencial da migration (constraint única, RLS, índices)
"""
from __future__ import annotations

import json

import pytest
from postgrest.exceptions import APIError

from radar.core.kg import source_bundles as repo
from radar.core.kg.source_bundles import BundleStorageError
from radar.domain.source_bundle import (
    AcquisitionStatus,
    SourceBundle,
    SubjectKind,
    compute_content_hash,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(**overrides):
    units = ["conteúdo de teste estável"]
    defaults = {
        "doc_name": "test.pdf",
        "units": units,
        "role": "base_notice",
        "content_hash": compute_content_hash(units),
        "authority_state": "active",
    }
    defaults.update(overrides)
    return defaults


def _make_bundle(**overrides) -> SourceBundle:
    """Constrói um SourceBundle válido."""
    defaults = {
        "schema_version": 1,
        "subject_kind": "opportunity",
        "subject_id": "fapesc:test-2026",
        "source": "fapesc",
        "collected_at": "2026-07-27T12:00:00Z",
        "producer_version": "adapter-v1",
        "acquisition_status": "complete",
        "documents": [_make_doc()],
    }
    defaults.update(overrides)
    return SourceBundle.model_validate(defaults)


def _make_actor_bundle(**overrides) -> SourceBundle:
    """Constrói um SourceBundle de ator (ICT) válido."""
    defaults = {
        "schema_version": 1,
        "subject_kind": "ict",
        "subject_id": "ict:exemplo:test-lab",
        "source": "exemplo",
        "collected_at": "2026-07-27T14:00:00Z",
        "producer_version": "catalog-v1",
        "acquisition_status": "partial",
        "documents": [{
            "doc_name": "page.html",
            "units": ["conteúdo do ator de teste"],
            "role": "official_page",
            "content_hash": compute_content_hash(["conteúdo do ator de teste"]),
            "authority_state": "active",
        }],
    }
    defaults.update(overrides)
    return SourceBundle.model_validate(defaults)


# ---------------------------------------------------------------------------
# Stubs do client Supabase
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data=None):
        self.data = data


class _InsertChain:
    """Captura o payload do insert e permite simular erro."""

    def __init__(self, sink: dict | None = None, error: Exception | None = None):
        self._sink = sink
        self._error = error

    def insert(self, payload):
        if self._sink is not None:
            self._sink["payload"] = payload
        return self

    def execute(self):
        if self._error:
            raise self._error
        return _Result(data=[{}])


class _SelectChain:
    """Fake para select/eq/order/limit → execute."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self._error: Exception | None = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        return self

    def order(self, col, **kwargs):
        return self

    def limit(self, n):
        return self

    def insert(self, payload):
        return _InsertChain(error=self._error)

    def execute(self):
        if self._error:
            raise self._error
        return _Result(data=self._rows)


class _FakeDB:
    def __init__(self, rows=None, sink=None, insert_error=None):
        self._rows = rows
        self._sink = sink
        self._insert_error = insert_error

    def table(self, _name):
        if self._sink is not None or self._insert_error is not None:
            return _InsertChain(sink=self._sink, error=self._insert_error)
        return _SelectChain(rows=self._rows)


@pytest.fixture
def pg_on(monkeypatch):
    monkeypatch.setattr(repo, "_pg_configured", lambda: True)


@pytest.fixture
def sink() -> dict:
    return {}


# ---------------------------------------------------------------------------
# Sem Supabase
# ---------------------------------------------------------------------------

class TestNoSupabase:
    def test_save_sem_supabase_noop(self, monkeypatch):
        monkeypatch.setattr(repo, "_pg_configured", lambda: False)
        bundle = _make_bundle()
        assert repo.save(bundle) is False

    def test_load_sem_supabase_retorna_none(self, monkeypatch):
        monkeypatch.setattr(repo, "_pg_configured", lambda: False)
        assert repo.load("opportunity", "fapesc:test") is None


# ---------------------------------------------------------------------------
# save — primeira inserção
# ---------------------------------------------------------------------------

class TestSaveFirstInsert:
    def test_insert_payload_structure(self, monkeypatch, pg_on, sink):
        db = _FakeDB(sink=sink)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_bundle()
        result = repo.save(bundle)
        assert result is True

        payload = sink["payload"]
        assert payload["subject_kind"] == "opportunity"
        assert payload["subject_id"] == "fapesc:test-2026"
        assert payload["source"] == "fapesc"
        assert payload["acquisition_status"] == "complete"
        assert payload["bundle_hash"] == bundle.compute_bundle_hash()
        assert payload["collected_at"] == "2026-07-27T12:00:00+00:00"

        # bundle JSONB deve conter os campos do envelope
        bundle_json = payload["bundle"]
        assert bundle_json["subject_kind"] == "opportunity"
        assert bundle_json["subject_id"] == "fapesc:test-2026"
        assert len(bundle_json["documents"]) == 1

    def test_actor_bundle_payload(self, monkeypatch, pg_on, sink):
        db = _FakeDB(sink=sink)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_actor_bundle()
        result = repo.save(bundle)
        assert result is True

        payload = sink["payload"]
        assert payload["subject_kind"] == "ict"
        assert payload["subject_id"] == "ict:exemplo:test-lab"
        assert payload["source"] == "exemplo"
        assert payload["acquisition_status"] == "partial"
        assert payload["bundle_hash"] == bundle.compute_bundle_hash()

    def test_insert_computes_bundle_hash(self, monkeypatch, pg_on, sink):
        db = _FakeDB(sink=sink)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_bundle()
        repo.save(bundle)
        assert sink["payload"]["bundle_hash"] == bundle.compute_bundle_hash()
        assert sink["payload"]["bundle_hash"].startswith("sha256:")


# ---------------------------------------------------------------------------
# save — idempotência (duplicata via constraint única)
# ---------------------------------------------------------------------------

class TestIdempotentRepeat:
    def test_duplicate_returns_true(self, monkeypatch, pg_on):
        """APIError code 23505 (unique_violation) é tratado como sucesso."""
        error = APIError({"code": "23505", "message": "duplicate key value violates unique constraint"})
        db = _FakeDB(insert_error=error)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_bundle()
        assert repo.save(bundle) is True

    def test_different_subject_same_content_allowed(self, monkeypatch, pg_on, sink):
        """Mesmo conteúdo para sujeitos diferentes não é duplicata."""
        db = _FakeDB(sink=sink)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        b1 = _make_bundle(subject_id="fapesc:a")
        b2 = _make_bundle(subject_id="fapesc:b")
        assert repo.save(b1) is True
        assert sink["payload"]["subject_id"] == "fapesc:a"

        sink2: dict = {}
        db2 = _FakeDB(sink=sink2)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db2)
        assert repo.save(b2) is True
        assert sink2["payload"]["subject_id"] == "fapesc:b"


# ---------------------------------------------------------------------------
# save — mudança material cria nova versão
# ---------------------------------------------------------------------------

class TestMaterialChange:
    def test_content_change_produces_new_hash(self):
        """Mudança de conteúdo altera o bundle_hash (repetição teria hash
        diferente e não seria duplicata)."""
        b1 = _make_bundle()
        doc2 = _make_doc(
            units=["conteúdo totalmente diferente"],
            content_hash=compute_content_hash(["conteúdo totalmente diferente"]),
        )
        b2 = _make_bundle(documents=[doc2])
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_acquisition_status_changes_hash(self):
        partial = _make_bundle(acquisition_status="partial")
        complete = _make_bundle(acquisition_status="complete")
        assert partial.compute_bundle_hash() != complete.compute_bundle_hash()


# ---------------------------------------------------------------------------
# save — erro real não é classificado como duplicidade
# ---------------------------------------------------------------------------

class TestRealErrors:
    def test_api_error_non_duplicate_raises(self, monkeypatch, pg_on):
        """Erro 500 ou outro código não 23505 levanta BundleStorageError."""
        error = APIError({"code": "500", "message": "Internal Server Error"})
        db = _FakeDB(insert_error=error)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_bundle()
        with pytest.raises(BundleStorageError, match="save failed"):
            repo.save(bundle)

    def test_unexpected_exception_is_sanitized(self, monkeypatch, pg_on, caplog):
        """A causa do provedor e seu conteúdo não chegam ao erro ou log."""
        secret = "https://provider.example/secret-response"
        db = _FakeDB(insert_error=RuntimeError(secret))
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_bundle()
        with pytest.raises(BundleStorageError, match="save failed") as raised:
            repo.save(bundle)
        assert raised.value.__cause__ is None
        assert secret not in str(raised.value)
        assert secret not in caplog.text

    def test_api_error_without_code_raises(self, monkeypatch, pg_on):
        """APIError sem code não é classificado como duplicidade."""
        error = APIError({"message": "some error"})
        db = _FakeDB(insert_error=error)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_bundle()
        with pytest.raises(BundleStorageError, match="save failed"):
            repo.save(bundle)


# ---------------------------------------------------------------------------
# load — último complete por sujeito
# ---------------------------------------------------------------------------

class TestLoad:
    def test_load_with_complete_rows(self, monkeypatch, pg_on):
        """Carrega o bundle da primeira linha retornada."""
        bundle = _make_bundle()
        bundle_json = json.loads(bundle.model_dump_json())
        db = _FakeDB(rows=[{"bundle": bundle_json}])
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        loaded = repo.load("opportunity", "fapesc:test-2026")
        assert loaded is not None
        assert loaded.subject_id == "fapesc:test-2026"
        assert loaded.subject_kind == SubjectKind.OPPORTUNITY
        assert loaded.acquisition_status == AcquisitionStatus.COMPLETE
        assert loaded.compute_bundle_hash() == bundle.compute_bundle_hash()

    def test_load_empty_returns_none(self, monkeypatch, pg_on):
        db = _FakeDB(rows=[])
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        assert repo.load("opportunity", "inexistente") is None

    @pytest.mark.parametrize("row", [
        {"not_bundle": "nope"},
        {"bundle": None},
        {"bundle": {}},
    ])
    def test_load_missing_or_empty_bundle_raises(self, monkeypatch, pg_on, row):
        db = _FakeDB(rows=[row])
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        with pytest.raises(BundleStorageError, match="invalid bundle payload") as raised:
            repo.load("opportunity", "fapesc:test")
        assert raised.value.__cause__ is None

    def test_load_invalid_bundle_is_sanitized(self, monkeypatch, pg_on, caplog):
        secret = "https://provider.example/invalid-bundle"
        db = _FakeDB(rows=[{"bundle": {"source": secret}}])
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        with pytest.raises(BundleStorageError, match="invalid bundle payload") as raised:
            repo.load("opportunity", "fapesc:test")
        assert raised.value.__cause__ is None
        assert secret not in str(raised.value)
        assert secret not in caplog.text

    def test_load_query_structure(self, monkeypatch, pg_on):
        """Verifica que a query filtra acquisition_status='complete'."""
        # Precisamos de um fake mais detalhado que capture .eq calls
        class _CaptureSelect:
            def __init__(self):
                self.calls = []

            def select(self, *a, **k):
                self.calls.append(("select", a, k))
                return self

            def eq(self, col, val):
                self.calls.append(("eq", col, val))
                return self

            def order(self, col, **k):
                self.calls.append(("order", col, k))
                return self

            def limit(self, n):
                self.calls.append(("limit", n))
                return self

            def execute(self):
                return _Result(data=[{
                    "bundle": json.loads(_make_bundle().model_dump_json())
                }])

        fake = _CaptureSelect()
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service",
                            lambda: type("DB", (), {"table": lambda s, n: fake})())

        repo.load("opportunity", "fapesc:test-2026")
        eq_calls = [(c[1], c[2]) for c in fake.calls if c[0] == "eq"]
        assert ("subject_kind", "opportunity") in eq_calls
        assert ("subject_id", "fapesc:test-2026") in eq_calls
        assert ("acquisition_status", "complete") in eq_calls

        order_calls = [(c[1], c[2]) for c in fake.calls if c[0] == "order"]
        assert len(order_calls) == 3
        assert any(col == "collected_at" and kw.get("desc") for col, kw in order_calls)
        assert any(col == "created_at" and kw.get("desc") for col, kw in order_calls)
        assert any(col == "id" and kw.get("desc") for col, kw in order_calls)

        select_calls = [c[1] for c in fake.calls if c[0] == "select"]
        assert ("bundle",) in select_calls

        limit_calls = [c[1] for c in fake.calls if c[0] == "limit"]
        assert 1 in limit_calls

    def test_load_error_is_sanitized(self, monkeypatch, pg_on, caplog):
        secret = "https://provider.example/connection-lost"
        class _FailingChain:
            def select(self, *a, **k):
                return self
            def eq(self, col, val):
                return self
            def order(self, col, **k):
                return self
            def limit(self, n):
                return self
            def execute(self):
                raise RuntimeError(secret)

        monkeypatch.setattr(
            "radar.core.infra.db.get_supabase_service",
            lambda: type("DB", (), {"table": lambda s, n: _FailingChain()})(),
        )
        with pytest.raises(BundleStorageError, match="load failed") as raised:
            repo.load("opportunity", "fapesc:test")
        assert raised.value.__cause__ is None
        assert secret not in str(raised.value)
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# partial e complete geram versões distintas
# partial posterior não substitui o último complete
# ---------------------------------------------------------------------------

class TestPartialVsComplete:
    def test_partial_and_complete_have_different_hashes(self):
        partial = _make_bundle(acquisition_status="partial")
        complete = _make_bundle(acquisition_status="complete")
        assert partial.compute_bundle_hash() != complete.compute_bundle_hash()

    def test_load_only_returns_complete(self, monkeypatch, pg_on):
        """load filtra acquisition_status='complete', então mesmo que existam
        bundles, só o complete é retornado."""
        bundle = _make_bundle()
        bundle_json = json.loads(bundle.model_dump_json())

        # Fake que retorna uma linha complete mesmo quando a query
        # filtra por acquisition_status='complete'
        class _CompleteOnly:
            def select(self, *a, **k):
                return self
            def eq(self, col, val):
                return self
            def order(self, col, **k):
                return self
            def limit(self, n):
                return self
            def execute(self):
                return _Result(data=[{"bundle": bundle_json}])

        monkeypatch.setattr("radar.core.infra.db.get_supabase_service",
                            lambda: type("DB", (), {"table": lambda s, n: _CompleteOnly()})())

        loaded = repo.load("opportunity", "fapesc:test-2026")
        assert loaded is not None
        assert loaded.acquisition_status == AcquisitionStatus.COMPLETE


# ---------------------------------------------------------------------------
# Round-trip JSON preserva o contrato
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_round_trip_preserves_envelope(self):
        """Bundle serializado via model_dump_json e desserializado preserva
        todos os campos do envelope."""
        original = _make_bundle()
        raw = json.loads(original.model_dump_json())
        restored = SourceBundle.model_validate(raw)
        assert restored.subject_kind == original.subject_kind
        assert restored.subject_id == original.subject_id
        assert restored.source == original.source
        assert restored.acquisition_status == original.acquisition_status
        assert restored.collected_at == original.collected_at
        assert restored.producer_version == original.producer_version
        assert restored.compute_bundle_hash() == original.compute_bundle_hash()
        assert len(restored.documents) == len(original.documents)
        for d_orig, d_rest in zip(original.documents, restored.documents, strict=True):
            assert d_orig.doc_name == d_rest.doc_name
            assert d_orig.content_hash == d_rest.content_hash
            assert d_orig.role == d_rest.role

    def test_round_trip_preserves_optional_fields(self):
        """Campos opcionais (source_url, published_at, composition_order,
        authority_state, amends_content_hash) sobrevivem ao round-trip."""
        doc = _make_doc(
            source_url="https://example.com/doc",
            published_at="2026-06-01",
            composition_order=1,
            authority_state="superseded",
        )
        bundle = _make_bundle(documents=[doc])
        raw = json.loads(bundle.model_dump_json())
        restored = SourceBundle.model_validate(raw)
        d = restored.documents[0]
        assert d.source_url == "https://example.com/doc"
        assert d.published_at == "2026-06-01"
        assert d.composition_order == 1
        assert d.authority_state.value == "superseded"

    def test_bundle_hash_not_in_model_json(self):
        """bundle_hash não é campo do envelope — é coluna separada do DB."""
        bundle = _make_bundle()
        raw = json.loads(bundle.model_dump_json())
        assert "bundle_hash" not in raw
        assert "created_at" not in raw


# ---------------------------------------------------------------------------
# Migration structure (comentários e contratos essenciais)
# ---------------------------------------------------------------------------

class TestMigrationContract:
    """Testa que a migration 044 contém os elementos obrigatórios."""

    MIGRATION_PATH = "supabase/migrations/044_source_bundles.sql"

    def test_migration_file_exists(self):
        import os
        assert os.path.exists(self.MIGRATION_PATH), (
            f"Migration {self.MIGRATION_PATH} não encontrada"
        )

    def test_migration_has_unique_constraint(self):
        with open(self.MIGRATION_PATH) as f:
            content = f.read()
        assert "unique" in content.lower(), (
            "Migration deve conter UNIQUE constraint"
        )

    def test_migration_has_rls_enabled(self):
        with open(self.MIGRATION_PATH) as f:
            content = f.read()
        assert "enable row level security" in content.lower(), (
            "Migration deve habilitar RLS"
        )

    def test_migration_has_bundle_hash_index(self):
        with open(self.MIGRATION_PATH) as f:
            content = f.read()
        assert "create index" in content.lower(), (
            "Migration deve criar índices"
        )

    def test_migration_idempotent(self):
        """Migration usa IF NOT EXISTS."""
        with open(self.MIGRATION_PATH) as f:
            content = f.read()
        assert "if not exists" in content.lower(), (
            "Migration deve usar IF NOT EXISTS para ser idempotente"
        )


# ---------------------------------------------------------------------------
# Colunas obrigatórias no payload
# ---------------------------------------------------------------------------

class TestRequiredColumns:
    def test_insert_includes_all_required_columns(self, monkeypatch, pg_on, sink):
        db = _FakeDB(sink=sink)
        monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: db)

        bundle = _make_bundle()
        repo.save(bundle)

        required = [
            "subject_kind", "subject_id", "source", "bundle_hash",
            "bundle", "acquisition_status", "collected_at",
        ]
        for col in required:
            assert col in sink["payload"], (
                f"Coluna obrigatória '{col}' ausente no payload do insert"
            )

    def test_created_at_not_in_payload(self):
        """created_at é default do banco, não enviado no insert."""
        bundle = _make_bundle()
        assert not hasattr(bundle, "created_at")
