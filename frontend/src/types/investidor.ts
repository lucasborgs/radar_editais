// Resultado do match de investidor (Q3) — POST /match/investidores.
// Entidade, não evento: sem status/deadline. Dois modos (tese vs generalista).

export interface InvestorMatchDimensions {
  tese?: string;
  setor?: string;
  estagio?: string;
}

export interface InvestorMatch {
  id: string;
  name: string;
  score: number;
  generalista: boolean;
  match_dimensions: InvestorMatchDimensions;
  justificativa: string;
  // enriquecidos do diretório (não vêm do LLM)
  site?: string | null;
  lead_follow?: string | null;
  fund_status?: string | null;
  estagio_alvo?: string[];
}
