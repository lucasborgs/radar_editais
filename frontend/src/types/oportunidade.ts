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
}
