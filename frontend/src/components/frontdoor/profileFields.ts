// Metadados de edição por campo do CompanyProfile, usados pelo card de diff em
// modo "✎ Editar" (D5): tipo do input + opções de select + coerção do valor
// digitado para o tipo certo no perfil. Centraliza o conhecimento que era
// implícito no formulário de onboarding/matching, agora editável inline.

import type { CompanyProfile } from "@/types/profile";
import { PORTE_LABELS } from "@/lib/constants";

export type FieldInputKind = "text" | "textarea" | "number" | "select" | "multiselect";

export interface FieldSpec {
  kind: FieldInputKind;
  options?: { value: string; label: string }[];
}

const UFS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
  "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
];

const FINANCIAMENTO_OPTIONS = [
  { value: "subvencao_nao_reembolsavel", label: "Subvenção (não reembolsável)" },
  { value: "pesquisa_colaborativa", label: "Pesquisa colaborativa" },
];

// Spec por campo. Campos sem entrada caem no default `text`.
export const FIELD_SPECS: Partial<Record<keyof CompanyProfile, FieldSpec>> = {
  nome: { kind: "text" },
  url_site: { kind: "text" },
  cnpj: { kind: "text" },
  tipo_entidade: {
    kind: "select",
    options: [
      { value: "empresa", label: "Empresa" },
      { value: "startup", label: "Startup" },
      { value: "universidade", label: "Universidade" },
      { value: "ICT", label: "ICT" },
    ],
  },
  one_liner: { kind: "textarea" },
  solution_summary: { kind: "textarea" },
  descricao_atividades: { kind: "textarea" },
  portfolio_projetos: { kind: "textarea" },
  equipe_resumo: { kind: "textarea" },
  cap_table_resumo: { kind: "textarea" },
  tracao_resumo: { kind: "textarea" },
  tamanho_empresa: {
    kind: "select",
    options: Object.entries(PORTE_LABELS).map(([value, label]) => ({ value, label })),
  },
  uf: { kind: "select", options: UFS.map((uf) => ({ value: uf, label: uf })) },
  trl: { kind: "number" },
  ano_fundacao: { kind: "number" },
  capital_social: { kind: "number" },
  faturamento_anual: { kind: "number" },
  mrr_arr: { kind: "number" },
  round_alvo_brl: { kind: "number" },
  estagio: {
    kind: "select",
    options: [
      { value: "pre-seed", label: "Pré-seed" },
      { value: "seed", label: "Seed" },
      { value: "serie-a", label: "Série A" },
      { value: "growth", label: "Growth" },
    ],
  },
  tipos_financiamento_interesse: { kind: "multiselect", options: FINANCIAMENTO_OPTIONS },
};

export function fieldSpec(field: keyof CompanyProfile): FieldSpec {
  return FIELD_SPECS[field] ?? { kind: "text" };
}

// Coage o valor cru de um input (string para text/select, string[] p/ multiselect)
// para o tipo armazenado no perfil. Vazio → null em campos numéricos.
export function coerceFieldValue(
  field: keyof CompanyProfile,
  raw: unknown,
): CompanyProfile[keyof CompanyProfile] {
  const spec = fieldSpec(field);
  if (spec.kind === "number") {
    if (raw === "" || raw === null || raw === undefined) return null as never;
    const n = Number(raw);
    return (Number.isFinite(n) ? n : null) as never;
  }
  if (spec.kind === "multiselect") {
    return (Array.isArray(raw) ? raw : []) as never;
  }
  return (raw ?? "") as never;
}

// Renderiza um valor de campo (do diff) para leitura humana (read-only do card).
export function renderFieldValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    if (value.length === 0) return "—";
    const opts = FINANCIAMENTO_OPTIONS;
    return value
      .map((v) => opts.find((o) => o.value === v)?.label ?? String(v))
      .join(", ");
  }
  return String(value);
}
