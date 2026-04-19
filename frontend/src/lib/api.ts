import { API_BASE_URL } from "./constants";
import type { EditalEntry, EditalCard, KGMatchResult, DashboardStats } from "@/types/edital";
import type { CompanyProfile } from "@/types/profile";
import type {
  WritingStartResponse,
  WritingTurnResponse,
  SectionStartResponse,
  ExtractProfileResponse,
  ContentItemSummary,
  ContentItemFull,
} from "@/types/api";

async function apiFetch<T>(
  path: string,
  options?: RequestInit,
  token?: string
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers,
    ...options,
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

// ── Auth / Profile ─────────────────────────────────────────

export const getMe = (token: string) =>
  apiFetch<{ user_id: string; workspace_id: string; profile: Partial<CompanyProfile> }>(
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

// ── Matching ───────────────────────────────────────────────

export const getMatches = (profile: CompanyProfile, topK: number = 10) =>
  apiFetch<{ matches: KGMatchResult[] }>("/match", {
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

export const sendWritingTurn = (
  sessionId: string,
  userMessage: string,
  sectionHint?: string
) =>
  apiFetch<WritingTurnResponse>("/writing/turn", {
    method: "POST",
    body: JSON.stringify({
      session_id: sessionId,
      user_message: userMessage,
      section_hint: sectionHint,
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

// ── Content Library ────────────────────────────────────────

export const getLibraryItems = (token: string, type?: string, q?: string) => {
  const params = new URLSearchParams();
  if (type) params.set("type", type);
  if (q) params.set("q", q);
  const qs = params.toString();
  return apiFetch<ContentItemSummary[]>(`/library${qs ? `?${qs}` : ""}`, undefined, token);
};

export const getLibraryItem = (id: string, token: string) =>
  apiFetch<ContentItemFull>(`/library/${id}`, undefined, token);

export const createLibraryItem = (
  data: { title: string; type: string; content: string; tags: string[]; source_url?: string },
  token: string
) =>
  apiFetch<ContentItemFull>("/library", { method: "POST", body: JSON.stringify(data) }, token);

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
  if (!res.ok) throw new Error(`API error ${res.status}: /library/upload-pdf`);
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
