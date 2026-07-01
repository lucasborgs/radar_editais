export interface OpportunityEntry {
  id: string;
  title: string;
  type: "edital" | "programa" | "investidor";
  themes: string[];
  status?: string;
  deadline?: string;
  fonte_recurso?: string[];
  description?: string;
}
