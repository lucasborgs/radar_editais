import pytest
from pydantic import ValidationError

from radar.api.routers.grounded_writing import GroundedWritingOpenRequest
from radar.core.services.consultant import ConsultantValidationError
from radar.core.services.grounded_writing import GroundedWriting
from radar.core.services.writing_session import WritingSession
from radar.domain.consultant import (
    CaminhoInovacao,
    ConsultantState,
    EvidenceReference,
    ProjetoInovacao,
)
from radar.domain.user_profile import CompanyProfile


def _selected_state(*, selected: bool = True) -> tuple[ConsultantState, CaminhoInovacao]:
    project = ProjetoInovacao(workspace_id="w1", profile_version="v1", brief_id="b1")
    path = CaminhoInovacao(
        status="selected" if selected else "proposed",
        tipo="subvencao",
        kind="subvencao",
        project_id=project.id,
        entity_ref="finep:1",
        opportunity_ref="finep:1",
        requirements=["Empresa brasileira com projeto de P&D"],
        facts=["Oportunidade vigente no catálogo."],
        gaps=["Confirmar a contrapartida."],
        recommendation="Aprofundar a proposta técnica.",
        next_step="Abrir a escrita.",
        evidence=[EvidenceReference(ref="finep:1", label="Edital", locator="p. 4")],
    )
    state = ConsultantState(
        conversation_id="c1",
        workspace_id="w1",
        project=project,
        project_id=project.id,
        paths=[path],
        path_ids=[path.id],
        selected_path_id=path.id if selected else None,
    )
    return state, path


def test_grounded_context_freezes_path_scope_and_evidence():
    state, path = _selected_state()

    context = GroundedWriting.build_context(
        state, path, "proposta_tecnica", ["library-1"]
    )

    assert context.project_id == state.project_id
    assert context.path_id == path.id
    assert context.retrieval_scope == ["finep:1"]
    assert context.allowed_materials == ["library-1"]
    assert context.source_refs[0].locator == "p. 4"
    assert context.gaps == ["Confirmar a contrapartida."]


def test_grounded_writing_requires_selected_path():
    state, path = _selected_state(selected=False)

    try:
        GroundedWriting.build_context(state, path, "proposta_tecnica", [])
    except ConsultantValidationError as exc:
        assert "selecionado" in str(exc)
    else:
        raise AssertionError("um caminho não selecionado não pode abrir escrita")


def test_grounded_writing_rejects_normative_artifact_for_open_path():
    state, path = _selected_state()
    path.formal_instrument = False

    try:
        GroundedWriting.build_context(state, path, "proposta_tecnica", [])
    except ConsultantValidationError as exc:
        assert "normativo" in str(exc)
    else:
        raise AssertionError("caminho aberto não pode virar proposta normativa")


def test_grounded_outline_is_typed_and_open_path_has_no_rag_scope():
    state, path = _selected_state()
    path.formal_instrument = False

    context = GroundedWriting.build_context(
        state, path, "abordagem_mercado", ["library-1"]
    )

    assert context.formal_instrument is False
    assert context.retrieval_scope == []
    assert GroundedWriting.build_outline("abordagem_mercado")[-1].startswith("4.")


def test_artifact_alias_is_normalized():
    assert GroundedWriting.normalize_artifact_type("technical-proposal") == "proposta_tecnica"


class _Result:
    def __init__(self, data=None):
        self.data = data


class _WritingSessionsDb:
    """DB mínimo que preserva o payload de writing_sessions entre reloads."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.application_logs: list[dict] = []
        self.fail_after_insert_once = False

    def table(self, name):
        return _Query(self, name)


class _Query:
    def __init__(self, db: _WritingSessionsDb, name: str):
        self.db = db
        self.name = name
        self.filters: dict[str, object] = {}
        self.payload: dict | None = None
        self.operation = "select"

    def insert(self, payload):
        self.payload = payload
        self.operation = "insert"
        return self

    def update(self, payload):
        self.payload = payload
        self.operation = "update"
        return self

    def select(self, *_fields):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def maybe_single(self):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.name == "writing_sessions" and self.operation == "insert":
            session_id = str((self.payload or {}).get("id") or "grounded-session")
            open_key = (self.payload or {}).get("grounded_open_key")
            if session_id in self.db.sessions or any(
                open_key and row.get("grounded_open_key") == open_key
                for row in self.db.sessions.values()
            ):
                raise RuntimeError("duplicate grounded writing session")
            row = {"id": session_id, "created_at": "2026-08-14T00:00:00Z", **(self.payload or {})}
            self.db.sessions[session_id] = row
            if self.db.fail_after_insert_once:
                self.db.fail_after_insert_once = False
                raise RuntimeError("connection dropped after insert")
            return _Result([row])
        if self.name == "writing_sessions" and self.operation == "update":
            session_id = str(self.filters["id"])
            self.db.sessions[session_id].update(self.payload or {})
            return _Result([self.db.sessions[session_id]])
        if self.name == "writing_sessions":
            row = self.db.sessions.get(str(self.filters.get("id", "")))
            if row is None and self.filters.get("grounded_open_key"):
                row = next((
                    item for item in self.db.sessions.values()
                    if item.get("workspace_id") == self.filters.get("workspace_id")
                    and item.get("grounded_open_key") == self.filters.get("grounded_open_key")
                ), None)
            return _Result(row)
        if self.name == "application_log" and self.operation == "insert":
            self.db.application_logs.append(dict(self.payload or {}))
            return _Result([self.db.application_logs[-1]])
        # Não existe application_log para vincular e a sessão ainda não tem turnos.
        return _Result([])


class _Repository:
    def __init__(self, state: ConsultantState):
        self.state = state

    def load(self, _db, _conversation_id, _workspace_id):
        return self.state


def test_grounded_open_persists_outline_plan_and_context_before_reload(monkeypatch):
    """A abertura fundamentada deve criar uma sessão já recuperável por reload."""
    import radar.core.kg.temporal as temporal_module
    import radar.core.services.grounded_writing as grounded_module

    state, path = _selected_state()
    db = _WritingSessionsDb()
    profile = CompanyProfile(nome="Empresa", tipo_entidade="empresa", trl=4)
    service = GroundedWriting(repository=_Repository(state))

    monkeypatch.setattr(grounded_module, "load_library_items", lambda *_args: [])
    monkeypatch.setattr(grounded_module, "profile_from_workspace", lambda *_args: profile)
    monkeypatch.setattr(WritingSession, "_build_source_card_context", lambda _self: "")
    monkeypatch.setattr(WritingSession, "_build_reflection_context", lambda *_args: "")
    monkeypatch.setattr(WritingSession, "_resolve_playbook", lambda _self: None)
    monkeypatch.setattr(temporal_module, "render_temporal_block", lambda _edital_id: "")

    opened = service.open(
        db,
        "w1",
        conversation_id="c1",
        path_id=path.id,
        artifact_type="proposta_tecnica",
    )

    persisted = db.sessions[opened["writing_session_id"]]
    expected_outline = GroundedWriting.build_outline("proposta_tecnica")
    assert persisted["proposal_outline"] == expected_outline
    assert persisted["section_drafts"]["__plan__"]["artifact_type"] == "proposta_tecnica"
    assert persisted["writing_context"]["path_id"] == path.id
    assert persisted["writing_context"]["requirements"] == path.requirements
    assert db.application_logs == [{
        "workspace_id": "w1",
        "edital_id": path.opportunity_ref,
        "session_id": opened["writing_session_id"],
        "status": "proposta_iniciada",
    }]

    reloaded = WritingSession(
        db=db,
        workspace_id="w1",
        profile=profile,
        session_id=opened["writing_session_id"],
        mode="proposal",
        allow_incomplete_profile=True,
    )
    info = reloaded.get_info()
    assert info["section_titles"] == expected_outline
    assert info["plan"] == persisted["section_drafts"]["__plan__"]
    assert info["writing_context"]["path_id"] == path.id


def test_grounded_open_reuses_one_session_for_retry(monkeypatch):
    import radar.core.kg.temporal as temporal_module
    import radar.core.services.grounded_writing as grounded_module

    state, path = _selected_state()
    db = _WritingSessionsDb()
    profile = CompanyProfile(nome="Empresa", tipo_entidade="empresa", trl=4)
    service = GroundedWriting(repository=_Repository(state))

    monkeypatch.setattr(grounded_module, "load_library_items", lambda *_args: [])
    monkeypatch.setattr(grounded_module, "profile_from_workspace", lambda *_args: profile)
    monkeypatch.setattr(WritingSession, "_build_source_card_context", lambda _self: "")
    monkeypatch.setattr(WritingSession, "_build_reflection_context", lambda *_args: "")
    monkeypatch.setattr(WritingSession, "_resolve_playbook", lambda _self: None)
    monkeypatch.setattr(temporal_module, "render_temporal_block", lambda _edital_id: "")

    first = service.open(
        db, "w1", conversation_id="c1", path_id=path.id,
        artifact_type="proposta_tecnica",
    )
    retry = service.open(
        db, "w1", conversation_id="c1", path_id=path.id,
        artifact_type="proposta_tecnica",
    )

    assert retry["writing_session_id"] == first["writing_session_id"]
    assert len(db.sessions) == 1


def test_grounded_open_recovers_session_when_insert_response_is_lost(monkeypatch):
    import radar.core.kg.temporal as temporal_module
    import radar.core.services.grounded_writing as grounded_module

    state, path = _selected_state()
    db = _WritingSessionsDb()
    db.fail_after_insert_once = True
    profile = CompanyProfile(nome="Empresa", tipo_entidade="empresa", trl=4)
    service = GroundedWriting(repository=_Repository(state))

    monkeypatch.setattr(grounded_module, "load_library_items", lambda *_args: [])
    monkeypatch.setattr(grounded_module, "profile_from_workspace", lambda *_args: profile)
    monkeypatch.setattr(WritingSession, "_build_source_card_context", lambda _self: "")
    monkeypatch.setattr(WritingSession, "_build_reflection_context", lambda *_args: "")
    monkeypatch.setattr(WritingSession, "_resolve_playbook", lambda _self: None)
    monkeypatch.setattr(temporal_module, "render_temporal_block", lambda _edital_id: "")

    recovered = service.open(
        db, "w1", conversation_id="c1", path_id=path.id,
        artifact_type="proposta_tecnica",
    )

    assert recovered["writing_session_id"] in db.sessions
    assert len(db.sessions) == 1


def test_grounded_open_request_bounds_and_normalizes_allowed_materials():
    request = GroundedWritingOpenRequest(
        conversation_id="c1", path_id="p1", allowed_material_ids=[" material-1 ", "material-1"],
    )
    assert request.allowed_material_ids == ["material-1"]

    with pytest.raises(ValidationError):
        GroundedWritingOpenRequest(
            conversation_id="c1", path_id="p1", allowed_material_ids=["x"] * 21,
        )
    with pytest.raises(ValidationError):
        GroundedWritingOpenRequest(
            conversation_id="c1", path_id="p1", allowed_material_ids=["x" * 129],
        )
