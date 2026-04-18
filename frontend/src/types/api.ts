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
