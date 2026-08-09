from __future__ import annotations

from radar.core.services.consultant import (
    ConsultantGraph,
    DiscoveryDocumentIntelligence,
    GoldPathways,
    OpenPathways,
)
from radar.core.services.eligibility import evaluate_opportunity_detailed
from radar.domain.consultant import (
    BriefProjeto,
    CaminhoInovacao,
    ConsultantState,
    KnowledgeSignal,
    MemoryContext,
    ProjetoInovacao,
)


def _state() -> ConsultantState:
    return ConsultantState(conversation_id="c1", workspace_id="w1", profile_snapshot={"nome": "ACME"})


def _path(project_id: str) -> CaminhoInovacao:
    return CaminhoInovacao(
        tipo="financiamento",
        project_id=project_id,
        entity_ref="finep:1",
        facts=["Oferta catalogada."],
        inferences=["Afinidade precisa ser validada."],
        gaps=["Validar requisito."],
        recommendation="Compare o caminho.",
        next_step="Revise os requisitos.",
    )


class FakePathways:
    def propose(self, *, brief, project):
        return _path(project.id)


class FailingPathways:
    def propose(self, *, brief, project):
        raise RuntimeError("catalog down")


def test_graph_pauses_for_explicit_confirmation_then_persists_path():
    graph = ConsultantGraph(FakePathways())

    draft, _, events = graph.run(_state(), "Quero reduzir o desperdício com sensores")
    assert draft.brief is not None
    assert draft.brief.status == "draft"
    assert draft.pending_confirmation is True
    assert draft.project is None
    assert "confirmation_required" in events

    confirmed, _, events = graph.run(draft, "Confirmo este brief")
    assert confirmed.project is not None
    assert confirmed.project.status == "confirmed"
    assert len(confirmed.paths) == 1
    assert confirmed.paths[0].entity_ref == "finep:1"
    assert confirmed.path_ids == [confirmed.paths[0].id]
    assert confirmed.paths[0].gaps
    assert confirmed.next_step == "Revise os requisitos."
    assert [message.role for message in confirmed.messages] == ["user", "assistant", "user", "assistant"]
    assert "paths_proposed" in events


def test_catalog_failure_does_not_create_false_project():
    graph = ConsultantGraph(FailingPathways())
    draft, _, _ = graph.run(_state(), "Quero desenvolver uma solução")
    failed, answer, events = graph.run(draft, "sim")

    assert failed.project is None
    assert failed.needs_review is True
    assert "não foi criado" in answer
    assert "error" in events


def test_brief_keeps_origins_gaps_and_confirmed_snapshot():
    state = _state()
    state.profile_snapshot = {
        "solution_summary": "Sensoriamento remoto",
        "uf": "SP",
        "trl": 4,
    }
    state.profile_version = "2026-08-08T12:00:00+00:00"
    graph = ConsultantGraph(FakePathways())

    draft, _, events = graph.run(state, "Reduzir perdas na produção")
    assert draft.brief is not None
    assert draft.brief.source_refs["original_intention"] == ["user"]
    assert draft.brief.source_refs["solution_hypothesis"] == ["profile"]
    assert draft.gaps
    assert "inelegível" not in " ".join(draft.gaps).lower()
    assert "gap_question" in events

    revised, _, _ = graph.run(draft, "Hospitais e equipes de manutenção")
    assert revised.brief is not None
    assert revised.brief.affected_users == "Hospitais e equipes de manutenção"
    assert revised.brief.source_refs["affected_users"] == ["user"]
    assert revised.brief.version == 2
    assert revised.project is None

    confirmed, _, _ = graph.run(revised, "Confirmo o brief")
    assert confirmed.project is not None
    assert confirmed.project.profile_version == state.profile_version
    assert confirmed.project.profile_snapshot == state.profile_snapshot
    assert confirmed.project.brief_snapshot is not None
    assert confirmed.project.brief_snapshot.affected_users == revised.brief.affected_users
    assert confirmed.project.decision_history[0]["kind"] == "project_confirmed"


class FakeKnowledge:
    def search(self, query, *, profile, limit=3):
        return [{
            "id": "finep:1",
            "kind": "edital",
            "name": "Edital de inovação",
            "description": "Apoio a projetos de P&D",
            "card": {
                "id": "finep:1",
                "kind": "edital",
                "title": "Edital de inovação",
                "objective": "Apoio a projetos de P&D",
                "status": "ABERTA",
                "themes": ["Saúde"],
                "official_url": "https://example.test/edital",
                "source": "finep",
            },
        }]

    def get(self, entity_ref):
        return None


def test_gold_pathways_converts_catalog_entity_to_contract():
    state = _state()
    state.brief = BriefProjeto(
        original_intention="sensores para saúde",
        problem_hypothesis="problema",
        solution_hypothesis="solução",
    )
    state.project = ProjetoInovacao(workspace_id="w1", brief_id=state.brief.id, profile_snapshot={})
    path = GoldPathways(FakeKnowledge()).propose(brief=state.brief, project=state.project)

    assert path.entity_ref == "finep:1"
    assert path.tipo == "financiamento"
    assert path.evidence[0].ref == "finep:1"
    assert path.next_step


def test_detailed_eligibility_preserves_unknown_without_rejection():
    result = evaluate_opportunity_detailed(
        [{"tipo": "porte", "op": "in", "valor": ["me", "epp"]}],
        {},
    )
    assert result["status"] == "nao_verificada"
    assert result["evaluations"][0]["status"] == "unknown"


class NormativeKnowledge:
    def search(self, query, *, profile, limit=3):
        return [
            {
                "id": "finep:credito",
                "kind": "edital",
                "name": "Linha reembolsável",
                "description": "Financiamento reembolsável para inovação",
                "card": {"id": "finep:credito", "kind": "edital", "title": "Linha reembolsável", "status": "ABERTA"},
            },
            {
                "id": "finep:subvencao",
                "kind": "edital",
                "name": "Subvenção Saúde",
                "description": "Subvenção econômica não reembolsável para P&D",
                "card": {
                    "id": "finep:subvencao", "kind": "edital", "title": "Subvenção Saúde",
                    "status": "ABERTA", "validity_state": "active", "deadline": "31/12/2026",
                    "official_url": "https://example.test/subvencao", "source": "finep",
                    "key_requirements": ["Empresa brasileira com projeto de P&D"],
                    "constraints": [{"tipo": "porte", "op": "in", "valor": ["me", "epp"]}],
                    "provenance": {"requirements": {"state": "stated", "citations": [{
                        "document": "edital.pdf", "page": 4, "quote": "Empresas brasileiras",
                        "source_url": "https://example.test/subvencao", "collected_at": "2026-08-01",
                    }]}},
                },
            },
            {
                "id": "finep:closed",
                "kind": "edital",
                "name": "Subvenção encerrada",
                "description": "Subvenção não reembolsável encerrada",
                "card": {"id": "finep:closed", "kind": "edital", "title": "Subvenção encerrada", "status": "ENCERRADA", "validity_state": "closed"},
            },
        ]

    def get(self, entity_ref):
        return None


def test_normative_path_filters_credit_closed_and_keeps_rule_unknown():
    brief = BriefProjeto(original_intention="P&D em saúde")
    project = ProjetoInovacao(workspace_id="w1", brief_id=brief.id, profile_snapshot={})
    paths = GoldPathways(NormativeKnowledge()).propose_many(brief=brief, project=project)

    assert len(paths) == 1
    path = paths[0]
    assert path.tipo == "subvencao"
    assert path.temporal_state == "active"
    assert path.opportunity_ref == "finep:subvencao"
    assert path.rule_evaluations[0].status == "unknown"
    assert path.gaps
    assert path.evidence[0].locator_quality == "exact"
    assert path.evidence[0].document == "edital.pdf"


def test_open_document_package_preserves_sources_freshness_and_pending_review():
    package = DiscoveryDocumentIntelligence().ingest({
        "id": "open:challenge-1",
        "url": "https://corp.example/challenge-1",
        "title": "Desafio de manutenção",
        "agency": "Corporação X",
        "descricao": "Piloto de manutenção preditiva.",
        "opportunity_type": "desafio",
        "status": "pending",
        "raw": {
            "evidence_package": {
                "identity": {
                    "canonical_url": "https://corp.example/challenge-1",
                    "collected_at": "2026-08-08T10:00:00Z",
                },
                "page": {"content_hash": "sha256:page"},
                "deep_research": {"confidence": "high", "citations": [
                    {"title": "Fonte corroborante", "url": "https://corp.example/about"},
                ]},
            },
        },
    })

    assert package.document.source_url == "https://corp.example/challenge-1"
    assert package.review_state == "needs_review"
    assert package.freshness["age_state"] == "known"
    assert {item.source_role for item in package.evidence} == {"primary", "corroborating"}


def test_open_path_is_not_an_edital_and_requires_market_validation():
    document = DiscoveryDocumentIntelligence().ingest({
        "id": "open:challenge-2", "url": "https://corp.example/challenge-2",
        "title": "Desafio de qualidade", "agency": "Corporação Y",
        "descricao": "Piloto em planta industrial.", "opportunity_type": "desafio",
        "status": "pending",
    })

    class OpenKnowledgeDouble:
        def search_signals(self, query, *, profile, limit=5):
            return [KnowledgeSignal(
                entity={
                    "id": "open:challenge-2", "kind": "opportunity",
                    "name": "Desafio de qualidade", "description": "Piloto em planta industrial.",
                    "card": {
                        "promoter": "Corporação Y", "source": "web",
                        "document": document.model_dump(mode="json"),
                    },
                }, kind="opportunity", formal_instrument=False, validity="needs_review",
            )]

    brief = BriefProjeto(original_intention="qualidade industrial")
    project = ProjetoInovacao(workspace_id="w1", brief_id=brief.id)
    path = OpenPathways(OpenKnowledgeDouble()).propose(brief=brief, project=project)

    assert path.kind == "open_innovation"
    assert path.formal_instrument is False
    assert path.temporal_state == "unknown"
    assert "prazo" in " ".join(path.gaps).lower()
    assert "não é elegibilidade" in " ".join(path.risks).lower()
    assert path.project_id == project.id


def test_confirmed_project_can_request_open_path_without_replacing_project():
    class OpenAwarePathways:
        def propose_many(self, *, brief, project, mode="normative"):
            assert mode == "open"
            return [CaminhoInovacao(
                tipo="open_innovation", kind="open_innovation", project_id=project.id,
                entity_ref="open:challenge", formal_instrument=False,
                recommendation="Faça uma abordagem ao promotor.",
                next_step="Validar contato com o promotor.",
                gaps=["Fonte pendente de revisão."],
            )]

    state = _state()
    brief = BriefProjeto(original_intention="reduzir perdas")
    state.brief = brief
    state.project = ProjetoInovacao(workspace_id="w1", brief_id=brief.id)
    state.project_id = state.project.id
    graph = ConsultantGraph(OpenAwarePathways())

    next_state, answer, events = graph.run(state, "Pesquise desafios corporativos de inovação aberta")

    assert next_state.project is not None
    assert next_state.paths[0].kind == "open_innovation"
    assert "mercado" not in answer.lower() or "próximo" in answer.lower()
    assert "market_next_step" in events


def test_path_selection_records_reason_and_keeps_single_selection():
    state = _state()
    state.project = ProjetoInovacao(workspace_id="w1", brief_id="b1")
    first = _path(state.project.id)
    second = _path(state.project.id)
    state.paths = [first, second]
    state.path_ids = [first.id, second.id]
    graph = ConsultantGraph(FakePathways())

    selected = graph.select_path(state, second.id, "A ICT necessária já está acessível.")

    assert selected.selected_path_id == second.id
    assert second.status == "selected"
    assert second.decision is not None
    assert second.decision.reason == "A ICT necessária já está acessível."
    assert second.state_history[-1].to_status == "selected"
    assert first.status == "proposed"
    assert all(item.status != "selected" for item in selected.paths if item.id != second.id)


def test_reassessment_preserves_selection_decision_and_marks_new_revision():
    state = _state()
    state.project = ProjetoInovacao(workspace_id="w1", brief_id="b1")
    path = _path(state.project.id)
    state.paths = [path]
    state.path_ids = [path.id]
    graph = ConsultantGraph(FakePathways())
    graph.select_path(state, path.id, "É o caminho mais viável para o estágio atual.")
    selected_revision = state.revision

    reassessed = graph.reassess_path(state, path.id, "O TRL informado foi atualizado.")

    assert reassessed.revision == selected_revision + 1
    assert reassessed.selected_path_id == path.id
    assert path.status == "reassess_needed"
    assert path.decision is not None
    assert path.decision.reason == "É o caminho mais viável para o estágio atual."
    assert path.reassessment_reason == "O TRL informado foi atualizado."
    assert [item.to_status for item in path.state_history[-2:]] == ["selected", "reassess_needed"]
    assert reassessed.project.decision_history[-1]["kind"] == "path_reassessment_requested"


def test_memory_context_is_typed_and_not_catalog_fact():
    memory = MemoryContext(
        kind="semantic", scope="workspace", scope_id="w1",
        content="A empresa teve melhor resultado em chamadas com TRL alto.",
        origin="reflection_insights", confidence=0.8,
    )

    assert memory.kind == "semantic"
    assert memory.scope == "workspace"
    assert memory.origin == "reflection_insights"
    assert memory.read_allowed is True
    assert not hasattr(memory, "entity")
