"""Walking skeleton do ConsultantGraph.

Conhecimento e caminhos ficam atrás de seams pequenas; o estado persistido é a
autoridade da conversa autenticada.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from radar.core.kg import entity_catalog
from radar.core.services import domain_paths, eligibility
from radar.domain.consultant import (
    BriefProjeto,
    CaminhoInovacao,
    CanalInovacao,
    ConsultantMessage,
    ConsultantState,
    DocumentClaim,
    DocumentIntelligenceResult,
    EvidenceReference,
    KnowledgeSignal,
    MemoryContext,
    PathDecision,
    PathStateTransition,
    ProjetoInovacao,
    RuleEvaluation,
    SourceDocumentRef,
)
from radar.domain.profile_schema import PROFILE_FIELD_NAMES

logger = logging.getLogger(__name__)


def _validity_value(value: object) -> str:
    normalized = str(value or "").lower()
    return normalized if normalized in {"unknown", "needs_review", "active"} else "needs_review"


class KnowledgeEntity(TypedDict, total=False):
    id: str
    kind: str
    name: str
    description: str
    card: dict


class Knowledge(Protocol):
    def search(self, query: str, *, profile: dict, limit: int = 3) -> list[KnowledgeEntity]: ...

    def search_signals(self, query: str, *, profile: dict, limit: int = 3) -> list[KnowledgeSignal]: ...

    def get(self, entity_ref: str) -> KnowledgeEntity | None: ...

    def paths(self, entity_ref: str) -> list[KnowledgeSignal]: ...


class Pathways(Protocol):
    def propose(
        self, *, brief: BriefProjeto, project: ProjetoInovacao,
        mode: str = "normative",
    ) -> CaminhoInovacao: ...

    def select(self, path: CaminhoInovacao, decision: PathDecision) -> CaminhoInovacao: ...

    def reassess(self, path: CaminhoInovacao, new_context: dict[str, Any]) -> CaminhoInovacao: ...


class DocumentIntelligence(Protocol):
    def ingest(self, document: dict[str, Any]) -> DocumentIntelligenceResult: ...


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class DiscoveryDocumentIntelligence:
    """Normaliza discovery/research sem promover nada ao catálogo."""

    def ingest(self, document: dict[str, Any]) -> DocumentIntelligenceResult:
        raw = document.get("raw") if "raw" in document else document
        raw = raw if isinstance(raw, dict) else {}
        package = raw.get("evidence_package") if isinstance(raw, dict) else None
        package = package if isinstance(package, dict) else {}
        identity = package.get("identity") if isinstance(package, dict) else {}
        identity = identity if isinstance(identity, dict) else {}
        sources = raw.get("sources") if isinstance(raw, dict) else None
        is_research = bool(sources) and "question" in document
        url = str(
            document.get("url")
            or identity.get("canonical_url")
            or package.get("canonical_url")
            or (sources[0].get("url") if sources and isinstance(sources[0], dict) else "")
        )
        title = str(document.get("title") or document.get("question") or "Achado web")
        collected_at = _parse_datetime(identity.get("collected_at") or document.get("created_at"))
        content_hash = None
        page = package.get("page") if isinstance(package, dict) else None
        if isinstance(page, dict):
            content_hash = page.get("content_hash")
        ref = str(document.get("id") or document.get("url_hash") or url or title)
        evidence: list[EvidenceReference] = []
        evidence.append(EvidenceReference(
            kind="web_source", ref=ref, label=title,
            locator="pagina_principal" if url else None,
            locator_quality="document_only" if url else "unresolved",
            document=title, source_url=url or None, source_hash=content_hash,
            version=collected_at.isoformat() if collected_at else None,
            source_role="primary" if url else "unknown",
        ))
        citation_sources = (
            package.get("deep_research", {}).get("citations", [])
            if isinstance(package.get("deep_research"), dict) else sources or []
        )
        for citation in citation_sources:
            if not isinstance(citation, dict) or not citation.get("url"):
                continue
            citation_url = str(citation["url"])
            if citation_url == url:
                continue
            evidence.append(EvidenceReference(
                kind="web_corroboration", ref=ref,
                label=str(citation.get("title") or citation_url),
                locator_quality="document_only", source_url=citation_url,
                quote=citation.get("snippet"),
                version=collected_at.isoformat() if collected_at else None,
                source_role="corroborating",
            ))

        claims: list[DocumentClaim] = []
        fields = package.get("fields") if isinstance(package, dict) else None
        if isinstance(fields, dict):
            for predicate, field in fields.items():
                if not isinstance(field, dict) or not str(field.get("value") or "").strip():
                    continue
                claims.append(DocumentClaim(
                    subject=title, predicate=str(predicate), value=str(field["value"]),
                    confidence=0.5, evidence=evidence[:1],
                ))
        answer = str(document.get("answer") or "").strip()
        if answer:
            claims.append(DocumentClaim(
                subject=title, predicate="research_summary", value=answer[:4_000],
                confidence=0.5 if len(evidence) > 1 else 0.3, evidence=evidence,
            ))
        status = str(document.get("status") or "pending").lower()
        review_state = "confirmed" if status == "promoted" or document.get("reviewed_at") else (
            "needs_review" if status != "rejected" else "draft"
        )
        confidence = 0.75 if len(evidence) > 1 else 0.45
        deep_research = package.get("deep_research") if isinstance(package, dict) else None
        if isinstance(deep_research, dict):
            confidence = {"high": 0.8, "medium": 0.6, "low": 0.35}.get(
                str(deep_research.get("confidence") or ""), confidence,
            )
        return DocumentIntelligenceResult(
            document=SourceDocumentRef(
                ref=ref, label=title, source_url=url or None,
                role="research_finding" if is_research else "opportunity_page",
                collected_at=collected_at, content_hash=content_hash,
            ),
            claims=claims, evidence=evidence,
            freshness={
                "collected_at": collected_at.isoformat() if collected_at else None,
                "age_state": "known" if collected_at else "unknown",
            }, confidence=confidence, review_state=review_state,
            source_kind="research" if is_research else (
                "curated" if status == "promoted" else "discovery"
            ),
        )


class RelationalKnowledge:
    """Adapter fino do catálogo gold, sem vazar SQL para a jornada."""

    def search(self, query: str, *, profile: dict, limit: int = 3) -> list[KnowledgeEntity]:
        results: list[KnowledgeEntity] = []
        try:
            semantic = entity_catalog.search_entities(query, k=max(limit * 2, 6))
        except Exception as exc:
            logger.warning("consultant: busca semântica indisponível: %s", exc)
            semantic = []

        for item in semantic:
            entity_ref = item.get("id") or ""
            if not entity_ref or item.get("kind") not in {"edital", "programa"}:
                continue
            card = self.get(entity_ref)
            if card:
                results.append(card)
        if results:
            return results[:limit]

        try:
            cards = entity_catalog.list_editais(limit=limit)
        except Exception as exc:
            logger.warning("consultant: catálogo gold indisponível: %s", exc)
            cards = []
        results = [self._from_card(card) for card in cards if card.get("id")]
        if len(results) < limit:
            try:
                programs = entity_catalog.list_entity_catalog("programas", limit=limit)
                for program in programs:
                    item = self.get(program.get("id") or "")
                    if item:
                        results.append(item)
            except Exception as exc:
                logger.warning("consultant: programas gold indisponíveis: %s", exc)
        return results[:limit]

    def get(self, entity_ref: str) -> KnowledgeEntity | None:
        if not entity_ref:
            return None
        try:
            card = entity_catalog.get_opportunity(entity_ref)
        except Exception as exc:
            logger.warning("consultant: falha ao ler entidade %s: %s", entity_ref, exc)
            return None
        return self._from_card(card) if card else None

    def search_signals(self, query: str, *, profile: dict, limit: int = 3) -> list[KnowledgeSignal]:
        """Contrato normativo público; ``search`` permanece adapter de T01."""
        signals: list[KnowledgeSignal] = []
        for entity in self.search(query, profile=profile, limit=limit):
            card = entity.get("card") or {}
            signals.append(KnowledgeSignal(
                entity=dict(entity),
                role="opportunity",
                knowledge_level="fact",
                validity=_validity_value(card.get("validity_state")),
                evidence_refs=GoldPathways._evidence(
                    str(entity.get("id") or ""), str(entity.get("name") or ""), card,
                ),
                reason="Recuperada do catálogo gold por intenção e afinidade semântica.",
            ))
        return signals

    def get_signal(self, entity_ref: str) -> KnowledgeSignal | None:
        entity = self.get(entity_ref)
        if entity is None:
            return None
        card = entity.get("card") or {}
        return KnowledgeSignal(
            entity=dict(entity), role="opportunity", knowledge_level="fact",
            validity=_validity_value(card.get("validity_state")),
            evidence_refs=self._evidence_for(entity),
            reason="Entidade lida diretamente do catálogo gold.",
        )

    @staticmethod
    def _evidence_for(entity: KnowledgeEntity) -> list[EvidenceReference]:
        return GoldPathways._evidence(
            str(entity.get("id") or ""), str(entity.get("name") or ""), entity.get("card") or {},
        )

    @staticmethod
    def _from_card(card: dict) -> KnowledgeEntity:
        return {
            "id": card.get("id", ""),
            "kind": card.get("kind") or "edital",
            "name": card.get("title") or "",
            "description": card.get("objective") or "",
            "card": card,
        }

    def paths(self, entity_ref: str) -> list[KnowledgeSignal]:
        """Retorna ICTs declaradas e candidatas, com o motivo separado."""
        opportunity = self.get(entity_ref)
        if not opportunity:
            return []
        card = opportunity.get("card") or {}
        themes = card.get("themes") or []
        declared_names = {str(name).strip().lower() for name in card.get("icts") or []}
        try:
            actors = entity_catalog.list_entity_catalog(
                "ict", tema=" ".join(str(theme) for theme in themes[:3]), limit=3,
            )
        except Exception as exc:
            logger.warning("consultant: catálogo de ICT indisponível: %s", exc)
            actors = []
        signals: list[KnowledgeSignal] = []
        for actor in actors:
            name = str(actor.get("name") or "")
            declared = name.strip().lower() in declared_names
            capabilities = actor.get("capacidades") or {}
            entity = {
                "id": actor.get("id") or "",
                "kind": "ict",
                "name": name,
                "description": actor.get("description") or "",
                "capacidades": capabilities,
                "relationship": "declared_by_opportunity" if declared else "inferred_by_theme",
            }
            signals.append(KnowledgeSignal(
                entity=entity,
                role="required_partner" if declared else "possible_partner",
                knowledge_level="fact" if declared else "inference",
                validity="needs_review",
                reason=(
                    "ICT relacionada pela fonte da oportunidade."
                    if declared else "ICT candidata por tema compartilhado; disponibilidade não verificada."
                ),
            ))
        return signals


class OpenKnowledge:
    """Adapter de achados web para inovação aberta.

    A tabela de staging é somente leitura nesta jornada. Um achado ``pending``
    pode ser mostrado como hipótese com revisão pendente, mas nunca é projetado
    em ``entities`` nem tratado como edital.
    """

    _OPEN_TERMS = (
        "desafio", "open innovation", "inovação aberta", "piloto",
        "prova de conceito", "poc", "empresa-âncora", "empresa ancora",
        "corporativo", "corporação", "corporacao",
    )
    _EXCLUDED_TERMS = (
        "crédito", "credito", "investidor", "investimento financeiro",
        "bolsa acadêmica", "bolsa academica", "licitação", "licitacao",
    )

    def __init__(self, db, document_intelligence: DocumentIntelligence | None = None):
        self.db = db
        self.documents = document_intelligence or DiscoveryDocumentIntelligence()

    @classmethod
    def _is_open(cls, row: dict) -> bool:
        if str(row.get("status") or "").lower() == "rejected":
            return False
        kind = str(row.get("opportunity_type") or "").lower()
        family = str(row.get("query_family") or "").lower()
        text = " ".join(str(row.get(field) or "") for field in (
            "title", "agency", "fonte", "descricao", "tema", "publico_alvo",
        )).lower()
        raw = row.get("raw") or {}
        if isinstance(raw, dict):
            text += " " + str(raw.get("answer") or "")
            package = raw.get("evidence_package") or {}
            if isinstance(package, dict):
                deep = package.get("deep_research") or {}
                if isinstance(deep, dict):
                    text += " " + str(deep.get("answer") or "")
        if any(term in text for term in cls._EXCLUDED_TERMS):
            return False
        return (
            kind in {"desafio", "challenge", "open_innovation", "canal"}
            or family == "corporate_open_innovation"
            or any(term in text for term in cls._OPEN_TERMS)
        )

    @staticmethod
    def _entity(row: dict, result: DocumentIntelligenceResult) -> KnowledgeEntity:
        kind = str(row.get("opportunity_type") or "").lower()
        entity_kind = "channel" if kind in {"programa", "canal"} else "opportunity"
        card = {
            "id": str(row.get("id") or result.document.ref),
            "kind": entity_kind,
            "title": row.get("title") or result.document.label,
            "description": row.get("descricao") or "",
            "promoter": row.get("agency") or row.get("fonte") or "Promotor não informado",
            "official_url": row.get("url") or result.document.source_url,
            "deadline": row.get("prazo_envio") or "",
            "participation_format": row.get("publico_alvo") or "",
            "opportunity_type": kind or "desafio",
            "status": row.get("status") or "pending",
            "review_state": result.review_state,
            "formal_instrument": False,
            "document": result.model_dump(mode="json"),
            "discovery_channel": row.get("discovery_channel") or "web_curated",
            "source": row.get("fonte") or row.get("agency") or "web",
        }
        channel = CanalInovacao(
            id=card["id"], kind=entity_kind, page=result.document,
            promoter=str(card["promoter"]), challenge=str(card["description"]),
            corroborating_sources=result.evidence[1:],
            collected_at=result.document.collected_at, freshness=result.freshness,
            review_state=result.review_state, confidence=result.confidence,
        )
        card["open_channel"] = channel.model_dump(mode="json")
        return {
            "id": card["id"], "kind": entity_kind,
            "name": card["title"], "description": card["description"],
            "card": card,
        }

    def _rows(self, limit: int) -> list[dict]:
        rows: list[dict] = []
        try:
            result = self.db.table("discovered_opportunities").select(
                "id,url,title,agency,fonte,descricao,prazo_envio,publico_alvo,tema,"
                "opportunity_type,status,raw,created_at,reviewed_at,discovery_channel,query_family"
            ).neq("status", "rejected").order("created_at", desc=True).limit(limit * 4).execute()
            rows.extend(result.data or [])
        except Exception as exc:
            logger.warning("consultant: staging aberta indisponível: %s", exc)
        # Findings são workspace-scoped e chegam sempre como revisão pendente.
        # Eles entram no mesmo adapter apenas para a pesquisa solicitada pelo
        # usuário; não são fatos nem publicação no catálogo.
        try:
            result = self.db.table("research_findings").select(
                "id,question,answer,sources,created_at,reviewed_at,promoted_to_library_id"
            ).is_("reviewed_at", "null").order("created_at", desc=True).limit(limit * 2).execute()
            for finding in result.data or []:
                rows.append({
                    "id": finding.get("id"), "title": finding.get("question"),
                    "question": finding.get("question"),
                    "descricao": finding.get("answer"), "fonte": "Deep Research",
                    "agency": "", "opportunity_type": "desafio", "status": "pending",
                    "raw": {"sources": finding.get("sources") or []},
                    "sources": finding.get("sources") or [],
                    "answer": finding.get("answer") or "",
                    "created_at": finding.get("created_at"),
                    "reviewed_at": finding.get("reviewed_at"),
                })
        except Exception as exc:
            logger.info("consultant: research_findings indisponível: %s", exc)
        return rows

    def search(self, query: str, *, profile: dict, limit: int = 3) -> list[KnowledgeEntity]:
        query_terms = set(_normal(query).split())
        results: list[KnowledgeEntity] = []
        for row in self._rows(limit):
            if not self._is_open(row):
                continue
            result = self.documents.ingest(row)
            text = " ".join(str(row.get(field) or "") for field in (
                "title", "descricao", "tema", "agency", "fonte",
            )).lower()
            overlap = len(query_terms & set(text.split()))
            entity = self._entity(row, result)
            entity["_overlap"] = overlap
            results.append(entity)
        results.sort(key=lambda item: int(item.get("_overlap") or 0), reverse=True)
        return results[:limit]

    def search_signals(self, query: str, *, profile: dict, limit: int = 3) -> list[KnowledgeSignal]:
        signals: list[KnowledgeSignal] = []
        for entity in self.search(query, profile=profile, limit=limit):
            card = entity.get("card") or {}
            document = DocumentIntelligenceResult.model_validate(card.get("document") or {})
            signals.append(KnowledgeSignal(
                entity=entity, kind=str(entity.get("kind") or "opportunity"),
                role="open_innovation", formal_instrument=False,
                knowledge_level="fact" if document.review_state == "confirmed" else "unknown",
                validity="active" if document.review_state == "confirmed" else "needs_review",
                evidence_refs=document.evidence,
                reason=(
                    "Achado web curado; a página é tratada como canal aberto, não como edital."
                    if document.review_state == "confirmed" else
                    "Achado web em staging; requer revisão humana antes de virar fato operacional."
                ),
            ))
        return signals

    def get(self, entity_ref: str) -> KnowledgeEntity | None:
        for row in self._rows(20):
            if str(row.get("id") or "") == entity_ref and self._is_open(row):
                return self._entity(row, self.documents.ingest(row))
        return None

    def paths(self, entity_ref: str) -> list[KnowledgeSignal]:
        return []


class OpenPathways:
    """Propostas de mercado abertas, sem elegibilidade ou edital implícito."""

    def __init__(self, knowledge: OpenKnowledge):
        self.knowledge = knowledge

    def propose(self, *, brief: BriefProjeto, project: ProjetoInovacao, mode: str = "open") -> CaminhoInovacao:
        paths = self.propose_many(brief=brief, project=project, mode=mode)
        if not paths:
            raise CatalogUnavailableError("Nenhum desafio corporativo aberto foi encontrado na staging.")
        return paths[0]

    def propose_many(
        self, *, brief: BriefProjeto, project: ProjetoInovacao, mode: str = "open",
    ) -> list[CaminhoInovacao]:
        if mode != "open":
            raise ValueError(f"Modo de caminho não suportado: {mode}")
        signals = self.knowledge.search_signals(
            f"{brief.problem_hypothesis} {brief.solution_hypothesis} {brief.original_intention}",
            profile=project.profile_snapshot, limit=5,
        )
        paths: list[CaminhoInovacao] = []
        for signal in signals:
            path = self._from_signal(signal, project=project)
            if path is not None:
                paths.append(path)
        return paths

    @staticmethod
    def _from_signal(signal: KnowledgeSignal, *, project: ProjetoInovacao) -> CaminhoInovacao:
        entity = signal.entity
        card = entity.get("card") or {}
        document = DocumentIntelligenceResult.model_validate(card.get("document") or {})
        title = str(entity.get("name") or "Desafio de inovação aberta")
        promoter = str(card.get("promoter") or "Promotor não informado")
        deadline = str(card.get("deadline") or "").strip()
        description = str(entity.get("description") or "").strip()
        facts = [
            f"Desafio/canal encontrado: {title}.",
            f"Promotor indicado pela fonte: {promoter}.",
            "A fonte não representa um edital formal nem uma decisão de elegibilidade.",
        ]
        if description:
            facts.append(f"Problema descrito na página: {description}")
        gaps: list[str] = []
        if not deadline:
            facts.append("Prazo não informado pela fonte consultada.")
            gaps.append("Prazo não informado; não trate a ausência como fluxo contínuo.")
        if not card.get("participation_format"):
            facts.append("Formulário ou formato de participação não informado.")
            gaps.append("Confirme o canal de contato, formulário ou formato de participação com o promotor.")
        if document.review_state != "confirmed":
            gaps.append("Fonte pendente de revisão humana; este achado ainda é um draft.")
        if len(document.evidence) < 2:
            gaps.append("Busque uma fonte corroborante ou confirme a página primária antes de abordar o promotor.")
        next_step = (
            "Validar o contato e preparar uma abordagem de mercado de uma página para o promotor, "
            "sem assumir elegibilidade ou prazo."
        )
        return CaminhoInovacao(
            tipo="open_innovation", kind="open_innovation", project_id=project.id,
            entity_ref=str(entity.get("id") or document.document.ref),
            opportunity_ref=str(entity.get("id") or document.document.ref),
            actors=[{"name": promoter, "role": "promoter", "source": "web"}],
            facts=facts,
            inferences=[
                "A aderência entre o problema do projeto e o desafio é uma hipótese de mercado, não uma afirmação do promotor."
            ],
            requirements=[
                "Solução/capacidade compatível com o problema descrito.",
                "Contato ou processo de participação a confirmar.",
            ],
            gaps=gaps,
            risks=[
                "Não é elegibilidade: a participação, contratação ou piloto não está garantida.",
                "A ausência de prazo não significa oportunidade contínua.",
            ],
            recommendation=(
                f"Avalie uma abordagem ao promotor {promoter} conectando o problema do projeto "
                f"ao desafio {title}."
            ),
            next_step=next_step, evidence=document.evidence,
            temporal_state="active" if deadline and document.review_state == "confirmed" else "unknown",
            last_evaluated_at=datetime.now(timezone.utc), confidence=document.confidence,
            needs_review=True, source=str(card.get("source") or "web"),
            formal_instrument=False, freshness=document.freshness,
        )


class CatalogUnavailableError(RuntimeError):
    pass


class GoldPathways:
    """Adapter normativo sobre o gold; regras e validade são determinísticas."""

    def __init__(
        self, knowledge: Knowledge, *, workspace_id: str | None = None, db=None,
        open_knowledge: OpenKnowledge | None = None,
    ):
        self.knowledge = knowledge
        self.workspace_id = workspace_id
        self.db = db
        self.open_knowledge = open_knowledge

    def propose(
        self, *, brief: BriefProjeto, project: ProjetoInovacao,
        mode: str = "normative",
    ) -> CaminhoInovacao:
        if mode == "open":
            if self.open_knowledge is None:
                raise CatalogUnavailableError("Pesquisa aberta indisponível neste ambiente.")
            return OpenPathways(self.open_knowledge).propose(brief=brief, project=project, mode=mode)
        paths = self.propose_many(brief=brief, project=project, mode=mode)
        if not paths:
            raise CatalogUnavailableError("Nenhum caminho normativo vigente foi encontrado no catálogo gold.")
        return paths[0]

    def propose_many(
        self, *, brief: BriefProjeto, project: ProjetoInovacao,
        mode: str = "normative",
    ) -> list[CaminhoInovacao]:
        if mode == "open":
            if self.open_knowledge is None:
                raise CatalogUnavailableError("Pesquisa aberta indisponível neste ambiente.")
            return OpenPathways(self.open_knowledge).propose_many(
                brief=brief, project=project, mode=mode,
            )
        if mode != "normative":
            raise ValueError(f"Modo de caminho não suportado: {mode}")
        profile = dict(project.profile_snapshot)
        profile.setdefault("one_liner", brief.solution_hypothesis)
        search_signals = getattr(self.knowledge, "search_signals", None)
        candidates = (
            [signal.entity for signal in search_signals(brief.original_intention, profile=profile, limit=8)]
            if search_signals is not None
            else self.knowledge.search(brief.original_intention, profile=profile, limit=8)
        )
        paths: list[CaminhoInovacao] = []
        for candidate in candidates:
            try:
                path = self._from_entity(candidate, profile=profile, project=project)
            except CatalogUnavailableError:
                continue
            if path is not None:
                paths.append(path)
            if len(paths) >= 3:
                break
        if not paths:
            raise CatalogUnavailableError("Nenhum caminho normativo vigente foi encontrado no catálogo gold.")
        return paths

    @staticmethod
    def select(path: CaminhoInovacao, decision: PathDecision) -> CaminhoInovacao:
        """Aplica a intenção de seleção sem escrever nada no Knowledge."""
        selected = path.model_copy(deep=True)
        selected.status = "selected"
        selected.decision = decision
        selected.updated_at = datetime.now(timezone.utc)
        return selected

    @staticmethod
    def reassess(path: CaminhoInovacao, new_context: dict[str, Any]) -> CaminhoInovacao:
        """Marca uma nova avaliação sem sobrescrever fatos ou evidências."""
        reassessed = path.model_copy(deep=True)
        reassessed.status = "reassess_needed"
        reassessed.reassessment_reason = str(new_context.get("reason") or "Contexto alterado.")
        reassessed.needs_review = True
        reassessed.updated_at = datetime.now(timezone.utc)
        return reassessed

    @staticmethod
    def _raw_entity(entity: KnowledgeEntity) -> tuple[dict, dict]:
        if isinstance(entity, KnowledgeSignal):
            entity = entity.entity
        card = entity.get("card") or {}
        mechanism = card.get("mecanismo") or card.get("mechanism") or ""
        if isinstance(mechanism, list):
            mechanism = " ".join(str(item) for item in mechanism)
        raw = {
            "kind": entity.get("kind") or card.get("kind") or "edital",
            "native_id": entity.get("id") or card.get("id") or "",
            "name": entity.get("name") or card.get("title") or "",
            "description": entity.get("description") or card.get("objective") or "",
            "status": card.get("status") or "desconhecido",
            "setores": card.get("themes") or [],
            "requisitos_texto": card.get("key_requirements") or [],
            "constraints": card.get("constraints") or [],
            "metadata": {
                "url": card.get("official_url") or "",
                "tipo": mechanism,
                "formato": card.get("aperture") or "",
            },
        }
        return raw, card

    @staticmethod
    def _evidence(entity_ref: str, name: str, card: dict) -> list[EvidenceReference]:
        evidence: list[EvidenceReference] = []
        provenance = card.get("provenance") or {}
        for field, fingerprint in provenance.items():
            for citation in (fingerprint or {}).get("citations") or []:
                document = citation.get("document")
                locator = citation.get("page") or field
                evidence.append(EvidenceReference(
                    kind="source_citation", ref=entity_ref, label=name,
                    locator=str(locator) if locator else None,
                    locator_quality="exact" if citation.get("page") else "document_only",
                    quote=citation.get("quote"), document=document,
                    source_url=citation.get("source_url") or card.get("official_url"),
                    source_hash=card.get("source_hash") or card.get("silver_source_hash"),
                    version=citation.get("collected_at") or card.get("version"),
                ))
        if not evidence:
            evidence.append(EvidenceReference(
                ref=entity_ref, label=name, locator="catalog.gold",
                source_url=card.get("official_url"),
                source_hash=card.get("source_hash") or card.get("silver_source_hash"),
                version=card.get("version") or card.get("collected_at") or card.get("last_verified_at"),
            ))
        return evidence

    def _from_entity(
        self, entity: KnowledgeEntity, *, profile: dict, project: ProjetoInovacao,
    ) -> CaminhoInovacao | None:
        raw, card = self._raw_entity(entity)
        tipo = domain_paths.classify_tipo(raw)
        # Esta vertical aceita apenas apoio público não reembolsável. O tipo
        # genérico de edital ainda é mantido como financiamento para preservar
        # o vocabulário legado; crédito, bolsa e caminhos abertos nunca entram.
        if tipo not in {domain_paths.PATH_TIPO_SUBVENCAO, domain_paths.PATH_TIPO_FINANCIAMENTO}:
            return None
        temporal_state = str(card.get("validity_state") or "needs_review")
        status = str(card.get("status") or "").strip().lower()
        if temporal_state == "closed" or status in {"encerrada", "fechada", "closed", "finished"}:
            return None
        evidence = self._evidence(raw["native_id"], raw["name"], card)
        detailed = eligibility.evaluate_opportunity_detailed(raw["constraints"], profile)
        if detailed["status"] == eligibility.INELEGIVEL:
            return None
        shared = set(raw["setores"]) & set(profile.get("setores") or [])
        path_seed = domain_paths.build_path(
            raw, profile=profile, eleg=detailed, url=card.get("official_url"),
            shared_themes=shared,
        )
        if path_seed is None:
            return None
        explanation = domain_paths.build_explanation(
            tipo, e=raw, eleg=detailed, profile=profile,
            has_project=True, shared_themes=shared,
        ) or {}
        gaps = list(explanation.get("pendentes") or []) + list(explanation.get("lacunas") or [])
        if temporal_state == "needs_review":
            gaps.append("A validade ou o prazo da oportunidade precisa de revisão antes de uma decisão.")
        if not any(item.locator_quality in {"exact", "document_only"} for item in evidence):
            gaps.append("A fonte está catalogada, mas ainda não há localização precisa de trecho para a regra crítica.")
        rule_evaluations = [RuleEvaluation(
            rule=item["rule"], status=item["status"], reason=item["reason"], evidence=evidence,
        ) for item in detailed.get("evaluations") or []]
        actors = self.knowledge.paths(raw["native_id"]) if hasattr(self.knowledge, "paths") else []
        actor_payload = [signal.entity | {
            "role": signal.role, "knowledge_level": signal.knowledge_level,
            "reason": signal.reason,
        } for signal in actors]
        if actor_payload:
            gaps.append("Confirme competência, equipamento, acesso e disponibilidade da ICT antes de contar com a parceria.")
        facts = list(explanation.get("confirmados") or [])
        facts.append(f"Oportunidade: {raw['name']}.")
        if card.get("deadline"):
            facts.append(f"Prazo publicado: {card['deadline']}.")
        if card.get("value"):
            facts.append(f"Valor informado pela fonte: {card['value']}.")
        risks = ["Aprovação depende da análise do edital e não é garantida."]
        if temporal_state == "needs_review":
            risks.append("Não trate a oportunidade como ativa até resolver a revisão temporal.")
        return CaminhoInovacao(
            tipo=tipo, project_id=project.id, entity_ref=raw["native_id"],
            opportunity_ref=raw["native_id"], actors=actor_payload, facts=facts,
            inferences=list(explanation.get("inferidos") or []),
            requirements=list(raw["requisitos_texto"] or []), gaps=gaps, risks=risks,
            recommendation=f"Avalie a subvenção com base nas regras e evidências de {raw['name']}.",
            next_step=path_seed["proximo_passo"], evidence=evidence,
            rule_evaluations=rule_evaluations, temporal_state=temporal_state,
            last_evaluated_at=datetime.now(timezone.utc), confidence=0.5,
            needs_review=bool(gaps) or temporal_state == "needs_review",
            source=card.get("source"),
        )


class ConsultantNotFoundError(LookupError):
    pass


class ConsultantConflictError(RuntimeError):
    pass


class ConsultantValidationError(ValueError):
    pass


_BRIEF_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("affected_users", "Quem é afetado por esse problema? Essa resposta pode mudar os caminhos disponíveis."),
    ("solution_hypothesis", "Qual solução ou abordagem você quer testar para esse problema?"),
    ("stage_maturity", "Em que estágio ou nível de maturidade está essa iniciativa hoje?"),
    ("location_constraints", "Há uma localização, UF ou outra restrição que eu deva considerar?"),
)


def _has_value(value) -> bool:
    if isinstance(value, list):
        return bool(value)
    return bool(str(value or "").strip())


def _brief_gaps(brief: BriefProjeto, profile: dict) -> list[str]:
    gaps: list[str] = []
    for field, question in _BRIEF_QUESTIONS:
        value = getattr(brief, field)
        if field == "stage_maturity" and not _has_value(value) and profile.get("trl") is not None:
            continue
        if field == "location_constraints" and not _has_value(value) and _has_value(profile.get("uf")):
            continue
        if not _has_value(value):
            gaps.append(question)
    return gaps


def _brief_source(brief: BriefProjeto, field: str, source: str) -> None:
    refs = brief.source_refs.setdefault(field, [])
    if source not in refs:
        refs.append(source)


def _parse_labeled_fields(message: str) -> dict[str, str]:
    labels = {
        "problema": "problem_hypothesis",
        "usuários": "affected_users",
        "usuarios": "affected_users",
        "solução": "solution_hypothesis",
        "solucao": "solution_hypothesis",
        "tecnologias": "technologies_capabilities",
        "objetivo": "innovation_objective",
        "estágio": "stage_maturity",
        "estagio": "stage_maturity",
        "localização": "location_constraints",
        "localizacao": "location_constraints",
        "restrições": "location_constraints",
        "restricoes": "location_constraints",
    }
    parts = re.split(r"\s*(?:;|\n)\s*", message.strip())
    parsed: dict[str, str] = {}
    for part in parts:
        if ":" not in part:
            continue
        label, value = part.split(":", 1)
        field = labels.get(_normal(label))
        if field and value.strip():
            parsed[field] = value.strip()
    return parsed


class ConsultantRepository:
    def create(self, db, workspace_id: str, profile_snapshot: dict, profile_version: str | None = None) -> ConsultantState:
        conversation_id = str(uuid4())
        state = ConsultantState(
            conversation_id=conversation_id, workspace_id=workspace_id,
            profile_snapshot=profile_snapshot, profile_version=profile_version,
        )
        db.table("consultant_sessions").insert({
            "id": conversation_id, "workspace_id": workspace_id,
            "state": state.model_dump(mode="json"), "revision": 0,
        }).execute()
        return state

    def load(self, db, conversation_id: str, workspace_id: str) -> ConsultantState | None:
        result = (
            db.table("consultant_sessions").select("state").eq("id", conversation_id)
            .eq("workspace_id", workspace_id).maybe_single().execute()
        )
        row = result.data if result else None
        return ConsultantState.model_validate(row["state"]) if row and row.get("state") else None

    def find_idempotent(self, db, workspace_id: str, key: str) -> dict | None:
        try:
            result = (
                db.table("consultant_turns").select("response")
                .eq("idempotency_key", key)
                .eq("workspace_id", workspace_id)
                .maybe_single().execute()
            )
            row = result.data if result else None
            return row.get("response") if row else None
        except Exception:
            return None

    def turn_response(self, db, conversation_id: str, key: str) -> dict | None:
        result = (
            db.table("consultant_turns").select("response").eq("session_id", conversation_id)
            .eq("idempotency_key", key).maybe_single().execute()
        )
        row = result.data if result else None
        return row.get("response") if row else None

    def save_state(self, db, state: ConsultantState, expected_revision: int) -> None:
        result = db.table("consultant_sessions").update({
            "state": state.model_dump(mode="json"), "revision": state.revision,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", state.conversation_id).eq("workspace_id", state.workspace_id).eq(
            "revision", expected_revision,
        ).select("id").execute()
        if not result.data:
            raise ConsultantConflictError("O brief mudou em outra aba. Recarregue para revisar a versão atual.")

    def save(self, db, state: ConsultantState, key: str, response: dict, expected_revision: int) -> None:
        self.save_state(db, state, expected_revision)
        try:
            db.table("consultant_turns").insert({
                "session_id": state.conversation_id, "idempotency_key": key,
                "workspace_id": state.workspace_id, "response": response,
                "revision": state.revision,
            }).execute()
        except Exception as exc:
            logger.info("consultant: turno já persistido (%s): %s", key, exc)

    def list(self, db, workspace_id: str) -> list[dict]:
        rows = (
            db.table("consultant_sessions").select("id, state, created_at, updated_at")
            .eq("workspace_id", workspace_id).order("updated_at", desc=True).execute().data or []
        )
        out = []
        for row in rows:
            state = ConsultantState.model_validate(row.get("state") or {})
            title = (state.brief.original_intention if state.brief else "Nova conversa")[:60]
            out.append({
                "session_id": row["id"], "kind": "consultant", "title": title,
                "edital_id": None, "edital_title": None, "status": "active",
                "turn_count": len(state.messages) // 2, "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            })
        return out

    def delete(self, db, conversation_id: str, workspace_id: str) -> bool:
        result = db.table("consultant_sessions").delete().eq("id", conversation_id).eq(
            "workspace_id", workspace_id,
        ).execute()
        return bool(result.data)


class _GraphState(TypedDict, total=False):
    state: dict
    message: str
    intent: str
    events: list[str]
    assistant_message: str
    mode: str


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_confirmation(text: str) -> bool:
    value = _normal(text)
    if not value or value.startswith(("não", "nao", "ainda não", "ainda nao")):
        return False
    return value in {"sim", "confirmo", "confirmar", "pode seguir", "vamos", "é isso", "e isso"} or value.startswith(
        ("sim,", "sim ", "confirmo ", "quero trabalhar", "pode criar", "vamos trabalhar"),
    )


def _is_open_request(text: str) -> bool:
    value = _normal(text)
    return any(term in value for term in (
        "desafio corporativo", "inovação aberta", "inovacao aberta",
        "open innovation", "piloto com empresa", "pesquisar desafios",
        "pesquise desafios", "oportunidade de mercado", "canal aberto",
    ))


def _is_reassess_request(text: str) -> bool:
    value = _normal(text)
    return any(term in value for term in (
        "reavali", "reavaliar", "reavalie", "mudou o projeto", "mudei o projeto",
        "mudei meu perfil", "perfil mudou", "fonte mudou", "nova informação",
        "nova informacao",
    ))


def _record_path_transition(
    path: CaminhoInovacao,
    to_status: str,
    *,
    reason: str,
    actor: str,
    context_revision: int,
) -> None:
    path.state_history.append(PathStateTransition(
        from_status=path.status,
        to_status=to_status,
        reason=reason,
        actor=actor,
        context_revision=context_revision,
    ))
    path.status = to_status
    path.context_revision = context_revision
    path.updated_at = datetime.now(timezone.utc)


class ConsultantGraph:
    def __init__(self, pathways: Pathways):
        graph = StateGraph(_GraphState)
        graph.add_node("interpret", self._interpret)
        graph.add_node("update_brief", self._update_brief)
        graph.add_node("ask_confirmation", self._ask_confirmation)
        graph.add_node("materialize_project", self._materialize_project)
        graph.add_node("propose_path", self._propose_path)
        graph.add_node("reassess_path", self._reassess_from_turn)
        graph.add_node("continue_project", self._continue_project)
        graph.add_node("finish", self._finish)
        graph.add_edge(START, "interpret")
        graph.add_conditional_edges("interpret", self._route_after_interpret)
        graph.add_edge("update_brief", "ask_confirmation")
        graph.add_edge("ask_confirmation", "finish")
        graph.add_edge("materialize_project", "propose_path")
        graph.add_edge("propose_path", "finish")
        graph.add_edge("reassess_path", "finish")
        graph.add_edge("continue_project", "finish")
        graph.add_edge("finish", END)
        self._graph = graph.compile()
        self.pathways = pathways

    def run(self, state: ConsultantState, message: str) -> tuple[ConsultantState, str, list[str]]:
        result = self._graph.invoke({
            "state": state.model_dump(mode="json"), "message": message, "events": [],
        })
        final_state = ConsultantState.model_validate(result["state"])
        self._update_working_memory(final_state)
        return final_state, result.get("assistant_message", ""), result.get("events", [])

    def select_path(self, state: ConsultantState, path_id: str, reason: str) -> ConsultantState:
        """Única transição de seleção; o serviço só persiste o resultado."""
        path = next((item for item in state.paths if item.id == path_id), None)
        if path is None:
            raise ConsultantValidationError("Caminho não encontrado nesta conversa.")
        clean_reason = reason.strip() or "Usuário escolheu este caminho para aprofundamento."
        if (
            state.selected_path_id == path_id
            and path.status == "selected"
            and path.decision is not None
            and path.decision.reason == clean_reason
        ):
            return state
        decision = PathDecision(kind="selected", reason=clean_reason, actor="user")
        next_revision = state.revision + 1
        for item in state.paths:
            if item.id == path_id:
                _record_path_transition(
                    item, "selected", reason=clean_reason, actor="user",
                    context_revision=next_revision,
                )
                item.decision = decision
            elif item.status == "selected":
                _record_path_transition(
                    item, "proposed", reason="Outro caminho foi selecionado.", actor="system",
                    context_revision=next_revision,
                )
        state.selected_path_id = path_id
        state.next_step = path.next_step
        state.revision = next_revision
        state.updated_at = datetime.now(timezone.utc)
        if state.project is not None:
            state.project.path_ids = [item.id for item in state.paths]
            state.project.decision_history.append({
                "kind": "path_selected", "path_id": path_id,
                "reason": clean_reason, "actor": "user", "at": state.updated_at.isoformat(),
            })
        state.memory_context.append(MemoryContext(
            kind="episodic", scope="project", scope_id=(state.project.id if state.project else state.workspace_id),
            content=f"path_selected: {clean_reason}", origin="consultant_decision",
            confidence=1.0, source_ref=state.conversation_id,
        ))
        return state

    def reassess_path(self, state: ConsultantState, path_id: str, reason: str) -> ConsultantState:
        """Marca reavaliação e preserva a seleção/decisões anteriores."""
        clean_reason = reason.strip()
        if not clean_reason:
            raise ConsultantValidationError("Explique o que mudou antes de reavaliar o caminho.")
        path = next((item for item in state.paths if item.id == path_id), None)
        if path is None:
            raise ConsultantValidationError("Caminho não encontrado nesta conversa.")
        if path.status == "reassess_needed" and path.reassessment_reason == clean_reason:
            return state
        _record_path_transition(
            path, "reassess_needed", reason=clean_reason, actor="user",
            context_revision=state.revision + 1,
        )
        path.reassessment_reason = clean_reason
        path.needs_review = True
        state.needs_review = True
        state.review_state = "needs_review"
        state.gaps = [f"Reavaliar o caminho após a nova informação: {clean_reason}"]
        state.next_step = "Revalidar requisitos, validade e evidências antes de continuar."
        state.revision += 1
        state.updated_at = datetime.now(timezone.utc)
        if state.project is not None:
            state.project.decision_history.append({
                "kind": "path_reassessment_requested", "path_id": path_id,
                "reason": clean_reason, "actor": "user", "at": state.updated_at.isoformat(),
            })
        state.memory_context.append(MemoryContext(
            kind="episodic", scope="project", scope_id=(state.project.id if state.project else state.workspace_id),
            content=f"path_reassessment_requested: {clean_reason}", origin="consultant_decision",
            confidence=1.0, source_ref=state.conversation_id,
        ))
        return state

    @staticmethod
    def _update_working_memory(state: ConsultantState) -> None:
        """Mantém um resumo pequeno e legível para retomada; não é conhecimento."""
        if state.project is not None:
            stage = "caminho escolhido" if state.selected_path_id else (
                "caminhos para comparar" if state.paths else "projeto confirmado"
            )
            summary = f"Projeto {stage}. Próximo passo: {state.next_step or 'a definir'}."
            scope, scope_id = "project", state.project.id
        elif state.brief is not None:
            summary = f"Brief em revisão. Próximo passo: {state.next_step or 'confirmar o brief'}."
            scope, scope_id = "workspace", state.workspace_id
        else:
            summary = "Conversa inicial sem projeto confirmado."
            scope, scope_id = "workspace", state.workspace_id
        state.conversation_summary = summary
        state.memory_context = [item for item in state.memory_context if item.kind != "working"]
        state.memory_context.insert(0, MemoryContext(
            kind="working", scope=scope, scope_id=scope_id, content=summary,
            origin="consultant_state", confidence=1.0, source_ref=state.conversation_id,
        ))

    @staticmethod
    def _append_message(raw: dict, role: str, content: str) -> None:
        raw.setdefault("messages", []).append(
            ConsultantMessage(role=role, content=content).model_dump(mode="json"),
        )

    def _interpret(self, data: _GraphState) -> dict:
        state = ConsultantState.model_validate(data["state"])
        if state.project is not None and _is_open_request(data["message"]):
            return {"intent": "open", "mode": "open"}
        if state.project is not None and _is_reassess_request(data["message"]):
            return {"intent": "reassess", "mode": "normative"}
        return {"intent": "confirm" if state.pending_confirmation and _is_confirmation(data["message"]) else "intention", "mode": "normative"}

    @staticmethod
    def _route_after_interpret(data: _GraphState) -> str:
        state = ConsultantState.model_validate(data["state"])
        if state.project is not None:
            if data.get("intent") == "open":
                return "propose_path"
            if data.get("intent") == "reassess":
                return "reassess_path"
            return "continue_project"
        if state.brief is None or (state.pending_confirmation and data.get("intent") != "confirm"):
            return "update_brief"
        if state.pending_confirmation or state.project is None:
            return "materialize_project"
        return "update_brief"

    def _update_brief(self, data: _GraphState) -> dict:
        state = ConsultantState.model_validate(data["state"])
        now = datetime.now(timezone.utc)
        if state.brief is None:
            profile = state.profile_snapshot
            state.brief = BriefProjeto(
                original_intention=data["message"].strip(),
                problem_hypothesis=f"Problema a detalhar: {data['message'].strip()}",
                solution_hypothesis=str(profile.get("solution_summary") or ""),
            )
            state.brief_id = state.brief.id
            _brief_source(state.brief, "original_intention", "user")
            _brief_source(state.brief, "problem_hypothesis", "user")
            if state.brief.solution_hypothesis:
                _brief_source(state.brief, "solution_hypothesis", "profile")
        else:
            message = data["message"].strip()
            parsed = _parse_labeled_fields(message)
            if not parsed and state.gaps:
                missing_field = next(
                    (field for field, question in _BRIEF_QUESTIONS if question == state.gaps[0]),
                    None,
                )
                if missing_field:
                    parsed[missing_field] = message
            if parsed:
                for field, value in parsed.items():
                    if field == "technologies_capabilities":
                        setattr(state.brief, field, [item.strip() for item in value.split(",") if item.strip()])
                    else:
                        setattr(state.brief, field, value)
                    _brief_source(state.brief, field, "user")
            else:
                state.brief.original_intention = message
                _brief_source(state.brief, "original_intention", "user")
            state.brief.version += 1
            state.brief.updated_at = now
        state.brief.doubts = _brief_gaps(state.brief, state.profile_snapshot)
        state.brief.review_state = "needs_review"
        state.brief.needs_review = True
        state.gaps = list(state.brief.doubts)
        state.next_step = state.gaps[0] if state.gaps else None
        # A lacuna fica visível como pergunta/próximo passo, mas não vira uma
        # inelegibilidade silenciosa nem impede a confirmação explícita do
        # usuário. Ele pode respondê-la antes ou depois de confirmar.
        state.pending_confirmation = True
        state.review_state = "needs_review"
        state.revision += 1
        state.updated_at = now
        raw = state.model_dump(mode="json")
        self._append_message(raw, "user", data["message"].strip())
        return {"state": raw, "events": [*data.get("events", []), "brief_updated"]}

    def _ask_confirmation(self, data: _GraphState) -> dict:
        state = ConsultantState.model_validate(data["state"])
        if state.gaps:
            answer = f"Montei um brief inicial e deixei a lacuna relevante explícita. {state.gaps[0]}"
            event = "gap_question"
        else:
            answer = "O brief está pronto para revisão. Confira as premissas e confirme se quer criar o projeto e procurar um caminho real no catálogo."
            event = "confirmation_required"
        raw = state.model_dump(mode="json")
        self._append_message(raw, "assistant", answer)
        return {
            "state": raw, "assistant_message": answer,
            "events": [*data.get("events", []), event, "confirmation_required"],
        }

    def _materialize_project(self, data: _GraphState) -> dict:
        state = ConsultantState.model_validate(data["state"])
        assert state.brief is not None
        state.brief.status = "confirmed"
        state.brief.review_state = "confirmed"
        state.brief.needs_review = False
        state.pending_confirmation = False
        now = datetime.now(timezone.utc)
        decision = {
            "kind": "project_confirmed",
            "brief_version": state.brief.version,
            "at": now.isoformat(),
        }
        state.project = ProjetoInovacao(
            workspace_id=state.workspace_id, profile_snapshot=state.profile_snapshot,
            profile_version=state.profile_version, brief_id=state.brief.id,
            brief_snapshot=state.brief.model_copy(deep=True),
            decisions=["Usuário confirmou o brief."], decision_history=[decision],
        )
        state.project_id = state.project.id
        state.review_state = "confirmed"
        state.revision += 1
        state.updated_at = now
        raw = state.model_dump(mode="json")
        self._append_message(raw, "user", data["message"].strip())
        return {"state": raw, "events": [*data.get("events", []), "project_confirmed"]}

    def _propose_path(self, data: _GraphState) -> dict:
        state = ConsultantState.model_validate(data["state"])
        assert state.project is not None and state.brief is not None
        try:
            proposer = getattr(self.pathways, "propose_many", None)
            mode = data.get("mode") or "normative"
            if proposer is not None:
                paths = proposer(brief=state.brief, project=state.project, mode=mode)
            else:
                # Doubles de T01/T02 ainda retornam um único caminho.
                try:
                    paths = [self.pathways.propose(brief=state.brief, project=state.project, mode=mode)]
                except TypeError:
                    paths = [self.pathways.propose(brief=state.brief, project=state.project)]
            paths = list(paths)
            if not paths:
                raise CatalogUnavailableError("Nenhum caminho normativo encontrado.")
        except Exception as exc:
            logger.warning("consultant: falha ao propor caminho: %s", exc)
            mode = data.get("mode") or "normative"
            if mode == "open" and state.project is not None:
                state.needs_review = True
                state.gaps = ["Não encontrei um desafio aberto revisado para esta intenção; peça uma pesquisa adicional ou tente outra formulação."]
                state.next_step = "Pesquisar fontes corporativas adicionais antes de registrar uma abordagem."
                state.revision += 1
                state.updated_at = datetime.now(timezone.utc)
                answer = "Ainda não encontrei um desafio corporativo aberto com fonte suficiente. Posso pesquisar mais, mas qualquer achado continuará pendente de revisão humana."
                raw = state.model_dump(mode="json")
                self._append_message(raw, "assistant", answer)
                return {
                    "state": raw, "assistant_message": answer,
                    "events": [*data.get("events", []), "open_research_requested", "needs_review"],
                }
            state.project = None
            state.brief.status = "draft"
            state.brief.review_state = "needs_review"
            state.brief.needs_review = True
            state.review_state = "needs_review"
            state.needs_review = True
            state.gaps = ["O catálogo precisa ser revisado antes de confirmar um projeto."]
            state.next_step = "Tente novamente quando o catálogo gold estiver disponível."
            state.revision += 1
            state.updated_at = datetime.now(timezone.utc)
            answer = "Entendi a confirmação, mas não consegui validar um caminho vigente no catálogo. O projeto não foi criado; podemos tentar novamente."
            raw = state.model_dump(mode="json")
            self._append_message(raw, "assistant", answer)
            return {
                "state": raw, "assistant_message": answer,
                "events": [*data.get("events", []), "error", "needs_review"],
            }

        state.paths = paths
        for path in paths:
            if not path.state_history:
                _record_path_transition(
                    path, "proposed", reason="Caminho proposto pelo consultor.",
                    actor="assistant", context_revision=state.revision + 1,
                )
        state.project.path_ids = [path.id for path in paths]
        state.path_ids = [path.id for path in paths]
        state.gaps = paths[0].gaps
        state.next_step = paths[0].next_step
        state.needs_review = any(path.needs_review for path in paths)
        state.revision += 1
        state.updated_at = datetime.now(timezone.utc)
        mode = data.get("mode") or "normative"
        answer = (
            f"Encontrei {len(paths)} caminho(s) {'aberto(s)' if mode == 'open' else 'normativo(s)'} potencial(is). "
            f"O próximo passo do primeiro é: {paths[0].next_step}"
        )
        raw = state.model_dump(mode="json")
        self._append_message(raw, "assistant", answer)
        return {
            "state": raw, "assistant_message": answer,
            "events": [*data.get("events", []), "paths_proposed", "next_step", "market_next_step"]
            if mode == "open" else [*data.get("events", []), "paths_proposed", "next_step"],
        }

    def _reassess_from_turn(self, data: _GraphState) -> dict:
        state = ConsultantState.model_validate(data["state"])
        if state.project is None or not state.paths:
            answer = "Ainda não há um caminho persistido para reavaliar."
            raw = state.model_dump(mode="json")
            self._append_message(raw, "user", data["message"].strip())
            self._append_message(raw, "assistant", answer)
            return {"state": raw, "assistant_message": answer, "events": ["needs_review"]}

        path = next(
            (item for item in state.paths if item.id == state.selected_path_id),
            state.paths[0],
        )
        reason = data["message"].strip()
        state = self.reassess_path(state, path.id, reason)
        answer = (
            f"Marquei o caminho para reavaliação por causa de: {reason} "
            "A decisão anterior foi preservada; vou revalidar as evidências antes do próximo passo."
        )
        raw = state.model_dump(mode="json")
        self._append_message(raw, "user", reason)
        self._append_message(raw, "assistant", answer)
        return {
            "state": raw, "assistant_message": answer,
            "events": ["path_reassessment_requested", "needs_review", "next_step"],
        }

    @staticmethod
    def _finish(data: _GraphState) -> dict:
        return {"state": data["state"], "assistant_message": data.get("assistant_message", ""), "events": data.get("events", [])}

    def _continue_project(self, data: _GraphState) -> dict:
        state = ConsultantState.model_validate(data["state"])
        if state.selected_path_id:
            selected = next((item for item in state.paths if item.id == state.selected_path_id), None)
            path_label = (selected.kind or selected.tipo) if selected else "caminho"
            status = selected.status if selected else "selected"
            answer = (
                f"Retomei o projeto e o {path_label} escolhido está em estado “{status}”. "
                f"Próximo passo: {state.next_step or 'a definir'}. "
                "Se algo mudou no projeto, perfil ou fonte, peça uma reavaliação explícita."
            )
        elif state.paths:
            answer = (
                f"Retomei o projeto com {len(state.paths)} caminhos para comparar. "
                "A escolha ainda está pendente."
            )
        else:
            answer = "Retomei o projeto confirmado. Posso continuar a partir do brief e pesquisar um caminho."
        raw = state.model_dump(mode="json")
        self._append_message(raw, "user", data["message"].strip())
        self._append_message(raw, "assistant", answer)
        return {"state": raw, "assistant_message": answer, "events": ["project_context_reused"]}


def profile_snapshot(db, workspace_id: str) -> dict:
    raw, _ = _profile_record(db, workspace_id)
    return raw


def _profile_record(db, workspace_id: str) -> tuple[dict, str | None]:
    result = db.table("workspaces").select("profile, profile_updated_at").eq(
        "id", workspace_id,
    ).maybe_single().execute()
    row = (result.data if result else None) or {}
    raw = row.get("profile") or {}
    snapshot = {key: raw[key] for key in PROFILE_FIELD_NAMES if key in raw}
    return snapshot, row.get("profile_updated_at")


class ConsultantService:
    def __init__(self, repository: ConsultantRepository | None = None):
        self.repository = repository or ConsultantRepository()

    def turn(
        self,
        db,
        workspace_id: str,
        message: str,
        conversation_id: str | None,
        idempotency_key: str,
        expected_revision: int | None = None,
    ) -> dict:
        if not conversation_id:
            prior = self.repository.find_idempotent(db, workspace_id, idempotency_key)
            if prior is not None:
                return prior
            snapshot, profile_version = _profile_record(db, workspace_id)
            state = self.repository.create(db, workspace_id, snapshot, profile_version)
        else:
            state = self.repository.load(db, conversation_id, workspace_id)
            if state is None:
                raise ConsultantNotFoundError("Conversa do consultor não encontrada.")
            if expected_revision is not None and state.revision != expected_revision:
                raise ConsultantConflictError("O brief mudou em outra aba. Recarregue para revisar a versão atual.")
            prior = self.repository.turn_response(db, conversation_id, idempotency_key)
            if prior is not None:
                return prior
        persist_revision = state.revision

        snapshot, profile_version = _profile_record(db, workspace_id)
        if state.project is not None and (
            profile_version != state.profile_version or snapshot != state.profile_snapshot
        ):
            drift_reason = "O perfil da empresa mudou desde a confirmação deste projeto."
            for path in state.paths:
                if path.status not in {"discarded", "completed", "reassess_needed"}:
                    _record_path_transition(
                        path, "reassess_needed", reason=drift_reason, actor="system",
                        context_revision=state.revision + 1,
                    )
                    path.reassessment_reason = drift_reason
                    path.needs_review = True
            state.needs_review = True
            state.review_state = "needs_review"
            state.gaps = [drift_reason]
            state.next_step = "Reavalie os caminhos usando o perfil atual antes de continuar."
            state.revision += 1
            state.updated_at = datetime.now(timezone.utc)

        self._hydrate_memory_context(db, state, message)

        pathways = GoldPathways(
            RelationalKnowledge(), workspace_id=workspace_id, db=db,
            open_knowledge=OpenKnowledge(db),
        )
        research_staged = False
        if state.project is not None and _is_open_request(message) and any(
            token in _normal(message) for token in ("pesquis", "busque", "procure")
        ):
            research_staged = self._stage_open_research(
                db, workspace_id, state, message,
            )
        next_state, answer, events = ConsultantGraph(pathways).run(state, message)
        if research_staged:
            events = [*events, "research_staged"]
        response = {
            "conversation_id": next_state.conversation_id, "assistant_message": answer,
            "events": events, "state": next_state.model_dump(mode="json"),
            "brief_id": next_state.brief_id, "project_id": next_state.project_id,
            "path_ids": next_state.path_ids,
        }
        self.repository.save(db, next_state, idempotency_key, response, persist_revision)
        return response

    @staticmethod
    def _hydrate_memory_context(db, state: ConsultantState, query: str) -> None:
        """Recupera memória autorizada sem promovê-la a fato do Knowledge."""
        state.memory_context = [
            item for item in state.memory_context
            if item.kind not in {"semantic", "episodic"}
        ]
        project_id = state.project.id if state.project is not None else None
        if project_id:
            for decision in state.project.decision_history[-6:]:
                kind = str(decision.get("kind") or "decisão")
                reason = str(decision.get("reason") or kind)
                state.memory_context.append(MemoryContext(
                    kind="episodic", scope="project", scope_id=project_id,
                    content=f"{kind}: {reason}", origin="consultant_decision",
                    confidence=1.0, source_ref=state.conversation_id,
                ))

        insights: list[dict] = []
        try:
            from radar.core.llm.agent_graph import memory_search
            insights = memory_search(state.workspace_id, query, limit=4)
        except Exception as exc:
            logger.debug("consultant: memória semântica indisponível: %s", exc)
        if not insights:
            try:
                from radar.core.reflection_service import load_active_insights
                insights = load_active_insights(db, state.workspace_id, max_total=4)
            except Exception as exc:
                logger.debug("consultant: insights curados indisponíveis: %s", exc)
        for insight in insights:
            content = str(insight.get("insight") or "").strip()
            if not content:
                continue
            state.memory_context.append(MemoryContext(
                kind="semantic", scope="workspace", scope_id=state.workspace_id,
                content=content, origin="reflection_insights", confidence={
                    1: 0.6, 2: 0.8, 3: 0.9,
                }.get(insight.get("level"), 0.5), read_allowed=True,
                source_ref="reflection_insights",
            ))

    @staticmethod
    def _stage_open_research(
        db, workspace_id: str, state: ConsultantState, request: str,
    ) -> bool:
        """Pesquisa adicional sob solicitação e grava somente em staging."""
        try:
            from radar.core.deep_research import run_deep_research

            brief = state.brief
            query = request.strip()
            if brief is not None:
                query = (
                    f"{request.strip()} Contexto do projeto: {brief.problem_hypothesis}. "
                    f"Solução: {brief.solution_hypothesis}."
                )
            result = run_deep_research(query)
            if not (result.answer or result.sources):
                return False
            sources = [
                {"url": source.url, "title": source.title, "snippet": source.snippet}
                for source in result.sources
            ]
            db.table("research_findings").insert({
                "workspace_id": workspace_id,
                "question": query[:2_000],
                "answer": result.answer,
                "sources": json.loads(json.dumps(sources)),
                "query": request[:2_000],
                "verified": False,
            }).execute()
            return True
        except Exception as exc:
            logger.warning("consultant: pesquisa aberta não foi para staging: %s", exc)
            return False

    def update_brief(
        self,
        db,
        workspace_id: str,
        conversation_id: str,
        expected_revision: int,
        updates: dict,
    ) -> dict:
        state = self.repository.load(db, conversation_id, workspace_id)
        if state is None:
            raise ConsultantNotFoundError("Conversa do consultor não encontrada.")
        if state.revision != expected_revision:
            raise ConsultantConflictError("O brief mudou em outra aba. Recarregue para revisar a versão atual.")
        if state.brief is None:
            raise ConsultantValidationError("Ainda não existe um brief para editar.")
        if state.project is not None or state.brief.status == "confirmed":
            raise ConsultantValidationError("O projeto já foi confirmado; não é possível editar o brief como rascunho.")

        allowed = {
            "original_intention", "problem_hypothesis", "affected_users", "solution_hypothesis",
            "technologies_capabilities", "innovation_objective", "stage_maturity",
            "location_constraints", "impact_expected", "partnership_needs",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ConsultantValidationError(f"Campos de brief não suportados: {', '.join(sorted(unknown))}.")
        for field, value in updates.items():
            if field == "technologies_capabilities":
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise ConsultantValidationError("technologies_capabilities deve ser uma lista de textos.")
                value = [item.strip() for item in value if item.strip()]
            elif not isinstance(value, str):
                raise ConsultantValidationError(f"O campo {field} deve ser texto.")
            setattr(state.brief, field, value)
            _brief_source(state.brief, field, "user")

        state.brief.version += 1
        state.brief.updated_at = datetime.now(timezone.utc)
        state.brief.review_state = "needs_review"
        state.brief.needs_review = True
        state.gaps = _brief_gaps(state.brief, state.profile_snapshot)
        state.brief.doubts = list(state.gaps)
        state.next_step = state.gaps[0] if state.gaps else None
        state.pending_confirmation = True
        state.review_state = "needs_review"
        state.revision += 1
        state.updated_at = datetime.now(timezone.utc)
        self.repository.save_state(db, state, expected_revision)
        return {"conversation_id": conversation_id, "state": state.model_dump(mode="json")}

    def confirm_project(self, db, workspace_id: str, conversation_id: str, expected_revision: int) -> dict:
        state = self.repository.load(db, conversation_id, workspace_id)
        if state is None:
            raise ConsultantNotFoundError("Conversa do consultor não encontrada.")
        if state.revision != expected_revision:
            raise ConsultantConflictError("O brief mudou em outra aba. Recarregue para revisar a versão atual.")
        if state.project is not None:
            return {"conversation_id": conversation_id, "state": state.model_dump(mode="json")}
        if state.brief is None or not state.pending_confirmation:
            raise ConsultantValidationError("Revise o brief antes de confirmar o projeto.")

        pathways = GoldPathways(
            RelationalKnowledge(), workspace_id=workspace_id, db=db,
            open_knowledge=OpenKnowledge(db),
        )
        next_state, answer, events = ConsultantGraph(pathways).run(state, "Confirmo este brief")
        response = {
            "conversation_id": conversation_id, "assistant_message": answer,
            "events": events, "state": next_state.model_dump(mode="json"),
            "brief_id": next_state.brief_id, "project_id": next_state.project_id,
            "path_ids": next_state.path_ids,
        }
        self.repository.save(db, next_state, f"project-confirm:{expected_revision}", response, expected_revision)
        return response

    def select_path(
        self, db, workspace_id: str, conversation_id: str, path_id: str,
        expected_revision: int, reason: str = "",
    ) -> dict:
        """Registra a intenção de seguir um caminho; não submete nem inicia escrita."""
        state = self.repository.load(db, conversation_id, workspace_id)
        if state is None:
            raise ConsultantNotFoundError("Conversa do consultor não encontrada.")
        path = next((item for item in state.paths if item.id == path_id), None)
        if path is None:
            raise ConsultantValidationError("Caminho não encontrado nesta conversa.")
        clean_reason = reason.strip() or "Usuário escolheu este caminho para aprofundamento."
        if (
            state.selected_path_id == path_id
            and path.status == "selected"
            and path.decision is not None
            and path.decision.reason == clean_reason
        ):
            return {"conversation_id": conversation_id, "events": ["path_selected"], "state": state.model_dump(mode="json")}
        if state.revision != expected_revision:
            raise ConsultantConflictError("O caminho mudou em outra aba. Recarregue para revisar a versão atual.")
        state = ConsultantGraph(
            GoldPathways(RelationalKnowledge(), workspace_id=workspace_id, db=db),
        ).select_path(state, path_id, clean_reason)
        ConsultantGraph._update_working_memory(state)
        self.repository.save_state(db, state, expected_revision)
        events = ["path_selected", "next_step"]
        if path.kind == "open_innovation":
            events.append("market_next_step")
        return {
            "conversation_id": conversation_id,
            "events": events,
            "state": state.model_dump(mode="json"),
        }

    def reassess_path(
        self, db, workspace_id: str, conversation_id: str, path_id: str,
        expected_revision: int, reason: str,
    ) -> dict:
        """Marca uma nova avaliação sem reescrever o histórico anterior."""
        clean_reason = reason.strip()
        if not clean_reason:
            raise ConsultantValidationError("Explique o que mudou antes de reavaliar o caminho.")
        state = self.repository.load(db, conversation_id, workspace_id)
        if state is None:
            raise ConsultantNotFoundError("Conversa do consultor não encontrada.")
        path = next((item for item in state.paths if item.id == path_id), None)
        if path is None:
            raise ConsultantValidationError("Caminho não encontrado nesta conversa.")
        if path.status == "reassess_needed" and path.reassessment_reason == clean_reason:
            return {"conversation_id": conversation_id, "events": ["path_reassessment_requested"], "state": state.model_dump(mode="json")}
        if state.revision != expected_revision:
            raise ConsultantConflictError("O caminho mudou em outra aba. Recarregue para revisar a decisão.")

        state = ConsultantGraph(
            GoldPathways(RelationalKnowledge(), workspace_id=workspace_id, db=db),
        ).reassess_path(state, path_id, clean_reason)
        ConsultantGraph._update_working_memory(state)
        self.repository.save_state(db, state, expected_revision)
        return {
            "conversation_id": conversation_id,
            "events": ["path_reassessment_requested", "needs_review", "next_step"],
            "state": state.model_dump(mode="json"),
        }

    def get(self, db, workspace_id: str, conversation_id: str) -> dict:
        state = self.repository.load(db, conversation_id, workspace_id)
        if state is None:
            raise ConsultantNotFoundError("Conversa do consultor não encontrada.")
        return {"conversation_id": conversation_id, "state": state.model_dump(mode="json")}


consultant_service = ConsultantService()
