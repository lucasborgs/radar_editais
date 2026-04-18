export type CompanySize = "MEI" | "ME" | "EPP" | "MEDIO" | "GRANDE";
export type TipoEntidade = "empresa" | "startup" | "universidade" | "ICT";
export type FaturamentoFaixa = "<500K" | "500K-5M" | "5M-50M" | ">50M";
export type TipoFinanciamento =
  | "subvencao_nao_reembolsavel"
  | "credito_reembolsavel"
  | "matching_embrapii"
  | "pesquisa_colaborativa";
export type UsoFinanciamento =
  | "P&D_interno"
  | "contratacao"
  | "equipamento"
  | "prototipagem"
  | "internacionalizacao"
  | "marketing";

export interface CompanyProfile {
  // Identificação
  nome: string;
  cnpj: string;
  tipo_entidade: TipoEntidade | "";
  // Descrição
  one_liner: string;
  problem_statement: string;
  solution_summary: string;
  descricao_atividades: string;
  portfolio_projetos: string;
  // Classificação
  tamanho_empresa: CompanySize | "";
  faturamento_anual_faixa: FaturamentoFaixa | "";
  localizacao: string;
  capital_social: number | null;
  certificacoes: string[];
  // Perfil tecnológico
  trl: number | null;
  equipe_resumo: string;
  // Intenção de financiamento
  tipos_financiamento_interesse: TipoFinanciamento[];
  uso_financiamento: UsoFinanciamento[];
  valor_buscado: number | null;
}

export const EMPTY_PROFILE: CompanyProfile = {
  nome: "",
  cnpj: "",
  tipo_entidade: "",
  one_liner: "",
  problem_statement: "",
  solution_summary: "",
  descricao_atividades: "",
  portfolio_projetos: "",
  tamanho_empresa: "",
  faturamento_anual_faixa: "",
  localizacao: "",
  capital_social: null,
  certificacoes: [],
  trl: null,
  equipe_resumo: "",
  tipos_financiamento_interesse: [],
  uso_financiamento: [],
  valor_buscado: null,
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
