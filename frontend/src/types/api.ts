// Tipos de resposta da API v2

export interface WritingMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  draftSaved?: boolean;
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
  draft_content?: string | null;
  sections_used: string[];
  turn_number: number;
  success: boolean;
  error?: string;
}

export interface SectionStartResponse {
  starter_message: string;
  section_title: string;
}

// Content Library

export type ContentItemType =
  | "proposal"
  | "project_description"
  | "team_bio"
  | "technical_doc"
  | "other";

export interface ContentItemSummary {
  id: string;
  title: string;
  type: ContentItemType;
  tags: string[];
  summary: string;
  themes: string[];
  created_at: string;
  updated_at: string;
}

export interface ContentItemFull extends ContentItemSummary {
  content: string;
  key_facts: string[];
  source_url: string | null;
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
