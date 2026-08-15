import type { ConsultantBriefUpdate } from "./api";

type ConsultantProjectConfirmation = {
  saveBrief: (updates: ConsultantBriefUpdate, expectedRevision: number) => Promise<{ revision: number }>;
  confirmProject: (expectedRevision: number) => Promise<void>;
};

export async function saveAndConfirmConsultantProject({
  updates,
  revision,
  confirmation,
}: {
  updates: ConsultantBriefUpdate;
  revision: number;
  confirmation: ConsultantProjectConfirmation;
}): Promise<void> {
  let nextRevision = revision;
  if (Object.keys(updates).length > 0) {
    const saved = await confirmation.saveBrief(updates, revision);
    nextRevision = saved.revision;
  }
  await confirmation.confirmProject(nextRevision);
}

export function artifactTypeForConsultantPath(path: { formal_instrument?: boolean }): string {
  return path.formal_instrument === false ? "abordagem_mercado" : "proposta_tecnica";
}
