import { API_BASE_URL } from "./constants";
import { createSupabaseClient } from "./supabase";
import type { EditalEntry, EditalCard, OportunidadeDetail, DashboardStats } from "@/types/edital";
import type { OpportunityEntry } from "@/types/oportunidade";
import type { CompanyProfile } from "@/types/profile";
import type {
  WritingTurnResponse,
  SectionStartResponse,
  ExtractProfileResponse,
  ContentItemSummary,
  ContentItemFull,
} from "@/types/api";

// Recupera o JWT corrente da sessão Supabase (lazy).
async function getAccessToken(): Promise<string | undefined> {
  try {
    const supabase = createSupabaseClient();
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? undefined;
  } catch {
    return undefined;
  }
}

// Erro tipado para falhas de API: carrega status, mensagem amigável e o
// request_id devolvido pelo backend (útil para suporte/correlação de logs).
export type ApiErrorKind = "connection" | "server" | "request";

export class ApiError extends Error {
  status: number;
  requestId?: string;
  kind: ApiErrorKind;
  constructor(message: string, status: number, requestId?: string, kind?: ApiErrorKind) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.kind = kind ?? (status >= 500 ? "server" : "request");
  }
}

const STATUS_MESSAGES: Record<number, string> = {
  401: "Sessão expirada. Faça login novamente.",
  403: "Você não tem permissão para esta ação.",
  404: "Recurso não encontrado.",
  429: "Muitas requisições em pouco tempo. Aguarde um instante e tente novamente.",
  500: "Erro interno no servidor. Tente novamente.",
  502: "Servidor indisponível no momento. Tente novamente.",
  503: "Servidor indisponível no momento. Tente novamente.",
};

// Constrói um ApiError a partir de uma resposta não-ok, extraindo `detail`
// (string ou lista de validação do FastAPI) e `request_id` quando presentes.
async function buildApiError(res: Response): Promise<ApiError> {
  let detailMsg: string | undefined;
  let requestId: string | undefined;
  try {
    const body = await res.json();
    requestId = body?.request_id;
    const detail = body?.detail;
    if (typeof detail === "string") {
      detailMsg = detail;
    } else if (Array.isArray(detail)) {
      // 422 do FastAPI: [{ loc, msg, type }]
      detailMsg = detail.map((d) => d?.msg).filter(Boolean).join("; ") || undefined;
    }
  } catch {
    // corpo não-JSON ou vazio
  }
  const message = detailMsg || STATUS_MESSAGES[res.status] || "Ocorreu um erro inesperado.";
  return new ApiError(message, res.status, requestId);
}

async function apiFetch<T>(
  path: string,
  options?: RequestInit,
  token?: string
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // Token explícito > sessão Supabase corrente > sem header (rota pública).
  const effectiveToken = token ?? (await getAccessToken());
  if (effectiveToken) headers["Authorization"] = `Bearer ${effectiveToken}`;
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      headers,
      ...options,
    });
  } catch {
    // fetch lança apenas para falhas de rede (DNS, TCP, CORS, offline) — nunca
    // para respostas HTTP. Classificamos como erro de conexão.
    throw new ApiError(
      "Falha de conexão com o servidor. Verifique sua internet e tente novamente.",
      0,
      undefined,
      "connection",
    );
  }
  if (!res.ok) throw await buildApiError(res);
  // 204 No Content (delete/archive/unarchive da library): corpo vazio —
  // res.json() estouraria SyntaxError mesmo com sucesso.
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ── Auth / Profile ─────────────────────────────────────────

export const getMe = (token: string) =>
  apiFetch<{
    user_id: string;
    workspace_id: string;
    profile: Partial<CompanyProfile>;
    // Operador do sistema (ADMIN_EMAILS no backend) — controla ferramentas de
    // gestão na UI (ex.: fila da Descoberta).
    is_admin?: boolean;
  }>(
    "/me",
    undefined,
    token
  );

export const saveProfile = (profile: CompanyProfile, token: string) =>
  apiFetch<{ success: boolean; profile: CompanyProfile }>("/me/profile", {
    method: "PUT",
    body: JSON.stringify(profile),
  }, token);

// ── Editais ────────────────────────────────────────────────

export interface EditaisFilters {
  status?: string;
  tema?: string;
  limit?: number;
}

export const getEditais = (filters?: EditaisFilters) => {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.tema) params.set("tema", filters.tema);
  if (filters?.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return apiFetch<EditalEntry[]>(`/editais${qs ? `?${qs}` : ""}`);
};

export const getEditalById = (id: string) =>
  apiFetch<EditalCard>(`/editais/${id}`);

// Ficha unificada por Oportunidade (KG v2 / PR8) — resolve edital, programa ou
// investimento (D1). `id` pode conter ':' (curados: `investidor:indicator capital`).
export const getOportunidadeById = (id: string) =>
  apiFetch<OportunidadeDetail>(`/oportunidades/${encodeURIComponent(id)}`);

export const getDashboardStats = () =>
  apiFetch<DashboardStats>("/stats");

export interface OpportunitiesFilters {
  tipo?: string;
  limit?: number;
}

export const getOpportunities = (filters?: OpportunitiesFilters) => {
  const params = new URLSearchParams();
  if (filters?.tipo) params.set("tipo", filters.tipo);
  if (filters?.limit) params.set("limit", String(filters.limit));
  const qs = params.toString();
  return apiFetch<OpportunityEntry[]>(`/opportunities${qs ? `?${qs}` : ""}`);
};

// ── ConsultantGraph (SCV1-T01) ─────────────────────────────
export interface ConsultantBrief {
  id: string;
  status: "draft" | "confirmed";
  original_intention: string;
  problem_hypothesis: string;
  affected_users: string;
  solution_hypothesis: string;
  technologies_capabilities: string[];
  innovation_objective: string;
  stage_maturity: string;
  location_constraints: string;
  impact_expected: string;
  partnership_needs: string;
  doubts: string[];
  source_refs: Record<string, string[]>;
  review_state: "draft" | "needs_review" | "confirmed";
  version: number;
  confidence: number;
  needs_review: boolean;
  updated_at: string;
}

export interface ConsultantPath {
  id: string;
  status: "proposed" | "investigating" | "selected" | "reassess_needed" | "discarded" | "completed";
  tipo: string;
  kind?: string | null;
  project_id: string;
  entity_ref: string;
  opportunity_ref?: string | null;
  actors: Array<Record<string, unknown>>;
  facts: string[];
  inferences: string[];
  requirements: string[];
  gaps: string[];
  risks: string[];
  recommendation: string;
  next_step: string;
  evidence: Array<{
    kind: string;
    ref: string;
    label: string;
    locator?: string | null;
    quote?: string | null;
    document?: string | null;
    source_url?: string | null;
    source_hash?: string | null;
    version?: string | null;
    source_role?: string;
  }>;
  rule_evaluations: Array<{
    rule: string;
    status: "satisfied" | "unknown" | "unsatisfied";
    reason: string;
  }>;
  temporal_state: string;
  formal_instrument?: boolean;
  freshness?: Record<string, unknown>;
  last_evaluated_at?: string | null;
  confidence: number;
  needs_review: boolean;
  source?: string | null;
  decision?: {
    kind: "selected" | "discarded" | "completed";
    reason: string;
    decided_at: string;
    actor: "user" | "assistant" | "system";
  } | null;
  state_history: Array<{
    from_status?: ConsultantPath["status"] | null;
    to_status: ConsultantPath["status"];
    reason: string;
    at: string;
    actor: "user" | "assistant" | "system";
    context_revision: number;
  }>;
  reassessment_reason?: string | null;
  context_revision: number;
}

export interface ConsultantJourneyState {
  conversation_id: string;
  workspace_id: string;
  profile_snapshot: Record<string, unknown>;
  profile_version: string | null;
  messages: Array<{ id: string; role: "user" | "assistant"; content: string; created_at: string }>;
  brief_id: string | null;
  project_id: string | null;
  path_ids: string[];
  brief: ConsultantBrief | null;
  project: {
    id: string;
    status: "confirmed";
    workspace_id: string;
    brief_id: string;
    profile_version: string | null;
    brief_snapshot: ConsultantBrief | null;
    decisions: string[];
    decision_history: Array<Record<string, unknown>>;
    path_ids: string[];
  } | null;
  paths: ConsultantPath[];
  selected_path_id: string | null;
  gaps: string[];
  next_step: string | null;
  pending_confirmation: boolean;
  revision: number;
  needs_review: boolean;
  review_state: "draft" | "needs_review" | "confirmed";
  conversation_summary: string;
  memory_context: Array<{
    kind: "working" | "episodic" | "semantic" | "procedural";
    scope: "workspace" | "project";
    scope_id: string;
    content: string;
    origin: string;
    confidence: number;
    read_allowed: boolean;
    source_ref?: string | null;
  }>;
  updated_at: string;
}

export interface ConsultantTurnResult {
  conversation_id: string;
  assistant_message: string;
  events: string[];
  state: ConsultantJourneyState;
}

export const consultantTurn = (
  message: string,
  conversationId: string | null,
  idempotencyKey: string,
  expectedRevision?: number,
) =>
  apiFetch<ConsultantTurnResult>("/consultant/turn", {
    method: "POST",
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      idempotency_key: idempotencyKey,
      expected_revision: expectedRevision,
    }),
  });

export type ConsultantBriefUpdate = Partial<Pick<
  ConsultantBrief,
  | "original_intention"
  | "problem_hypothesis"
  | "affected_users"
  | "solution_hypothesis"
  | "technologies_capabilities"
  | "innovation_objective"
  | "stage_maturity"
  | "location_constraints"
  | "impact_expected"
  | "partnership_needs"
>>;

export const updateConsultantBrief = (
  conversationId: string,
  expectedRevision: number,
  updates: ConsultantBriefUpdate,
  token: string,
) =>
  apiFetch<{ conversation_id: string; state: ConsultantJourneyState }>(
    `/consultant/${encodeURIComponent(conversationId)}/brief`,
    {
      method: "PATCH",
      body: JSON.stringify({ expected_revision: expectedRevision, ...updates }),
    },
    token,
  );

export const confirmConsultantProject = (
  conversationId: string,
  expectedRevision: number,
  token: string,
) =>
  apiFetch<ConsultantTurnResult>(
    `/consultant/${encodeURIComponent(conversationId)}/project/confirm`,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision }) },
    token,
  );

export const selectConsultantPath = (
  conversationId: string,
  pathId: string,
  expectedRevision: number,
  reason: string,
  token: string,
) =>
  apiFetch<{ conversation_id: string; events: string[]; state: ConsultantJourneyState }>(
    `/consultant/${encodeURIComponent(conversationId)}/paths/${encodeURIComponent(pathId)}/select`,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision, reason }) },
    token,
  );

export const reassessConsultantPath = (
  conversationId: string,
  pathId: string,
  expectedRevision: number,
  reason: string,
  token: string,
) =>
  apiFetch<{ conversation_id: string; events: string[]; state: ConsultantJourneyState }>(
    `/consultant/${encodeURIComponent(conversationId)}/paths/${encodeURIComponent(pathId)}/reassess`,
    { method: "POST", body: JSON.stringify({ expected_revision: expectedRevision, reason }) },
    token,
  );

export const getConsultantState = (conversationId: string, token: string) =>
  apiFetch<{ conversation_id: string; state: ConsultantJourneyState }>(
    `/consultant/${encodeURIComponent(conversationId)}`,
    undefined,
    token,
  );

export const deleteConsultantState = (conversationId: string, token: string) =>
  apiFetch<{ ok: boolean }>(`/consultant/${encodeURIComponent(conversationId)}`, { method: "DELETE" }, token);

// ── Compatibilidade de perfil e objetos antigos ───────────

export interface ProfileDiffItem {
  field: keyof CompanyProfile;
  label: string;              // já em PT-BR (vem do backend)
  old: unknown;
  new: unknown;
}

// Par de trechos reais que gerou o match (motor v3, Stage 2) — a explicação
// do card: trecho da empresa ↔ trecho do edital/tese, com o cosseno do par.
export interface MatchedExcerpt {
  company_text: string;
  edital_text: string;
  section?: string | null;
  score: number;
  origin?: "profile" | "library_doc";
}

// Elegibilidade dura (Stage 1 do funil v3). `inelegivel` nunca chega ao
// front (é filtrado antes); sobram `elegivel` e `nao_verificada` (perfil
// incompleto → o card mostra "elegibilidade não verificada" + o que completar).
export interface Elegibilidade {
  status: "elegivel" | "nao_verificada" | "inelegivel";
  unsat: string[];
  unknown: string[];
}

// Tipo mantido para leitura de objetos históricos; não é parte da jornada nova.
export interface MatchVerdict {
  racional_afinidade: string;
  red_flags_elegibilidade: string[];
  fit_mecanismo: string;
  recomendacao: "alta" | "media" | "baixa";
}

export interface PathEvidence {
  tipo: "tema" | "trecho";
  detalhe?: string;
  empresa?: string;
  oportunidade?: string;
}

// Contrato mínimo do caminho de inovação (spec product-pathways-domain-matching.md)
export interface InnovationPath {
  tipo: string;                       // financiamento | credito | subvencao | bolsa | desafio | aceleradora | incubadora | ict
  entidade: string;                   // native_id
  objetivo: string;
  requisitos: string[];
  canal_de_acesso: string;
  evidencias: PathEvidence[];
  status: string;                     // possibilidade | lacunas | candidatura_viável
  proximo_passo: string;
}

export interface PathExplanation {
  tipo: string;
  dominio: string;
  criterios: string;
  confirmados: string[];
  inferidos: string[];
  pendentes: string[];
  lacunas: string[];
  proximo_passo: string;
}

export interface MatchedEdital {
  kind: "edital";
  source: string;
  edital_id: string;
  entity_id: string;          // native_id ("finep:589")
  name: string;
  description: string;
  score: number;              // afinidade de escopo (métrica canônica; ring)
  affinity: number;           // média dos máximos (0..1) — ranking
  technical_score?: number | null;
  setores: string[];
  matched_excerpts: MatchedExcerpt[];
  status: string | null;
  prazo: string | null;
  valor: string | null;
  url?: string | null;
  elegibilidade?: Elegibilidade | null;
  verdict?: MatchVerdict | null;
  temporal_mode?: string | null;
  validity_state?: string | null;
  temporal_value?: string | null;
  decision_source?: string | null;
  last_verified_at?: string | null;
  tipo?: string | null;
  caminho?: InnovationPath | null;
  explicacao?: PathExplanation | null;
}

export interface MatchedEntity {
  kind: "programa";
  entity_id: string;          // native_id ("programa:centelha")
  name: string;
  description: string | null;
  score: number;
  affinity: number;           // mesma escala 0..1 do funil (ranking unificado)
  setores?: string[];
  matched_excerpts: MatchedExcerpt[];
  verdict?: MatchVerdict | null;             // veredito, chaveado por entity_id
  tipo?: string | null;
  caminho?: InnovationPath | null;
  explicacao?: PathExplanation | null;
}

export interface IctCapabilities {
  institution: string;
  municipio: string;
  competencias: string[];
  equipamentos: string[];
  condicoes_acesso: string;
  verificado_em: string;
}

export interface IctPartner {
  id: string;                 // native_id ("ict:labx")
  name: string;
  description: string;
  themes: string[];
  tipo: string;
  kind: "ict";
  source?: string;            // fonte (embrapii | pnipe)
  uf?: string;
  url?: string;               // canal de acesso/contato
  capacidades?: IctCapabilities;
  caminho?: InnovationPath | null;
  explicacao?: PathExplanation | null;
}

export interface GroundedWritingOpenResult {
  writing_session_id: string;
  project_id: string;
  path_id: string;
  artifact_type: string;
  outline: string[];
  requirements: string[];
  gaps: string[];
  context: Record<string, unknown>;
}

export const openGroundedWriting = (
  conversationId: string,
  pathId: string,
  artifactType = "proposta_tecnica",
) =>
  apiFetch<GroundedWritingOpenResult>("/writing/grounded/open", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: conversationId,
      path_id: pathId,
      artifact_type: artifactType,
    }),
  });

export const groundedWritingReview = (sessionId: string) =>
  apiFetch<Record<string, unknown>>(`/writing/grounded/${sessionId}/review`, {
    method: "POST",
  });

export const groundedWritingTurn = (
  sessionId: string,
  instruction: string,
  sectionHint?: string,
  idempotencyKey?: string,
) =>
  apiFetch<Record<string, unknown>>("/writing/grounded/turn", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      instruction,
      section_hint: sectionHint,
      idempotency_key: idempotencyKey,
    }),
  });

// ── Writing Turn Stream (Sprint 1 — C1) ─────────────────────

export interface WritingStreamCallbacks {
  onToken: (text: string) => void;
  onTool?: (name: string) => void;
  onDone: (payload: Record<string, unknown>) => void;
  onError: (message: string) => void;
}

export async function writingTurnStream(
  sessionId: string,
  message: string,
  sectionHint: string | null | undefined,
  libraryItemIds: string[],
  profile: Partial<CompanyProfile> | null,
  callbacks: WritingStreamCallbacks,
  signal?: AbortSignal,
  idempotencyKey?: string,
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const token = await getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const body: Record<string, unknown> = {
    session_id: sessionId,
    user_message: message,
    section_hint: sectionHint ?? null,
    profile: profile?.nome ? profile : null,
    library_item_ids: libraryItemIds,
  };
  if (idempotencyKey) body.idempotency_key = idempotencyKey;

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/writing/turn/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    throw new ApiError(
      "Falha de conexão com o servidor. Verifique sua internet e tente novamente.",
      0,
      undefined,
      "connection",
    );
  }
  if (!res.ok) throw await buildApiError(res);
  if (!res.body) throw new Error("Streaming não suportado neste navegador.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch {
      // Stream interrompido no meio (rede) — erro de conexão, não de geração.
      throw new ApiError(
        "A conexão com o servidor foi interrompida durante a geração.",
        0,
        undefined,
        "connection",
      );
    }
    const { done, value } = chunk;
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let eventName = "message";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
      }
      if (!dataLine) continue;

      let parsed: unknown;
      try {
        parsed = JSON.parse(dataLine);
      } catch {
        continue;
      }

      if (eventName === "token") {
        callbacks.onToken((parsed as { text: string }).text);
      } else if (eventName === "tool") {
        callbacks.onTool?.((parsed as { name: string }).name);
      } else if (eventName === "done") {
        callbacks.onDone(parsed as Record<string, unknown>);
        return;
      } else if (eventName === "error") {
        callbacks.onError((parsed as { message: string }).message);
        return;
      }
    }
  }
}

export type ModelTier = "fast" | "auto" | "pro";

export const sendWritingTurn = (
  sessionId: string,
  userMessage: string,
  sectionHint?: string,
  modelTier?: ModelTier,
  idempotencyKey?: string,
): Promise<WritingTurnResponse> => {
  const body: Record<string, unknown> = {
    session_id: sessionId,
    user_message: userMessage,
    section_hint: sectionHint,
    model_tier: modelTier,
  };
  if (idempotencyKey) body.idempotency_key = idempotencyKey;
  return apiFetch<WritingTurnResponse>("/writing/turn", {
    method: "POST",
    body: JSON.stringify(body),
  });
};

export const cancelWritingTurn = (sessionId: string) =>
  apiFetch<{ ok: boolean }>(`/writing/${sessionId}/cancel`, {
    method: "POST",
  });

export interface WritingGenerateResponse {
  session_id: string;
  sections_done: string[];
  failed_sections: string[];
  success: boolean;
  generation_critic_annotations?: Record<string, Record<string, unknown>>;
}

export const generateWritingProposal = (sessionId: string) =>
  apiFetch<WritingGenerateResponse>(`/writing/${sessionId}/generate`, {
    method: "POST",
    body: JSON.stringify({}),
  });

export interface GroundedWritingOpenResult {
  writing_session_id: string;
  project_id: string;
  path_id: string;
  artifact_type: string;
  outline: string[];
  requirements: string[];
  gaps: string[];
  context: Record<string, unknown>;
}

export const openGroundedWriting = (
  conversationId: string,
  pathId: string,
  artifactType = "proposta_tecnica",
) =>
  apiFetch<GroundedWritingOpenResult>("/writing/grounded/open", {
    method: "POST",
    body: JSON.stringify({
      conversation_id: conversationId,
      path_id: pathId,
      artifact_type: artifactType,
    }),
  });

export const groundedWritingReview = (sessionId: string) =>
  apiFetch<Record<string, unknown>>(`/writing/grounded/${sessionId}/review`, {
    method: "POST",
  });

export const groundedWritingTurn = (
  sessionId: string,
  instruction: string,
  sectionHint?: string,
  idempotencyKey?: string,
) =>
  apiFetch<Record<string, unknown>>("/writing/grounded/turn", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      instruction,
      section_hint: sectionHint,
      idempotency_key: idempotencyKey,
    }),
  });

export const startSectionChat = (sessionId: string, sectionTitle: string) =>
  apiFetch<SectionStartResponse>("/writing/section-start", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, section_title: sectionTitle }),
  });

export const extractProfileFromUrl = (url: string) =>
  apiFetch<ExtractProfileResponse>("/profile/extract", {
    method: "POST",
    body: JSON.stringify({ url }),
  });

// Extração de perfil a partir de PDF (onboarding). Rota pública (multipart):
// não setamos Content-Type — o browser define o boundary. Mandamos o token se
// houver, igual às demais chamadas semi-públicas. Padrão herdado de uploadLibraryPdf.
export const extractProfileFromDocument = async (
  file: File
): Promise<ExtractProfileResponse> => {
  const form = new FormData();
  form.append("file", file);
  const headers: Record<string, string> = {};
  const token = await getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE_URL}/profile/extract-from-document`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) throw await buildApiError(res);
  return res.json();
};

// Extração de perfil a partir de um item da biblioteca (autenticado).
export const extractProfileFromLibraryItem = (itemId: string, token: string) =>
  apiFetch<ExtractProfileResponse>(`/profile/extract-from-library/${itemId}`, {
    method: "POST",
  }, token);

// ── Content Library ────────────────────────────────────────

export const getLibraryItems = (
  token: string,
  type?: string,
  q?: string,
  includeArchived?: boolean
) => {
  const params = new URLSearchParams();
  if (type) params.set("type", type);
  if (q) params.set("q", q);
  if (includeArchived) params.set("include_archived", "true");
  const qs = params.toString();
  return apiFetch<ContentItemSummary[]>(`/library${qs ? `?${qs}` : ""}`, undefined, token);
};

export const archiveLibraryItem = (id: string, token: string) =>
  apiFetch<{ success: boolean }>(`/library/${id}/archive`, { method: "POST" }, token);

// Desarquiva (restaura) item soft-deleted. Wrapper do endpoint já existente no
// backend (POST /library/{id}/unarchive); usado pelo gerenciador "Arquivos"
// para que arquivar não vire ação irrecuperável pela UI.
export const unarchiveLibraryItem = (id: string, token: string) =>
  apiFetch<void>(`/library/${id}/unarchive`, { method: "POST" }, token);

export const getLibraryItem = (id: string, token: string) =>
  apiFetch<ContentItemFull>(`/library/${id}`, undefined, token);

export const createLibraryItem = (
  data: { title: string; type: string; content: string; tags: string[]; source_url?: string },
  token: string
) =>
  apiFetch<ContentItemFull>("/library", { method: "POST", body: JSON.stringify(data) }, token);

export const updateLibraryItem = (
  id: string,
  updates: Partial<{ title: string; type: string; content: string; tags: string[]; source_url: string }>,
  token: string,
) =>
  apiFetch<ContentItemFull>(`/library/${id}`, {
    method: "PUT",
    body: JSON.stringify(updates),
  }, token);

export const uploadLibraryPdf = async (
  file: File,
  meta: { title: string; type: string; tags: string },
  token: string
): Promise<ContentItemFull> => {
  const form = new FormData();
  form.append("file", file);
  form.append("title", meta.title);
  form.append("type", meta.type);
  form.append("tags", meta.tags);
  const res = await fetch(`${API_BASE_URL}/library/upload-pdf`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw await buildApiError(res);
  return res.json();
};

export const deleteLibraryItem = (id: string, token: string) =>
  apiFetch<void>(`/library/${id}`, { method: "DELETE" }, token);

// ── Writing Document (P1-A) ────────────────────────────────

export interface DocumentSection {
  title: string;
  content: string;
  citations?: unknown[] | null;
}

// GET /writing/{id}/document → outline (em ordem) + conteúdo por seção + o
// edital/alvo da sessão. É a fonte de verdade do documento que o workspace
// recarrega após cada turno (spec §9).
export interface WritingDocument {
  session_id: string;
  edital_id: string;
  sections: DocumentSection[];
  plan?: Record<string, unknown> | null;
  plan_pending?: boolean;
  writing_context?: {
    artifact_type?: string;
    requirements?: string[];
    gaps?: string[];
    source_refs?: Array<Record<string, unknown>>;
  } | null;
}

export const getWritingDocument = (sessionId: string) =>
  apiFetch<WritingDocument>(`/writing/${sessionId}/document`);

export const saveDocumentSection = (
  sessionId: string,
  sectionTitle: string,
  content: string
) =>
  apiFetch<{ success: boolean; section_title: string }>(`/writing/${sessionId}/section`, {
    method: "PUT",
    body: JSON.stringify({ session_id: sessionId, section_title: sectionTitle, content }),
  });

export const exportDocument = (sessionId: string) =>
  apiFetch<{ markdown: string; session_id: string }>(`/writing/${sessionId}/export`);

export const getChunkText = (chunkId: string) =>
  apiFetch<{ id: string; text: string; section: string }>(`/writing/chunks/${chunkId}`);

// ── Auto-review (checklist 3-passes, ancorado por seção) ───
// Shape REAL do core/services/checklist_service.py + enriquecimento `section`
// feito no router (backend/routers/writing.py:_attach_issue_sections).
export interface ComplianceIssue {
  requirement: string;
  status: "ok" | "missing" | "partial";
  evidence: string;
  suggestion: string;
  section: string; // anexado pelo backend (W6)
}

export interface QualityIssue {
  category: "clarity" | "coherence" | "persuasion" | "tone";
  severity: "low" | "medium" | "high";
  excerpt: string;
  suggestion: string;
  section: string;
}

export interface CompletenessSection {
  title: string;
  status: "empty" | "shallow" | "adequate" | "thorough";
  suggestion: string;
  section: string;
}

export interface AutoReview {
  compliance: { issues: ComplianceIssue[]; score: number };
  quality: { issues: QualityIssue[]; overall_score: number };
  completeness: {
    sections: CompletenessSection[];
    missing_sections: string[];
    overall_score: number;
  };
  error: Array<{ pass: string; message: string }> | null;
}

export const autoReviewChecklist = (sessionId: string, token: string) =>
  apiFetch<{ session_id: string; review: AutoReview }>(
    `/writing/${sessionId}/checklist/auto-review`,
    { method: "POST" },
    token,
  );

export interface WritingRefineResponse {
  section_updated: boolean;
  new_content: string | null;
  critic_feedback: Record<string, unknown> | null;
  options: string[];
  error: string | null;
}

export const refineSection = (
  sessionId: string,
  sectionTitle: string,
  instruction: string,
) =>
  apiFetch<WritingRefineResponse>(`/writing/${sessionId}/refine`, {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      section_title: sectionTitle,
      instruction,
    }),
  });

export const saveSessionToStorage = (sessionId: string, token: string) =>
  apiFetch<{ path: string; signed_url: string; session_id: string }>(
    `/writing/${sessionId}/save-to-storage`,
    { method: "POST" },
    token,
  );

// ── Writing Sessions list (resumable) ──────────────────────

export interface WritingSessionSummary {
  session_id: string;
  edital_id: string;
  edital_title?: string;
  // Rows anteriores à migration 020 podem não expor kind; nesse caso são
  // sessões de escrita por compatibilidade.
  kind?: "frontdoor" | "writing";
  status: "active" | "completed" | "abandoned";
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export const listWritingSessions = (token: string) =>
  apiFetch<{ sessions: WritingSessionSummary[] }>("/writing/sessions", undefined, token);

export const deleteWritingSession = (sessionId: string, token: string) =>
  apiFetch<{ ok: boolean }>(`/writing/sessions/${sessionId}`, { method: "DELETE" }, token);

// ── Conversations (lista unificada, spec chat-first fase 2) ─
// backend/routers/conversations.py — writing + frontdoor sobre as mesmas
// tabelas; o sidebar consome a lista e a home retoma frontdoor pelo detail.

export interface ConversationSummary {
  session_id: string;
  kind: "frontdoor" | "writing" | "consultant";
  title: string | null; // frontdoor; writing usa edital_title (server-side)
  edital_id: string | null;
  edital_title?: string | null; // resolvido server-side em batch para writing
  status: "active" | "completed" | "abandoned";
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationEntry {
  id: number; // session_turns.id
  turn_index: number;
  entry_kind: "msg" | "diff" | "radar" | "profile_incomplete";
  role: "user" | "assistant";
  content: string; // entry_kind=msg
  payload: Record<string, unknown> | null; // entry_kind=diff|radar
}

export interface ConversationDetail {
  session_id: string;
  kind: "frontdoor" | "writing" | "consultant";
  title: string | null;
  edital_id: string | null;
  status: "active" | "completed" | "abandoned";
  created_at: string;
  updated_at: string;
  entries: ConversationEntry[];
}

export const listConversations = (token: string) =>
  apiFetch<{ conversations: ConversationSummary[] }>("/conversations", undefined, token);

export const getConversation = (sessionId: string, token: string) =>
  apiFetch<ConversationDetail>(`/conversations/${sessionId}`, undefined, token);

// ── Applications (Pipeline) ────────────────────────────────

export type ApplicationStatus =
  | "matched"
  | "brief_gerado"
  | "proposta_iniciada"
  | "submetida"
  | "em_analise"
  | "aprovada"
  | "reprovada"
  | "desistiu";

export interface ApplicationItem {
  application_id: string;
  edital_id: string;
  edital_title: string | null;
  status: ApplicationStatus;
  match_score: number | null;
  deadline: string | null;
  days_left: number | null;
  session_id: string | null;
  progress_pct: number;
  updated_at: string;
}

export const listApplications = (token: string) =>
  apiFetch<ApplicationItem[]>("/applications", undefined, token);

export const updateApplicationStatus = (
  id: string,
  status: ApplicationStatus,
  token: string,
  feedback_notas?: string,
) =>
  apiFetch<{ success: boolean }>(`/applications/${id}/status`, {
    method: "PUT",
    body: JSON.stringify({ status, feedback_notas }),
  }, token);

// ── Research findings (Item 2, Fase B do deep_research) ────
// Staging area: findings do sub-agente de pesquisa chegam verified=false; o
// usuário promove à content_library o que quiser manter (gate humano).

export interface ResearchFindingSource {
  url: string;
  title: string;
}

export interface ResearchFinding {
  id: string;
  question: string;
  answer: string | null;
  sources: ResearchFindingSource[];
  query: string | null;
  verified: boolean;
  created_at: string;
  reviewed_at: string | null;
  promoted_to_library_id: string | null;
}

export const getResearchFindings = (token: string) =>
  apiFetch<{ findings: ResearchFinding[] }>(
    "/research-findings",
    undefined,
    token,
  );

export const promoteResearchFinding = (id: string, token: string) =>
  apiFetch<{ promoted: boolean; library_id: string }>(
    `/research-findings/${id}/promote`,
    { method: "POST" },
    token,
  );

// ── Relevance classification types ─────────────────────────

export type RelevanceStatus = "unclassified" | "classified" | "error";

export type RelevanceDecision = "in_scope" | "out_of_scope" | "needs_review";

export interface RelevanceEvidence {
  code: string;
  quote?: string | null;
  source?: string | null;
  locator?: { document?: string | null; page?: number | null } | null;
}

export interface RelevanceVerdict {
  decision: RelevanceDecision;
  reason_codes: string[];
  exclusion_codes: string[];
  evidence: RelevanceEvidence[];
  missing_information: string[];
  classifier_version: string;
}

// ── Discovered opportunities (torneira web, gate humano) ────

export interface DiscoveredOpportunity {
  id: string;
  url: string;
  title: string | null;
  agency: string | null;
  fonte: string | null;
  descricao: string | null;
  prazo_envio: string | null;
  publico_alvo: string | null;
  tema: string | null;
  opportunity_type: string | null;
  status: string;
  extraction_quality: "high" | "low" | null;
  edital_link: string | null;
  created_at: string;
  reviewed_at: string | null;
  promoted_web_source_id: string | null;
  relevance_status: RelevanceStatus;
  relevance_verdict: RelevanceVerdict | null;
  relevance_error: string | null;
  relevance_classified_at: string | null;
  promotion_run?: PromotionRun;
}

export interface PromotionRun {
  id: string;
  route: "web_source" | "direct_pdf" | "evidence_package";
  status: "awaiting_fetch" | "processing" | "ready" | "partial_failure" | "failed" | "queued";
  edital_id?: string | null;
  stages: Record<string, { status: string; updated_at?: string }>;
  updated_at: string;
}

export const getDiscoveredOpportunities = (token: string, includeReviewed?: boolean) =>
  apiFetch<{ opportunities: DiscoveredOpportunity[] }>(
    `/discovered-opportunities${includeReviewed ? "?include_reviewed=true" : ""}`,
    undefined,
    token,
  );

export const promoteDiscoveredOpportunity = (
  id: string,
  editalLink?: string | null,
  token?: string,
) =>
  apiFetch<{ promoted: boolean; url: string; web_source_id?: string; edital_processed?: { url_hash: string; n_chars: number } }>(
    `/discovered-opportunities/${id}/promote`,
    {
      method: "POST",
      body: JSON.stringify({ edital_link: editalLink || null }),
    },
    token,
  );

export const rejectDiscoveredOpportunity = (id: string, reason?: string, token?: string) =>
  apiFetch<{ rejected: boolean }>(
    `/discovered-opportunities/${id}/reject`,
    {
      method: "POST",
      body: JSON.stringify({ reason: reason || null }),
    },
    token,
  );

export const updateDiscoveredEditalLink = (id: string, editalLink: string, token: string) =>
  apiFetch<{ updated: boolean; edital_link: string }>(
    `/discovered-opportunities/${id}/edital-link`,
    {
      method: "PATCH",
      body: JSON.stringify({ edital_link: editalLink }),
    },
    token,
  );

export const retryDiscoveredPromotion = (id: string, stage: "fetch" | "silver" | "radar" | "rag", token: string) =>
  apiFetch<{ retried: boolean; stage: string; promotion_run_id: string }>(
    `/discovered-opportunities/${id}/promotion/retry`,
    { method: "POST", body: JSON.stringify({ stage }) },
    token,
  );

// ── Workspace multi-modo ──────────────────────────────────

export interface ModeSwitchResponse {
  mode: "explorer" | "escrita";
  response: string;
  welcome?: string | null;
  error?: string | null;
}

export const workspaceMode = (
  sessionId: string,
  mode: string,
  message?: string,
) =>
  apiFetch<ModeSwitchResponse>(`/workspace/${sessionId}/mode`, {
    method: "POST",
    body: JSON.stringify({
      mode,
      message: message ?? "",
    }),
  });

// ── Profile drift (Gap 4) ─────────────────────────────────

export interface ProfileDriftSignal {
  stale: boolean;
  days_since_update: number;
  new_items_since: number;
  profile_updated_at: string | null;
  recommendation: string | null;
}

export const getProfileDrift = (token: string) =>
  apiFetch<ProfileDriftSignal>("/me/profile/drift", undefined, token);

// ── Source Coverage ────────────────────────────────────

export type ChannelHealth =
  | "disabled"
  | "failing"
  | "degraded"
  | "stale"
  | "healthy"
  | "unknown";

export interface ChannelRunMetrics {
  last_attempt: string | null;
  last_success: string | null;
  total_records_observed: number | null;
  total_records_emitted: number | null;
  total_records_staged: number | null;
  yield_rate: number | null;
}

export interface EditorialFunnel {
  source_key: string;
  approved: number;
  rejected: number;
  pending: number;
  approval_rate: number | null;
  avg_review_hours: number | null;
}

export interface FamilyFunnel {
  family_key: string;
  approved: number;
  rejected: number;
  pending: number;
  approval_rate: number | null;
  avg_review_hours: number | null;
}

export interface CoverageGap {
  source_key: string | null;
  signal: string;
}

export interface EmergingDomain {
  domain: string;
  approval_count: number;
  first_approved_at: string;
  last_approved_at: string;
  candidate_for_dedicated_monitoring: boolean;
}

export interface ChannelHealthStatus {
  source_key: string;
  health: ChannelHealth;
}

export interface SourceCoverageResponse {
  generated_at: string;
  channels: ChannelHealthStatus[];
  runs: Record<string, ChannelRunMetrics>;
  channel_funnel: Record<string, EditorialFunnel>;
  family_funnel: Record<string, FamilyFunnel>;
  gaps: CoverageGap[];
  emerging_domains: EmergingDomain[];
  limitations: string[];
}

export const getSourceCoverage = (token: string) =>
  apiFetch<SourceCoverageResponse>("/source-coverage", undefined, token);

// ── Data Quality Exceptions (admin) ────────────────────────

export type DataQualitySubjectKind = "opportunity" | "investor" | "ict" | "program" | "agency";

export type DataQualityIssueCode =
  | "fact_conflict"
  | "critical_fact_missing"
  | "validation_failed"
  | "evidence_unresolved"
  | "temporal_status_without_basis"
  | "temporal_status_conflict";

export type DataQualityExceptionState = "open" | "resolved" | "superseded";

export type DataQualityReviewDecision =
  | "confirm"
  | "correct"
  | "mark_unknown"
  | "confirm_continuous";

export type DataQualityLocatorQuality = "exact" | "document_only" | "unresolved";

export interface DataQualityEvidenceRef {
  schema_version: 1;
  source: string;
  native_id?: string | null;
  edital_id?: string | null;
  document?: string | null;
  page?: number | null;
  block_idx?: number | null;
  section_path: string[];
  quote?: string | null;
  canonical_content_hash?: string | null;
  silver_source_hash?: string | null;
  bundle_hash?: string | null;
  content_hash?: string | null;
  collected_at?: string | null;
  locator_quality: DataQualityLocatorQuality;
}

export interface DataQualityReviewIn {
  review_id: string;
  decision: DataQualityReviewDecision;
  justification: string;
  corrected_value?: string | null;
  evidence_refs?: DataQualityEvidenceRef[];
}

export interface DataQualityReviewOut {
  review_id: string;
  decision: DataQualityReviewDecision;
  corrected_value?: string | null;
  reviewed_at: string;
  evidence_refs: DataQualityEvidenceRef[];
}

export interface DataQualityExceptionOut {
  id: string;
  subject_kind: DataQualitySubjectKind;
  subject_id: string;
  source?: string | null;
  field_path: string;
  issue_code: DataQualityIssueCode;
  safe_value?: string | null;
  evidence_refs: DataQualityEvidenceRef[];
  impact: string;
  state: DataQualityExceptionState;
  bundle_hash?: string | null;
  producer_version?: string | null;
  detected_at?: string | null;
  last_observed_at?: string | null;
  current_review?: DataQualityReviewOut | null;
}

export interface DataQualityExceptionListResponse {
  items: DataQualityExceptionOut[];
  limit: number;
  offset: number;
  has_more: boolean;
  next_offset?: number | null;
}

export interface DataQualityExceptionFilters {
  status?: DataQualityExceptionState;
  code?: DataQualityIssueCode;
  source?: string;
  field?: string;
  limit?: number;
  offset?: number;
}

export const getDataQualityExceptions = (
  token: string,
  filters?: DataQualityExceptionFilters,
) => {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.code) params.set("code", filters.code);
  if (filters?.source) params.set("source", filters.source);
  if (filters?.field) params.set("field", filters.field);
  if (typeof filters?.limit === "number") params.set("limit", String(filters.limit));
  if (typeof filters?.offset === "number") params.set("offset", String(filters.offset));
  const qs = params.toString();
  return apiFetch<DataQualityExceptionListResponse>(
    `/data-quality/exceptions${qs ? `?${qs}` : ""}`,
    undefined,
    token,
  );
};

export const getDataQualityException = (exceptionId: string, token: string) =>
  apiFetch<DataQualityExceptionOut>(
    `/data-quality/exceptions/${encodeURIComponent(exceptionId)}`,
    undefined,
    token,
  );

export const reviewDataQualityException = (
  exceptionId: string,
  body: DataQualityReviewIn,
  token: string,
) =>
  apiFetch<DataQualityExceptionOut>(
    `/data-quality/exceptions/${encodeURIComponent(exceptionId)}/reviews`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
    token,
  );
