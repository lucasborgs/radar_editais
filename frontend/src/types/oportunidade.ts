export interface OpportunityEntry {
  id: string;
  title: string;
  type: "edital" | "programa" | "investidor" | "ict";
  themes: string[];
  status?: string;
  deadline?: string;
  fonte_recurso?: string[];
  description?: string;
  aperture?: string;       // prazo | continua | recorrente | fechada
  macro_temas?: string[];  // macro-temas do edital (desambiguação)
  temporal_mode?: string | null;
  validity_state?: string | null;
  temporal_value?: string | null;
  decision_source?: string | null;
  last_verified_at?: string | null;
}
