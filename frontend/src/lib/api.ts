import { API_BASE_URL } from "./constants";
import type { EditalEntry, EditalCard, KGMatchResult, DashboardStats } from "@/types/edital";
import type { CompanyProfile } from "@/types/profile";
import type { WritingStartResponse, WritingTurnResponse } from "@/types/api";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

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

export const sendWritingTurn = (sessionId: string, userMessage: string) =>
  apiFetch<WritingTurnResponse>("/writing/turn", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, user_message: userMessage }),
  });
