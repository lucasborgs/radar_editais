from radar.core.services.consultant import ConsultantValidationError
from radar.core.services.grounded_writing import GroundedWriting
from radar.domain.consultant import (
    CaminhoInovacao,
    ConsultantState,
    EvidenceReference,
    ProjetoInovacao,
)


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
