import { API_BASE_URL } from "./constants";
import { createSupabaseClient } from "./supabase";
import type { EditalEntry, EditalCard, KGMatchResult, DashboardStats } from "@/types/edital";
import type { InvestorMatch } from "@/types/investidor";
import type { CompanyProfile } from "@/types/profile";
import type {
  WritingStartResponse,
  WritingTurnResponse,
  SectionStartResponse,
  ExtractProfileResponse,
  ContentItemSummary,
  ContentItemFull,
} from "@/types/api";

// Recupera o JWT corrente da sessão Supabase (lazy). Existir aqui evita
// que cada função de API tenha que receber e propagar o token manualmente —
// problema antigo onde algumas funções passavam (getMe, saveProfile) e
// outras não (getMatches, startWritingSession, …), causando 401 em massa.
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
export class ApiError extends Error {
  status: number;
  requestId?: string;
  constructor(message: string, status: number, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
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
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers,
    ...options,
  });
  if (!res.ok) throw await buildApiError(res);
  return res.json();
}

// ── Auth / Profile ─────────────────────────────────────────

export const getMe = (token: string) =>
  apiFetch<{
    user_id: string;
    workspace_id: string;
    profile: Partial<CompanyProfile>;
    preferences?: UserPreferences;
    contribute_to_global_weights?: boolean;
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

export const getDashboardStats = () =>
  apiFetch<DashboardStats>("/stats");

// ── Knowledge Graph (público — Dashboard) ──────────────────

export type GraphNodeType =
  | "edital"
  | "tema"
  | "publico"
  | "subprograma"
  | "home"
  | "outro";

export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  edital_id?: string;
  status?: string;
}

export interface GraphLink {
  source: string;
  target: string;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

export interface KGChatMessage {
  role: "user" | "assistant";
  content: string;
}

export const getGraph = () => apiFetch<GraphData>("/graph");

export const kgExplore = (
  message: string,
  history: KGChatMessage[],
  editalIds: string[] = [],
  nodeId?: string,
  nodeType?: string,
) =>
  apiFetch<{ answer: string }>("/kg-explore", {
    method: "POST",
    body: JSON.stringify({
      message,
      history,
      edital_ids: editalIds,
      node_id: nodeId,
      node_type: nodeType,
    }),
  });

// ── Matching ───────────────────────────────────────────────

export const getMatches = (profile: CompanyProfile, topK: number = 10) =>
  apiFetch<{ matches: KGMatchResult[] }>("/match", {
    method: "POST",
    body: JSON.stringify({ profile, top_k: topK }),
  });

// ── Radar unificado (L2) ───────────────────────────────────
// Shape espelha core/services/radar_service.py::merge_radar: cada item carrega
// os campos comuns + `payload` cru do matcher de origem (KGMatchResult p/ evento,
// InvestorMatch p/ entidade). O front lê os campos comuns p/ o card e desce ao
// payload só na expansão (justificativa, dimensões, etc.). Rota PÚBLICA (B2):
// sem JWT usa pesos globais; com JWT usa pesos do workspace.

export type RadarKindClass = "evento" | "entidade";
export type RadarOpportunityType = "edital" | "desafio" | "programa" | "investidor";
export type RadarTier = "forte" | "fraco";

export interface RadarItem {
  id: string;
  kind_class: RadarKindClass;
  opportunity_type: RadarOpportunityType;
  verificacao: string;        // "verificado" | "provisorio"
  title: string;
  score: number;
  why_now: string;            // sinal "por que agora" (countdown / tese)
  tier: RadarTier;            // "fraco" = rebaixado (aproximado/abaixo do floor)
  rank: number;
  rrf: number;
  // payload cru do matcher de origem — tipado por kind_class no consumo.
  payload: KGMatchResult | InvestorMatch;
}

export interface RadarResponse {
  radar: RadarItem[];
  counts: Record<string, number>;
}

export const getRadar = (
  profile: CompanyProfile,
  topK: number = 10,
  perTypeCap?: number | null,
) =>
  apiFetch<RadarResponse>("/match/radar", {
    method: "POST",
    body: JSON.stringify({ profile, top_k: topK, per_type_cap: perTypeCap ?? null }),
  });

// ── Front-door (turno conversacional, público) ─────────────
// POST /frontdoor/turn (backend/routers/frontdoor.py): resposta sobre a base +
// proposta de diff de perfil. O front aplica o diff só após aceite (D4).

export interface ProfileDiffItem {
  field: keyof CompanyProfile;
  label: string;              // já em PT-BR (vem do backend)
  old: unknown;
  new: unknown;
}

export const frontdoorTurn = (
  message: string,
  history: KGChatMessage[],
  profile: Partial<CompanyProfile> | null,
) =>
  apiFetch<{ answer: string; profile_diff: ProfileDiffItem[] | null }>(
    "/frontdoor/turn",
    {
      method: "POST",
      body: JSON.stringify({ message, history, profile }),
    },
  );

// ── Opportunity Brief (GO/NO-GO, logado) ───────────────────
// Shape espelha core/opportunity_brief_service.py::generate_brief.

export type BriefRecommendation = "GO" | "GO_COM_RESSALVAS" | "NO_GO";

export interface BriefDecisionRow {
  dimension: string;
  score: number;
  max: number;
}

export interface OpportunityBrief {
  application_id: string;
  edital_id: string;
  recommendation: BriefRecommendation;
  total_score: number;        // 0-100
  decision_matrix: BriefDecisionRow[];
  narrative: { strengths: string[]; risks: string[]; next_steps: string[] };
  error?: string;
}

export const getOpportunityBrief = (
  editalId: string,
  profile: CompanyProfile | null,
  modelTier?: ModelTier,
) =>
  apiFetch<OpportunityBrief>("/opportunity/brief", {
    method: "POST",
    body: JSON.stringify({
      edital_id: editalId,
      profile: profile ?? null,
      model_tier: modelTier ?? null,
    }),
  });

// Investidores (Q3) — entidade, sem gate, dois modos (tese vs estágio).
export const getInvestorMatches = (profile: CompanyProfile, topK: number = 6) =>
  apiFetch<{ matches: InvestorMatch[] }>("/match/investidores", {
    method: "POST",
    body: JSON.stringify({ profile, top_k: topK }),
  });

// ── Writing Session ────────────────────────────────────────

export const startWritingSession = (
  editalId: string,
  profile: CompanyProfile
) =>
  apiFetch<WritingStartResponse>("/writing/start", {
    method: "POST",
    body: JSON.stringify({ edital_id: editalId, profile }),
  });

export type ModelTier = "fast" | "auto" | "pro";

export interface ModelTierInfo {
  tier: ModelTier;
  model: string;
  label: string;
  use_case: string;
}

export interface CommandsResponse {
  commands: Array<{ name: string; description: string; endpoint: string }>;
  model_tiers: ModelTierInfo[];
}

export const getCommands = () => apiFetch<CommandsResponse>("/commands");

export const sendWritingTurn = (
  sessionId: string,
  userMessage: string,
  sectionHint?: string,
  modelTier?: ModelTier
) =>
  apiFetch<WritingTurnResponse>("/writing/turn", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      user_message: userMessage,
      section_hint: sectionHint,
      model_tier: modelTier,
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

export interface DocumentSection { title: string; content: string }

export const getWritingDocument = (sessionId: string) =>
  apiFetch<{ session_id: string; sections: DocumentSection[] }>(`/writing/${sessionId}/document`);

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

// ── Writing Sessions list (resumable) ──────────────────────

export interface WritingSessionSummary {
  session_id: string;
  edital_id: string;
  edital_title?: string;
  status: "active" | "completed" | "abandoned";
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export const listWritingSessions = (token: string) =>
  apiFetch<{ sessions: WritingSessionSummary[] }>("/writing/sessions", undefined, token);

export const deleteWritingSession = (sessionId: string, token: string) =>
  apiFetch<{ ok: boolean }>(`/writing/sessions/${sessionId}`, { method: "DELETE" }, token);

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

// ── User preferences ───────────────────────────────────────

export interface UserPreferences {
  contribute_to_global_weights: boolean;
}

export const getMyPreferences = (token: string) =>
  apiFetch<UserPreferences>("/me/preferences", undefined, token);

export const updateMyPreferences = (prefs: Partial<UserPreferences>, token: string) =>
  apiFetch<UserPreferences>("/me/preferences", {
    method: "PUT",
    body: JSON.stringify(prefs),
  }, token);

// ── Matching weights & reflection suggestions (Gap 2) ─────

export type WeightDimension =
  | "elegibilidade"
  | "tematico"
  | "trl"
  | "mecanismo"
  | "contrapartida";

export interface WorkspaceWeight {
  dimension: WeightDimension;
  weight: number;
  source: "manual" | "reflection" | "global_aggregated";
  scope: "workspace" | "global";
  approved_from_insight_id?: string;
  approved_at?: string;
  updated_at?: string;
}

export type SuggestionStatus = "pending" | "approved" | "superseded";

export interface PendingSuggestion {
  dimension: WeightDimension;
  delta: number;
  rationale: string;
  status: SuggestionStatus;
}

export interface PendingInsight {
  insight_id: string;
  insight: string;
  confidence: "low" | "medium" | "high";
  created_at: string;
  suggestions: PendingSuggestion[];
}

export const getWorkspaceWeights = (token: string) =>
  apiFetch<{ weights: WorkspaceWeight[] }>("/me/weights", undefined, token);

export const getPendingWeightSuggestions = (token: string) =>
  apiFetch<{ insights: PendingInsight[] }>("/me/weights/pending", undefined, token);

export const approveWeightSuggestions = (
  insightId: string,
  suggestions: Array<{ dimension: WeightDimension; delta: number }>,
  token: string,
) =>
  apiFetch<{ applied: Array<{ dimension: string; weight: number }> }>(
    "/me/weights/approve",
    {
      method: "POST",
      body: JSON.stringify({ insight_id: insightId, suggestions }),
    },
    token,
  );

export const revertWorkspaceWeight = (dimension: WeightDimension, token: string) =>
  apiFetch<{ success: boolean; dimension: string }>(
    `/me/weights/${dimension}`,
    { method: "DELETE" },
    token,
  );

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
