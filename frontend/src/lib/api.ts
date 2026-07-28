import { API_BASE_URL } from "./constants";
import { createSupabaseClient } from "./supabase";
import type { EditalEntry, EditalCard, OportunidadeDetail, DashboardStats } from "@/types/edital";
import type { OpportunityEntry } from "@/types/oportunidade";
import type { CompanyProfile } from "@/types/profile";
import type {
  WritingStartResponse,
  WritingTurnResponse,
  WritingMode,
  SectionStartResponse,
  ExtractProfileResponse,
  ContentItemSummary,
  ContentItemFull,
  Plan,
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

export interface KGChatMessage {
  role: "user" | "assistant";
  content: string;
}

// ── Explore (turno conversacional exploratório) ────────────
// POST /explore (backend/routers/explore.py): resposta sobre a base +
// proposta de diff de perfil. O front aplica o diff só após aceite (D4).
// Autenticado: persiste a conversa (kind='frontdoor') e devolve session_id.

export interface ProfileDiffItem {
  field: keyof CompanyProfile;
  label: string;              // já em PT-BR (vem do backend)
  old: unknown;
  new: unknown;
}

// Ids das entradas persistidas num turno logado via
// persist_frontdoor_turn. `diff` é o que o front usa (PATCH no aceite/descarte).
export interface FrontdoorEntryIds {
  user: number | null;
  assistant: number | null;
  diff?: number | null;
}

// Par de trechos reais que gerou o match (motor v3, Stage 2) — a explicação
// do card: trecho da empresa ↔ trecho do edital/tese, com o cosseno do par.
export interface MatchedExcerpt {
  company_text: string;
  edital_text: string;
  section?: string | null;
  score: number;
}

// Elegibilidade dura (Stage 1 do funil v3). `inelegivel` nunca chega ao
// front (é filtrado antes); sobram `elegivel` e `nao_verificada` (perfil
// incompleto → o card mostra "elegibilidade não verificada" + o que completar).
export interface Elegibilidade {
  status: "elegivel" | "nao_verificada" | "inelegivel";
  unsat: string[];
  unknown: string[];
}

// Veredito LLM do match (Estágio 3 do funil v3). Nullable: o card renderiza sem
// ele e o recebe quando a task terminar (poll via fetchMatchVerdicts). Anônimo
// nunca recebe veredito (o cache é por workspace).
export interface MatchVerdict {
  racional_afinidade: string;
  red_flags_elegibilidade: string[];
  fit_mecanismo: string;
  recomendacao: "alta" | "media" | "baixa";
}

export interface MatchedEdital {
  kind: "edital";
  source: string;
  edital_id: string;
  entity_id: string;          // native_id ("finep:589")
  name: string;
  description: string;
  score: number;              // melhor par (cosseno 0..1) — ring
  affinity: number;           // média dos máximos (0..1) — ranking
  setores: string[];
  matched_excerpts: MatchedExcerpt[];
  status: string | null;
  prazo: string | null;
  valor: string | null;
  url?: string | null;
  elegibilidade?: Elegibilidade | null;
  verdict?: MatchVerdict | null;
}

// Facetas do card de investidor (site/ticket/estágio da tese).
export interface InvestmentOffer {
  offer_name: string;
  official_url: string;
  estagio_alvo: string[];
  ticket_range: { min_brl: number | null; max_brl: number | null } | null;
}

export interface MatchedEntity {
  kind: "investidor" | "programa";
  entity_id: string;          // native_id ("investidor:kptl" / "programa:centelha")
  name: string;
  description: string | null;
  score: number;
  affinity: number;           // mesma escala 0..1 do funil (ranking unificado)
  setores?: string[];
  matched_excerpts: MatchedExcerpt[];
  estagio_alvo?: string[];
  offer?: InvestmentOffer;                   // só investidor
  verificado?: boolean;
  verdict?: MatchVerdict | null;             // veredito, chaveado por entity_id
}

export interface RadarMatchesResponse {
  matched_editais: MatchedEdital[];
  matched_programas: MatchedEntity[];
  matched_investidores: MatchedEntity[];
  meta: {
    ranking: "affinity";
    uses_workspace_chunks: boolean;
  };
}

export const getRadarMatches = (profile: CompanyProfile) =>
  apiFetch<RadarMatchesResponse>("/radar/matches", {
    method: "POST",
    body: JSON.stringify({ profile }),
  });

// Payload de fim de turno — mesmo shape em `POST /explore` (JSON único) e no
// frame `done` de `POST /explore/stream` (SSE, item 1/TASK 4): o backend
// gera os dois a partir do mesmo `_post_process`, então um único tipo cobre
// ambos os transportes.
export interface ExploreTurnResult {
  answer: string;
  truncated?: boolean; // PR6.2: resposta cortada no teto de passos do agente
  profile_diff: ProfileDiffItem[] | null;
  matched_editais?: MatchedEdital[];
  matched_entities?: MatchedEntity[];
  session_id?: string;
  entry_ids?: FrontdoorEntryIds;
  next_action?: {
    offer: string;
    options: Array<{ label: string; action: string }>;
  }; // PR1 (4-phase): oferta de planejamento
}

export const frontdoorTurn = (
  message: string,
  history: KGChatMessage[],
  profile: Partial<CompanyProfile> | null,
  sessionId?: string | null,
) =>
  apiFetch<ExploreTurnResult>("/explore", {
    method: "POST",
    body: JSON.stringify({
      message,
      history,
      profile,
      session_id: sessionId ?? null,
    }),
  });

// ── Explore streaming (SSE) — item 1, TASK 4 ────────────────
// Variação de `frontdoorTurn` que consome `POST /explore/stream`. Callbacks
// em vez de um retorno único: `onToken` chega ao vivo, `onDone`/`onError` são
// TERMINAIS (mutuamente exclusivos — nunca os dois no mesmo turno; um `error`
// NUNCA é seguido de `done`, contrato do endpoint). Não passa por `apiFetch`
// (que faz `res.json()` de corpo único) — lê o stream via `getReader()`.
export interface ExploreStreamCallbacks {
  onToken: (text: string) => void;
  onTool?: (name: string) => void;
  onDone: (payload: ExploreTurnResult) => void;
  onError: (message: string) => void;
}

export async function exploreStream(
  message: string,
  history: KGChatMessage[],
  profile: Partial<CompanyProfile> | null,
  sessionId: string | null | undefined,
  callbacks: ExploreStreamCallbacks,
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const token = await getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE_URL}/explore/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ message, history, profile, session_id: sessionId ?? null }),
  });
  if (!res.ok) throw await buildApiError(res);
  if (!res.body) throw new Error("Streaming não suportado neste navegador.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames SSE terminam em uma linha em branco (\n\n). Um frame pode
    // chegar fatiado entre dois reads — só processa o que já está completo,
    // mantém o resto no buffer pro próximo chunk.
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
        callbacks.onDone(parsed as ExploreTurnResult);
        return;
      } else if (eventName === "error") {
        // TERMINAL (contrato do endpoint, backend/routers/explore.py): nunca
        // há um "done" depois. Lança pra quem chamou tratar como falha de
        // turno — igual ao catch de `frontdoorTurn` hoje, sem esperar mais nada.
        callbacks.onError((parsed as { message: string }).message);
        return;
      }
    }
  }
}

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
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const token = await getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE_URL}/writing/turn/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      session_id: sessionId,
      user_message: message,
      section_hint: sectionHint ?? null,
      profile: profile?.nome ? profile : null,
      library_item_ids: libraryItemIds,
    }),
    signal,
  });
  if (!res.ok) throw await buildApiError(res);
  if (!res.body) throw new Error("Streaming não suportado neste navegador.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
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

// Poll cache-only dos vereditos (Estágio 2): a chave é o file_key do hipergrado
// (`${source}__${edital_id}`). Auth obrigatória — anônimo não tem workspace.
export const fetchMatchVerdicts = (oportunidadeIds: string[]) =>
  apiFetch<{ verdicts: Record<string, MatchVerdict | null> }>("/match/verdicts", {
    method: "POST",
    body: JSON.stringify({ oportunidade_ids: oportunidadeIds }),
  });

// ── Planning (FASE 1 do four-phase-workflow) ───────────────

export const planningGenerate = (
  question: string,
  analysis: string,
  editalId?: string | null,
) =>
  apiFetch<Plan>("/planning/generate", {
    method: "POST",
    body: JSON.stringify({
      question,
      analysis,
      edital_id: editalId ?? null,
    }),
  });

export const getExistingPlan = (sessionId: string) =>
  apiFetch<Plan>(`/planning/${sessionId}`);

export const planningAdjust = (sessionId: string, instruction: string) =>
  apiFetch<Plan>(`/planning/${sessionId}/adjust`, {
    method: "POST",
    body: JSON.stringify({ instruction }),
  });

// ── Writing Session ────────────────────────────────────────

export const startWritingSession = (
  editalId: string,
  profile: CompanyProfile,
  mode?: WritingMode,
  plan?: Plan | null,
) =>
  apiFetch<WritingStartResponse>("/writing/start", {
    method: "POST",
    body: JSON.stringify({
      edital_id: editalId,
      profile,
      mode,
      plan: plan ?? undefined,
    }),
  });

export type ModelTier = "fast" | "auto" | "pro";

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

export interface DocumentSection { title: string; content: string }

// GET /writing/{id}/document → outline (em ordem) + conteúdo por seção + o
// edital/alvo da sessão. É a fonte de verdade do documento que o workspace
// recarrega após cada turno (spec §9).
export interface WritingDocument {
  session_id: string;
  edital_id: string;
  sections: DocumentSection[];
  plan?: Record<string, unknown> | null;
  plan_pending?: boolean;
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
  kind: "frontdoor" | "writing";
  title: string | null; // frontdoor; writing deriva do edital no front
  edital_id: string | null;
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
  kind: "frontdoor" | "writing";
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

export const appendConversationEntry = (
  sessionId: string,
  body: { entry_kind: "radar" | "diff"; payload: Record<string, unknown> },
  token: string,
) =>
  apiFetch<ConversationEntry>(
    `/conversations/${sessionId}/entries`,
    { method: "POST", body: JSON.stringify(body) },
    token,
  );

export const updateConversationEntry = (
  sessionId: string,
  entryId: number,
  payload: Record<string, unknown>,
  token: string,
) =>
  apiFetch<ConversationEntry>(
    `/conversations/${sessionId}/entries/${entryId}`,
    { method: "PATCH", body: JSON.stringify({ payload }) },
    token,
  );

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
