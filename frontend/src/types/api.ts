// Tipos de resposta da API v2

export interface WritingMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface WritingStartResponse {
  session_id: string;
  edital_id: string;
  section_titles: string[];
  content_source: "section_index" | "live_fetch";
  success: boolean;
}

export interface WritingTurnResponse {
  session_id: string;
  assistant_message: string;
  sections_used: string[];
  turn_number: number;
  success: boolean;
  error?: string;
}

export interface SectionStartResponse {
  starter_message: string;
  section_title: string;
}

// Profile extraction (onboarding por URL)

export type FieldConfidence = "high" | "missing";

export interface ExtractProfileResponse {
  profile: import("./profile").CompanyProfile;
  confidence: Record<string, FieldConfidence>;
  source_title: string;
  low_confidence: boolean;
  error: string | null;
}
