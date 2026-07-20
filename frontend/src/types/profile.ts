export type CompanySize = "MEI" | "ME" | "EPP" | "MEDIO" | "GRANDE";
export type TipoEntidade = "empresa" | "startup" | "universidade" | "ICT";
export type TipoFinanciamento =
  | "subvencao_nao_reembolsavel"
  | "pesquisa_colaborativa"
  | "capital_risco";

export interface CompanyProfile {
  // Identificação
  nome: string;
  cnpj: string;
  url_site: string;
  tipo_entidade: TipoEntidade | "";
  // Descrição
  one_liner: string;
  solution_summary: string;
  descricao_atividades: string;
  portfolio_projetos: string;
  // Estilo de escrita — craft, só o Redator vê (não entra em matching)
  estilo_escrita: string;
  // Classificação
  tamanho_empresa: CompanySize | "";
  capital_social: number | null;
  // Elegibilidade dura (teto do matching) — região/idade/faturamento
  uf: string;
  faturamento_anual: number | null;
  ano_fundacao: number | null;
  // Perfil tecnológico
  trl: number | null;
  equipe_resumo: string;
  // Intenção de financiamento
  tipos_financiamento_interesse: TipoFinanciamento[];
  // Capital privado / desafios (Q3/Q4) — opcionais, alimentam o match de investidor
  estagio: EstagioInvestimento | "";
  mrr_arr: number | null;
  round_alvo_brl: number | null;
  cap_table_resumo: string;
  tracao_resumo: string;
}

export type EstagioInvestimento = "pre-seed" | "seed" | "serie-a" | "growth";

export const EMPTY_PROFILE: CompanyProfile = {
  nome: "",
  cnpj: "",
  url_site: "",
  tipo_entidade: "",
  one_liner: "",
  solution_summary: "",
  descricao_atividades: "",
  portfolio_projetos: "",
  estilo_escrita: "",
  tamanho_empresa: "",
  capital_social: null,
  uf: "",
  faturamento_anual: null,
  ano_fundacao: null,
  trl: null,
  equipe_resumo: "",
  tipos_financiamento_interesse: [],
  estagio: "",
  mrr_arr: null,
  round_alvo_brl: null,
  cap_table_resumo: "",
  tracao_resumo: "",
};

export const PROFILE_STORAGE_KEY = "radar_company_profile";

export function loadProfileFromStorage(): CompanyProfile | null {
  if (typeof window === "undefined") return null;
  try {
    const saved = localStorage.getItem(PROFILE_STORAGE_KEY);
    if (!saved) return null;
    const parsed = JSON.parse(saved) as Partial<CompanyProfile>;
    return parsed.nome ? { ...EMPTY_PROFILE, ...parsed } : null;
  } catch {
    return null;
  }
}

export function saveProfileToStorage(profile: CompanyProfile): void {
  try {
    localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profile));
  } catch {
    // ignore
  }
}
