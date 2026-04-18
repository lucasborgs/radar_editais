export type SectionStatus = "pending" | "draft" | "reviewed";

export interface ProposalSection {
  title: string;
  status: SectionStatus;
  isGeneral?: boolean;
}

const DEFAULT_SECTIONS = [
  "Contextualização / Problema",
  "Objetivos",
  "Metodologia / Plano de trabalho",
  "Equipe executora",
  "Cronograma",
  "Orçamento",
  "Resultados esperados / Impacto",
];

const SECTION_KEYWORDS: Record<string, string[]> = {
  "Contextualização / Problema": ["contexto", "problem", "justif", "introdução"],
  "Objetivos": ["objetivo", "meta", "finalidade"],
  "Metodologia / Plano de trabalho": ["metodolog", "plano", "atividade", "trabalho"],
  "Equipe executora": ["equipe", "pesquisador", "coordenador", "executor"],
  "Cronograma": ["cronograma", "prazo", "etapa", "fase"],
  "Orçamento": ["orçamento", "custo", "recurso", "financeiro", "valor"],
  "Resultados esperados / Impacto": ["resultado", "impacto", "entregável", "produto"],
};

function matchSection(editalTitle: string): string | null {
  const lower = editalTitle.toLowerCase();
  for (const [canonical, keywords] of Object.entries(SECTION_KEYWORDS)) {
    if (keywords.some((kw) => lower.includes(kw))) return canonical;
  }
  return null;
}

export function getProposalSections(editalSectionTitles: string[]): ProposalSection[] {
  const used = new Set<string>();
  const sections: ProposalSection[] = [];

  // Tenta mapear seções do edital para seções canônicas
  for (const title of editalSectionTitles) {
    const canonical = matchSection(title);
    if (canonical && !used.has(canonical)) {
      used.add(canonical);
      sections.push({ title: canonical, status: "pending" });
    }
  }

  // Adiciona seções padrão não cobertas
  for (const def of DEFAULT_SECTIONS) {
    if (!used.has(def)) {
      sections.push({ title: def, status: "pending" });
    }
  }

  // Seção geral sempre ao final
  sections.push({ title: "Geral", status: "pending", isGeneral: true });

  return sections;
}

export function completionStats(sections: ProposalSection[]) {
  const real = sections.filter((s) => !s.isGeneral);
  const done = real.filter((s) => s.status !== "pending").length;
  return { done, total: real.length };
}

export function buildFullProposal(
  sections: ProposalSection[],
  drafts: Record<string, string>
): string {
  return sections
    .filter((s) => !s.isGeneral && s.status !== "pending" && drafts[s.title])
    .map((s) => `## ${s.title}\n\n${drafts[s.title]}`)
    .join("\n\n---\n\n");
}
