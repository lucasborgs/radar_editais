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

// Sprint 2 do Cenário B: agente pode pedir info ao usuário via tool dedicada.
// Quando presente, frontend renderiza prompt destacado em vez de [COMPLETAR: ...].
export interface PendingUserInput {
  field: string;   // kebab-case (cnpj, valor-contrapartida, etc.)
  prompt: string;  // pergunta em PT-BR
}

// Trace opcional das tools chamadas no turn (path agente apenas). Útil para
// debug / visualização de "como o agente pensou".
export interface ToolTraceEntry {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output: string;
}

export interface WritingTurnResponse {
  session_id: string;
  assistant_message: string;
  draft_content?: string | null;
  sections_used?: string[];
  turn_number: number;
  success: boolean;
  error?: string;
  error_type?: string;
  // Presentes APENAS quando workspace tem agent_writing_enabled=true.
  pending_user_input?: PendingUserInput | null;
  tool_trace?: ToolTraceEntry[];
  compliance_flags?: Array<Record<string, unknown>>;
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

export type EnrichmentStatus = "pending" | "processing" | "done" | "failed";

export interface ContentItemSummary {
  id: string;
  title: string;
  type: ContentItemType;
  tags: string[];
  summary: string;
  themes: string[];
  enrichment_status?: EnrichmentStatus;
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
