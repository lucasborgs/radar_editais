// Tipos e helpers compartilhados do workspace de escrita (1b).
// O workspace é DB-backed: a fonte de verdade do documento é
// GET /writing/{id}/document (spec §9). Estes tipos modelam o estado de UI
// derivado dele (co-edição, highlight, undo).

import type { WritingMode } from "@/types/api";
import type { AutoReview } from "@/lib/api";

// ── Findings do auto-review, normalizados para a UI (N3) ────────────────────
// Cada finding já carrega a seção a que pertence ("Geral" = sem âncora). Os 3
// passes do checklist_service viram um shape único para ancorar no editor.
export type FindingKind = "compliance" | "quality" | "completeness";

export interface Finding {
  kind: FindingKind;
  section: string; // título da seção do outline ou "Geral"
  text: string; // descrição legível do issue
  suggestion: string; // ação sugerida (entra no prompt "corrigir com IA")
  // Rótulo curto de severidade/status para o badge (ex.: "alta", "ausente").
  badge?: string;
}

// Achata o AutoReview nos findings acionáveis. Issues "ok"/"thorough"/"adequate"
// não viram findings (nada a corrigir).
export function flattenReview(review: AutoReview): Finding[] {
  const out: Finding[] = [];

  for (const i of review.compliance.issues ?? []) {
    if (i.status === "ok") continue;
    out.push({
      kind: "compliance",
      section: i.section || "Geral",
      text:
        i.status === "missing"
          ? `Requisito ausente: ${i.requirement}`
          : `Requisito parcial: ${i.requirement}`,
      suggestion: i.suggestion || "",
      badge: i.status === "missing" ? "ausente" : "parcial",
    });
  }

  for (const i of review.quality.issues ?? []) {
    out.push({
      kind: "quality",
      section: i.section || "Geral",
      text: i.excerpt ? `"${i.excerpt}"` : `Problema de ${i.category}`,
      suggestion: i.suggestion || "",
      badge: severityLabel(i.severity),
    });
  }

  for (const s of review.completeness.sections ?? []) {
    if (s.status === "adequate" || s.status === "thorough") continue;
    out.push({
      kind: "completeness",
      section: s.section || s.title || "Geral",
      text: s.status === "empty" ? "Seção vazia ou a preencher" : "Seção rasa — pouco desenvolvida",
      suggestion: s.suggestion || "",
      badge: s.status === "empty" ? "vazia" : "rasa",
    });
  }

  return out;
}

function severityLabel(sev: string): string {
  if (sev === "high") return "alta";
  if (sev === "medium") return "média";
  return "baixa";
}

// Agrupa findings por seção, preservando "Geral" para o bloco do topo.
export function groupBySection(findings: Finding[]): Map<string, Finding[]> {
  const m = new Map<string, Finding[]>();
  for (const f of findings) {
    const arr = m.get(f.section) ?? [];
    arr.push(f);
    m.set(f.section, arr);
  }
  return m;
}

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
  // PR6.2: resposta interrompida no limite de passos do agente (aviso discreto).
  truncated?: boolean;
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
