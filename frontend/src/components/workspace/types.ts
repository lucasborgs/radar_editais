// Tipos e helpers compartilhados do workspace de escrita (1b).
// O workspace é DB-backed: a fonte de verdade do documento é
// GET /writing/{id}/document (spec §9). Estes tipos modelam o estado de UI
// derivado dele (co-edição, highlight, undo).

import type { WritingMode } from "@/types/api";

// Mensagem do chat do workspace. Espelha WritingMessage da 1a + chips de
// co-edição (seções tocadas pelo agente neste turno, com snapshot p/ undo).
export interface WorkspaceMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  // N2: seções persistidas pelo agente neste turno (via tool_trace.saved_section).
  // Cada uma vira um chip "§ {título} atualizada · ↶ desfazer".
  editedSections?: string[];
  // Avisos de compliance (âmbar) anexados ao turno.
  complianceFlags?: Array<Record<string, unknown>>;
}

// Uma seção no documento contínuo. `content` vem do backend; os demais campos
// são estado de UI local (co-edição).
export interface WorkspaceSection {
  title: string;
  content: string;
}

// Âncora estável por seção — usada para rolar o editor até a seção a partir do
// explorer e para destacar seções tocadas. Slug determinístico do título.
export function sectionAnchorId(title: string): string {
  return (
    "ws-section-" +
    title
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
  );
}

export function modeLabel(mode: WritingMode | undefined): string {
  return mode === "pitch" ? "Pitch" : "Proposta";
}

// Deriva o modo a partir do id do alvo quando o backend não o expõe (ex.: ao
// retomar uma sessão só com o /document). `investidor:<slug>` → pitch.
export function modeFromEditalId(editalId: string): WritingMode {
  return editalId.startsWith("investidor:") ? "pitch" : "proposal";
}

// Quantas seções têm conteúdo (para "4/7 seções" no header).
export function filledCount(sections: WorkspaceSection[]): number {
  return sections.filter((s) => s.content.trim().length > 0).length;
}
