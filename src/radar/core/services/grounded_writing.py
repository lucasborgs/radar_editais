"""Escrita iniciada por caminho, mantendo a WritingSession como motor."""

from __future__ import annotations

from typing import Any

from radar.api.common import load_library_items, profile_from_workspace
from radar.core.services.checklist_service import auto_review_checklist
from radar.core.services.consultant import (
    ConsultantNotFoundError,
    ConsultantRepository,
    ConsultantValidationError,
    consultant_service,
)
from radar.core.services.writing_session import WritingSession, get_session_document
from radar.domain.consultant import CaminhoInovacao, ConsultantState, WritingContext


class GroundedWriting:
    """Adapter fino entre ConsultantGraph e WritingSession.

    A classe não cria projeto, caminho ou elegibilidade. Ela somente consome
    um caminho selecionado e passa seu snapshot ao runtime de escrita.
    """

    _ARTIFACT_ALIASES = {
        "proposal": "proposta_tecnica",
        "technical_proposal": "proposta_tecnica",
        "technical proposal": "proposta_tecnica",
        "proposta": "proposta_tecnica",
        "proposta técnica": "proposta_tecnica",
        "market_one_pager": "abordagem_mercado",
        "one_pager": "abordagem_mercado",
        "abordagem de mercado": "abordagem_mercado",
    }

    def __init__(self, repository: ConsultantRepository | None = None):
        self.repository = repository or consultant_service.repository

    @classmethod
    def normalize_artifact_type(cls, value: str) -> str:
        normalized = " ".join(value.strip().lower().replace("-", " ").split())
        normalized = cls._ARTIFACT_ALIASES.get(normalized, normalized)
        if normalized not in {"proposta_tecnica", "abordagem_mercado"}:
            raise ConsultantValidationError(
                "Artefato não suportado. Use proposta_tecnica ou abordagem_mercado."
            )
        return normalized

    @staticmethod
    def build_outline(artifact_type: str) -> list[str]:
        if artifact_type == "abordagem_mercado":
            return [
                "1. Problema e contexto do desafio",
                "2. Solução proposta",
                "3. Evidências e capacidade de execução",
                "4. Próximo passo com o promotor",
            ]
        return [
            "1. Identificação e resumo executivo",
            "2. Problema, relevância e objetivo",
            "3. Solução técnica e inovação",
            "4. Metodologia e plano de trabalho",
            "5. Resultados, indicadores e impactos",
            "6. Equipe, parcerias e capacidade de execução",
            "7. Cronograma e orçamento",
        ]

    @staticmethod
    def build_context(
        state: ConsultantState,
        path: CaminhoInovacao,
        artifact_type: str,
        allowed_materials: list[str],
    ) -> WritingContext:
        if state.project is None or state.project.id != path.project_id:
            raise ConsultantValidationError("O caminho não está ligado ao projeto confirmado.")
        if path.status != "selected" or state.selected_path_id != path.id:
            raise ConsultantValidationError(
                "A escrita só pode começar a partir de um caminho selecionado."
            )
        if artifact_type == "proposta_tecnica" and not path.formal_instrument:
            raise ConsultantValidationError(
                "Este caminho não possui instrumento normativo para uma proposta técnica."
            )

        retrieval_scope = []
        if artifact_type == "proposta_tecnica" and path.formal_instrument:
            target = path.opportunity_ref or path.entity_ref
            if target:
                retrieval_scope = [target]

        return WritingContext(
            project_id=state.project.id,
            path_id=path.id,
            path_revision=path.context_revision,
            profile_version=state.project.profile_version,
            artifact_type=artifact_type,
            formal_instrument=path.formal_instrument,
            source_refs=path.evidence,
            retrieval_scope=retrieval_scope,
            allowed_materials=allowed_materials,
            requirements=list(path.requirements),
            facts=list(path.facts),
            gaps=list(path.gaps),
            claims=list(path.claims),
        )

    @staticmethod
    def _plan(context: WritingContext, outline: list[str]) -> dict[str, Any]:
        requirements = [
            {"id": f"req_{index}", "title": requirement, "requirement": requirement}
            for index, requirement in enumerate(context.requirements, start=1)
        ]
        return {
            "artifact_type": context.artifact_type,
            "sections": [{"id": f"section_{i}", "title": title} for i, title in enumerate(outline, 1)],
            "requirements": requirements,
            "gaps": context.gaps,
            "claims": context.claims,
        }

    @staticmethod
    def _context_for_prompt(context: WritingContext) -> dict[str, Any]:
        return context.model_dump(mode="json")

    def open(
        self,
        db,
        workspace_id: str,
        *,
        conversation_id: str,
        path_id: str,
        artifact_type: str,
        allowed_material_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        artifact_type = self.normalize_artifact_type(artifact_type)
        state = self.repository.load(db, conversation_id, workspace_id)
        if state is None:
            raise ConsultantNotFoundError("Conversa do consultor não encontrada.")
        path = next((item for item in state.paths if item.id == path_id), None)
        if path is None:
            raise ConsultantValidationError("Caminho não encontrado nesta conversa.")

        requested_materials = [str(item) for item in (allowed_material_ids or []) if str(item).strip()]
        materials = load_library_items(db, workspace_id, requested_materials)
        authorized_ids = [str(item["id"]) for item in materials if item.get("id")]
        context = self.build_context(state, path, artifact_type, authorized_ids)
        profile = profile_from_workspace(db, workspace_id)
        target = path.opportunity_ref or path.entity_ref
        if not target:
            raise ConsultantValidationError("O caminho selecionado não possui fonte para escrita.")

        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile,
            edital_id=target,
            library_items=materials,
            mode="proposal",
            plan=self._plan(context, self.build_outline(artifact_type)),
            writing_context=self._context_for_prompt(context),
            allow_incomplete_profile=True,
        )
        return {
            "writing_session_id": session.session_id,
            "context": context.model_dump(mode="json"),
            "project_id": context.project_id,
            "path_id": context.path_id,
            "outline": session.get_info()["section_titles"],
            "requirements": context.requirements,
            "gaps": context.gaps,
            "artifact_type": context.artifact_type,
        }

    def _load_context(self, db, session_id: str, workspace_id: str) -> WritingContext:
        result = (
            db.table("writing_sessions")
            .select("writing_context, workspace_id")
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        row = result.data if result else None
        if not row or row.get("workspace_id") != workspace_id:
            raise ConsultantNotFoundError("Sessão de escrita fundamentada não encontrada.")
        context = row.get("writing_context")
        if not isinstance(context, dict) or not context.get("path_id"):
            raise ConsultantValidationError("Esta sessão não foi aberta por um caminho selecionado.")
        return WritingContext.model_validate(context)

    def _session(self, db, workspace_id: str, session_id: str) -> tuple[WritingSession, WritingContext]:
        context = self._load_context(db, session_id, workspace_id)
        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile_from_workspace(db, workspace_id),
            session_id=session_id,
            library_items=load_library_items(db, workspace_id, context.allowed_materials),
            mode="proposal",
            allow_incomplete_profile=True,
        )
        return session, context

    def turn(
        self,
        db,
        workspace_id: str,
        session_id: str,
        instruction: str,
        section_hint: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key:
            try:
                cached = (
                    db.table("writing_turn_idempotency")
                    .select("response_json")
                    .eq("session_id", session_id)
                    .eq("idempotency_key", idempotency_key)
                    .maybe_single()
                    .execute()
                )
                if cached and cached.data and cached.data.get("response_json"):
                    return cached.data["response_json"]
            except Exception:
                pass
        session, context = self._session(db, workspace_id, session_id)
        result = session.turn(instruction, section_hint)
        document = result.get("document") or session.get_document()
        citations = [
            citation
            for section in document.get("sections", [])
            for citation in (section.get("citations") or [])
        ]
        pending = result.get("pending_user_input")
        pending_questions = list(context.gaps)
        if isinstance(pending, dict) and pending.get("prompt"):
            pending_questions.append(str(pending["prompt"]))
        response = {
            **result,
            "writing_session_id": session_id,
            "context": context.model_dump(mode="json"),
            "evidence_refs": context.source_refs,
            "retrieved_citations": citations,
            "pending_questions": pending_questions,
            "gaps": pending_questions,
        }
        if idempotency_key:
            try:
                db.table("writing_turn_idempotency").insert({
                    "idempotency_key": idempotency_key,
                    "session_id": session_id,
                    "response_json": response,
                }).execute()
            except Exception:
                # A duplicate retry is harmless; the session itself remains the
                # source of truth and the next request can still reload it.
                pass
        return response

    async def review(self, db, workspace_id: str, session_id: str) -> dict[str, Any]:
        context = self._load_context(db, session_id, workspace_id)
        document = get_session_document(db, session_id, workspace_id)
        if document is None:
            raise ConsultantNotFoundError("Sessão de escrita fundamentada não encontrada.")
        proposal = "\n\n---\n\n".join(
            f"## {section['title']}\n\n{section['content']}"
            for section in document["sections"]
        )
        requirements = [
            {"id": f"req_{index}", "requirement": requirement, "source": "selected_path"}
            for index, requirement in enumerate(context.requirements, start=1)
        ]
        review = await auto_review_checklist(
            proposal=proposal,
            edital_requirements=requirements,
            outline=[section["title"] for section in document["sections"]],
            workspace_id=workspace_id,
            session_id=session_id,
        )
        gaps = list(context.gaps)
        gaps.extend(
            issue["requirement"]
            for issue in review.get("compliance", {}).get("issues", [])
            if issue.get("status") in {"missing", "partial"} and issue.get("requirement")
        )
        gaps.extend(review.get("completeness", {}).get("missing_sections", []) or [])
        return {
            "writing_session_id": session_id,
            "context": context.model_dump(mode="json"),
            "review": review,
            "gaps": list(dict.fromkeys(gaps)),
            "evidence_refs": context.source_refs,
        }


grounded_writing = GroundedWriting()
