// Tipos alinhados com a API v2 (FINEP-only, KG-based)

export type EditalStatus = "ABERTA" | "ENCERRADA" | "Desconhecido";

export interface ValueRange {
  min_brl: number | null;
  max_brl: number | null;
}

export interface TrlRange {
  min: number | null;
  max: number | null;
}

// Entrada mínima do index.json (list endpoint)
export interface EditalEntry {
  id: string;
  title: string;
  status: EditalStatus;
  deadline: string;
  pub_date: string;
  link: string;
  themes: string[];
  publico_alvo: string[];
  fonte_recurso: string[];
  n_pdfs: number;
  n_facts: number;
}

// Card rico (knowledge_graph/cards/{id}.json) — detail endpoint
export interface EditalCard extends EditalEntry {
  objective: string | null;
  mechanism: "subvencao" | "reembolsavel" | "investimento" | "misto" | null;
  eligible_entities: string[];
  eligible_sectors: string[];
  value_range: ValueRange;
  trl_range: TrlRange;
  required_certifications: string[];
  counterpart_required: boolean;
  key_requirements: string[];
  key_facts: string[];
  generated_at: string;
  source: "facts+metadata" | "metadata_only";
}

// Resultado do matching híbrido
export interface DimScore {
  score: number;
  max: number;
}

export interface MatchDimensions {
  elegibilidade?: DimScore;
  tematico?: DimScore;
  trl?: DimScore;
  mecanismo?: DimScore;
  contrapartida?: DimScore;
}

export interface KGMatchResult {
  id: string;
  title: string;
  score: number;
  score_deterministic: number;
  score_tematico: number;
  status: EditalStatus;
  deadline: string;
  match_dimensions: MatchDimensions;
  dimensoes_semanticas?: Record<string, string>;
  justificativa: string;
  key_requirements?: string[];
  objective?: string;
}

export interface DashboardStats {
  total_editais: number;
  last_updated: string;
  by_status: Record<string, number>;
  n_themes: number;
  n_fontes: number;
}
