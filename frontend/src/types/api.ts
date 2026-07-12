// Tipos de resposta da API v2

// ── Planning (FASE 1 do four-phase-workflow) ─────────────

export interface PlanSection {
  id: string;
  title: string;
  description: string;
  key_points: string[];
  estimated_length: string;
  pre_fill: string | null;
}

export interface PlanAlignment {
  company_themes: string[];
  edital_themes: string[];
  match_score: number;
  critical_gaps: string[];
}

export interface Plan {
  title: string;
  sections_total: number;
  sections: PlanSection[];
  alignment: PlanAlignment;
  compliance_hints: string[];
}

export interface WritingMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  draftSaved?: boolean;
}

export type WritingMode = "proposal" | "pitch";

export interface WritingStartResponse {
  session_id: string;
  edital_id: string;
  // W-D3: modo derivado do id (ou override) — front usa p/ o badge do header.
  mode?: WritingMode;
  section_titles: string[];
  content_source: "section_index" | "live_fetch";
  success: boolean;
  // get_info também devolve isto ao retomar uma sessão.
  pending_user_input?: PendingUserInput | null;
  turn_count?: number;
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
  // W-D1: título normalizado da seção persistida — presente só em entradas
  // save_draft bem-sucedidas. A co-edição do workspace usa p/ highlight + undo.
  saved_section?: string;
  // F3: veredito estruturado do critic (approved, issues, feedback) — presente
  // em entradas save_draft que passaram pelo critic.
  critic_result?: Record<string, unknown>;
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
  // Geração em lote: seções concluídas / falhas
  sections_done?: string[];
  failed_sections?: string[];
  // True quando um draft completo foi gerado neste turno
  draft_ready?: boolean;
  // PR6.2: turno cortado no teto de passos do agente — mostrar aviso discreto.
  truncated?: boolean;
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
  // Presente quando a listagem inclui arquivados (include_archived=true). String
  // ISO se soft-deleted, null/ausente se ativo. Usado pela página "Arquivos"
  // para distinguir, por linha, ativos de arquivados na mesma lista.
  archived_at?: string | null;
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
