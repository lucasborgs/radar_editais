"""Contratos mínimos da jornada do consultor estratégico v1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceReference(BaseModel):
    kind: str = "catalog"
    ref: str
    label: str
    locator: str | None = None
    locator_quality: Literal["exact", "document_only", "unresolved"] = "unresolved"
    quote: str | None = None
    document: str | None = None
    source_url: str | None = None
    source_hash: str | None = None
    version: str | None = None
    source_role: Literal["primary", "corroborating", "secondary", "unknown"] = "unknown"


# Nome de domínio usado pela vertical normativa. O alias mantém o contrato
# mínimo introduzido em T01/T02 estável para os consumidores existentes.
Evidence = EvidenceReference


PathStatus = Literal[
    "proposed",
    "investigating",
    "selected",
    "reassess_needed",
    "discarded",
    "completed",
]


class PathDecision(BaseModel):
    """Decisão humana sobre um caminho; nunca é um fato do catálogo."""

    kind: Literal["selected", "discarded", "completed"]
    reason: str
    decided_at: datetime = Field(default_factory=_now)
    actor: Literal["user", "assistant", "system"] = "user"


class PathStateTransition(BaseModel):
    """Auditoria curta das transições do lifecycle do caminho."""

    from_status: PathStatus | None = None
    to_status: PathStatus
    reason: str = ""
    at: datetime = Field(default_factory=_now)
    actor: Literal["user", "assistant", "system"] = "system"
    context_revision: int = 0


class MemoryContext(BaseModel):
    """Contexto recuperado, explicitamente separado de conhecimento canônico."""

    kind: Literal["working", "episodic", "semantic", "procedural"]
    scope: Literal["workspace", "project"]
    scope_id: str
    content: str
    origin: str
    confidence: float = 0.0
    read_allowed: bool = True
    source_ref: str | None = None
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)


class RuleEvaluation(BaseModel):
    """Resultado de uma regra dura, sem transformar desconhecido em rejeição."""

    rule: str
    status: Literal["satisfied", "unknown", "unsatisfied"]
    reason: str
    evidence: list[EvidenceReference] = Field(default_factory=list)


class KnowledgeSignal(BaseModel):
    """Sinal factual que atravessa o adapter Knowledge sem expor o backend."""

    entity: dict = Field(default_factory=dict)
    kind: Literal["channel", "opportunity", "actor", "document"] = "opportunity"
    role: str = "opportunity"
    formal_instrument: bool = True
    knowledge_level: Literal["fact", "inference", "unknown"] = "fact"
    validity: Literal["unknown", "needs_review", "active"] = "needs_review"
    evidence_refs: list[EvidenceReference] = Field(default_factory=list)
    claims: list[dict] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    reason: str = ""


class SourceDocumentRef(BaseModel):
    """Referência estável ao documento, sem duplicar o SourceBundle."""

    ref: str
    label: str
    source_url: str | None = None
    role: str = "opportunity_page"
    collected_at: datetime | None = None
    content_hash: str | None = None


class DocumentClaim(BaseModel):
    subject: str
    predicate: str
    value: str
    confidence: float = 0.0
    evidence: list[EvidenceReference] = Field(default_factory=list)


class DocumentIntelligenceResult(BaseModel):
    """Pacote de descoberta consumido pelo caminho aberto."""

    document: SourceDocumentRef
    claims: list[DocumentClaim] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    freshness: dict = Field(default_factory=dict)
    confidence: float = 0.0
    review_state: Literal["draft", "needs_review", "confirmed"] = "draft"
    source_kind: Literal["discovery", "research", "curated"] = "discovery"

    @property
    def source_document_ref(self) -> SourceDocumentRef:
        return self.document


class CanalInovacao(BaseModel):
    """Entrada aberta: uma página/canal, não um instrumento de edital."""

    id: str
    kind: Literal["channel", "opportunity"] = "opportunity"
    formal_instrument: bool = False
    page: SourceDocumentRef
    promoter: str = ""
    challenge: str = ""
    corroborating_sources: list[EvidenceReference] = Field(default_factory=list)
    collected_at: datetime | None = None
    freshness: dict = Field(default_factory=dict)
    review_state: Literal["draft", "needs_review", "confirmed"] = "draft"
    confidence: float = 0.0


# Nome usado na spec quando a página representa uma oportunidade individual.
Oportunidade = CanalInovacao


class BriefProjeto(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: Literal["draft", "confirmed"] = "draft"
    original_intention: str
    problem_hypothesis: str = ""
    affected_users: str = ""
    solution_hypothesis: str = ""
    technologies_capabilities: list[str] = Field(default_factory=list)
    innovation_objective: str = ""
    stage_maturity: str = ""
    location_constraints: str = ""
    impact_expected: str = ""
    partnership_needs: str = ""
    doubts: list[str] = Field(default_factory=list)
    source_refs: dict[str, list[str]] = Field(default_factory=dict)
    review_state: Literal["draft", "needs_review", "confirmed"] = "draft"
    origin: str = "conversation"
    version: int = 1
    confidence: float = 0.5
    needs_review: bool = True
    updated_at: datetime = Field(default_factory=_now)


class ProjetoInovacao(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: Literal["confirmed"] = "confirmed"
    workspace_id: str
    empresa_id: str | None = None
    profile_snapshot: dict = Field(default_factory=dict)
    profile_version: str | None = None
    brief_id: str
    brief_snapshot: BriefProjeto | None = None
    decisions: list[str] = Field(default_factory=list)
    decision_history: list[dict] = Field(default_factory=list)
    path_ids: list[str] = Field(default_factory=list)
    version: int = 1
    confidence: float = 0.5
    needs_review: bool = True
    review_state: Literal["confirmed", "needs_review"] = "confirmed"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CaminhoInovacao(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: PathStatus = "proposed"
    tipo: str = ""
    # T01-T03 chamavam este campo de ``tipo``. ``kind`` é o contrato rico das
    # verticais e fica sincronizado para não quebrar consumidores existentes.
    kind: str | None = None
    project_id: str
    entity_ref: str
    opportunity_ref: str | None = None
    actors: list[dict] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendation: str
    next_step: str
    evidence: list[EvidenceReference] = Field(default_factory=list)
    claims: list[dict] = Field(default_factory=list)
    claim_gaps: list[str] = Field(default_factory=list)
    rule_evaluations: list[RuleEvaluation] = Field(default_factory=list)
    temporal_state: str = "needs_review"
    last_evaluated_at: datetime | None = None
    confidence: float = 0.5
    needs_review: bool = True
    source: str | None = None
    formal_instrument: bool = True
    freshness: dict = Field(default_factory=dict)
    decision: PathDecision | None = None
    state_history: list[PathStateTransition] = Field(default_factory=list)
    reassessment_reason: str | None = None
    context_revision: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def model_post_init(self, __context) -> None:
        if self.kind is None:
            self.kind = self.tipo or "financiamento"
        elif not self.tipo:
            self.tipo = self.kind


class ConsultantMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=_now)


class ConsultantState(BaseModel):
    conversation_id: str
    workspace_id: str
    profile_snapshot: dict = Field(default_factory=dict)
    profile_version: str | None = None
    messages: list[ConsultantMessage] = Field(default_factory=list)
    brief_id: str | None = None
    project_id: str | None = None
    path_ids: list[str] = Field(default_factory=list)
    brief: BriefProjeto | None = None
    project: ProjetoInovacao | None = None
    paths: list[CaminhoInovacao] = Field(default_factory=list)
    selected_path_id: str | None = None
    gaps: list[str] = Field(default_factory=list)
    next_step: str | None = None
    pending_confirmation: bool = False
    revision: int = 0
    needs_review: bool = False
    review_state: Literal["draft", "needs_review", "confirmed"] = "draft"
    conversation_summary: str = ""
    memory_context: list[MemoryContext] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_now)


class WritingContext(BaseModel):
    """Snapshot autorizado que liga um caminho selecionado à escrita.

    O catálogo continua sendo a autoridade sobre fatos e regras; este snapshot
    apenas congela o contexto que uma sessão de escrita pode consumir.
    """

    project_id: str
    path_id: str
    path_revision: int = 0
    profile_version: str | None = None
    artifact_type: str
    formal_instrument: bool = True
    source_refs: list[EvidenceReference] = Field(default_factory=list)
    retrieval_scope: list[str] = Field(default_factory=list)
    allowed_materials: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    claims: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
