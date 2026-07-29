// Tipos legados ainda consumidos pelas telas de editais; o backend os projeta do gold SQL.

export type EditalStatus = "ABERTA" | "ENCERRADA" | "Desconhecido";

export interface ValueRange {
  min_brl: number | null;
  max_brl: number | null;
}

export interface TrlRange {
  min: number | null;
  max: number | null;
}

// Entrada mínima do endpoint de listagem de editais.
export interface EditalEntry {
  id: string;
  title: string;
  status: EditalStatus;
  deadline: string;
  pub_date: string;
  link: string;
  themes: string[];
  technologies: string[];
  programs: string[];
  publico_alvo: string[];
  fonte_recurso: string[];
  n_pdfs: number;
  n_facts: number;
}

// Card rico projetado pelo detail endpoint.
export interface EditalCard extends EditalEntry {
  objective: string | null;
  mechanism: "subvencao" | "reembolsavel" | "investimento" | "misto" | null;
  technologies: string[];
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

// ── Ficha unificada por Oportunidade (KG v2 / PR8) ──────────────────────────
// Payload de `GET /oportunidades/{id}` (`entity_catalog.get_opportunity`).
// Unidade = SEMPRE Oportunidade (D1): edital, programa ou investimento sob um
// tipo só. Campos curated-only (estagio_alvo/ticket_range/lead_follow) são
// opcionais. Substitui o EditalCard v1 (value_range/key_facts/link) na ficha.

export type OpportunityKind = "edital" | "programa" | "investimento" | string;

export interface TicketRange {
  min_brl: number | null;
  max_brl: number | null;
}

// Constraint de elegibilidade dura tipada (PR5). A avaliação sat/unsat é
// match-time; na ficha a lista crua vira chips com rótulo humano.
export interface EligibilityConstraint {
  tipo: string;                 // porte | sede_uf | faturamento | trl | forma_juridica | parceria
  op: string;                   // in | not_in | lte | gte | exige
  valor: unknown;               // list | number | string, depende do tipo/op
}

export interface OportunidadeDetail {
  id: string;
  kind: OpportunityKind;
  aperture: string;             // prazo | continua | recorrente | fechada | ""
  source: string;
  title: string;
  status: string;
  deadline: string;
  objective: string;
  mecanismo: string[];          // slugs
  mechanism: string;            // rótulo de display já montado
  macro_temas: string[];
  themes: string[];
  technologies: string[];
  programs: string[];
  publico_alvo: string[];
  eligible_entities: string[];
  key_requirements: string[];
  exclusoes: string[];
  constraints: EligibilityConstraint[];
  value: string | TicketRange | null;
  icts: string[];
  investidores: string[];
  fonte_recurso: string[];
  opportunity_type: string;
  official_url: string;
  document_urls: string[];
  collected_at: string;
  temporal_mode?: string | null;
  validity_state?: string | null;
  temporal_value?: string | null;
  decision_source?: string | null;
  last_verified_at?: string | null;
  // Curated-only (programa/investimento):
  estagio_alvo?: string[];
  ticket_range?: TicketRange | "";
  lead_follow?: string;
  // Provenance per field path (RT01-T11):
  provenance?: Record<string, FieldProvenance>;
}

// ── Provenance (RT01-T11) ──────────────────────────────────

export interface Citation {
  document: string;
  page?: number | null;
  quote?: string;
  source_url?: string;
  collected_at?: string;
}

export interface FieldProvenance {
  state: string;
  citations?: Citation[];
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

// Parceiro ICT sugerido para um edital (spec mechanism-scope-decisions, D3c).
// Produzido pela FRENTE A no payload do match; consumido como DISPLAY. ICT é
// sugestão no match, nunca compromisso (guard-rail project_ict_mapping).
export interface IctPartner {
  id: string;
  name: string;
  themes_match: string[];        // temas em comum com o edital/perfil
  brings_cofinancing: boolean;   // arranjo PODE aportar co-financiamento (possibilidade)
  url: string;
}

export interface KGMatchResult {
  id: string;
  title: string;
  opportunity_type?: string;   // edital | desafio | programa (default edital)
  verificacao?: string;        // verificado | provisorio (Descoberta sem revisão humana)
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
  // Contrato D2/D3c (FRENTE A produz, FRENTE B consome) — todos opcionais:
  mechanism?: string | null;            // mechanism do card; "investimento" → cluster capital de risco
  requires_ict_partner?: boolean;       // edital exige/beneficia parceiro ICT
  ict_partners?: IctPartner[];          // parceiros ICT sugeridos (display)
}

export interface DashboardStats {
  total_editais: number;
  last_updated: string;
  by_status: Record<string, number>;
  n_themes: number;
  n_fontes: number;
  n_programas: number;
  n_investidores: number;
  n_icts: number;
  total_oportunidades: number;
}
