export type SectionStatus = "pending" | "draft" | "reviewed";

export interface ProposalSection {
  title: string;
  status: SectionStatus;
  isGeneral?: boolean;
}

// Outline padrão usado como fallback se a sessão não retornar títulos
const DEFAULT_OUTLINE = [
  "1. Identificação da empresa",
  "2. Objeto do projeto",
  "3. Justificativa e relevância",
  "4. Objetivos",
  "5. Metodologia e plano de trabalho",
  "6. Equipe técnica",
  "7. Cronograma",
  "8. Orçamento",
];

export function getProposalSections(outlineTitles: string[]): ProposalSection[] {
  const titles = outlineTitles.length > 0 ? outlineTitles : DEFAULT_OUTLINE;
  const sections: ProposalSection[] = titles.map((title) => ({
    title,
    status: "pending",
  }));
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
