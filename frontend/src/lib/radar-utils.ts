import type { MatchedEdital, MatchedEntity } from "@/lib/api";

export type DeadlineUrgency = "closing" | "soon" | "future" | "continuous" | "confirm";
export type DeadlineFilter = "all" | "closing" | "soon" | "continuous";
export type EligibilityFilter = "all" | "elegivel" | "nao_verificada";
export type EditalOrder = "affinity" | "deadline";

export interface RadarFilters {
  setores: string[];
  eligibility: EligibilityFilter;
  deadline: DeadlineFilter;
  order: EditalOrder;
}

export const DEFAULT_RADAR_FILTERS: RadarFilters = {
  setores: [],
  eligibility: "all",
  deadline: "all",
  order: "affinity",
};

function saoPauloToday(now = new Date()): Date {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const value = (type: string) => Number(parts.find((part) => part.type === type)?.value);
  return new Date(Date.UTC(value("year"), value("month") - 1, value("day")));
}

export function parseDeadline(prazo: string | null): Date | null {
  if (!prazo) return null;
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(prazo);
  if (!match) return null;
  const [, day, month, year] = match;
  const parsed = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  return parsed.getUTCFullYear() === Number(year)
    && parsed.getUTCMonth() === Number(month) - 1
    && parsed.getUTCDate() === Number(day)
    ? parsed
    : null;
}

export function deadlineUrgency(prazo: string | null, now = new Date()): DeadlineUrgency {
  if (!prazo) return "continuous";
  const deadline = parseDeadline(prazo);
  if (!deadline) return "confirm";
  const days = Math.round((deadline.getTime() - saoPauloToday(now).getTime()) / 86_400_000);
  if (days < 0) return "confirm";
  if (days <= 7) return "closing";
  if (days <= 30) return "soon";
  return "future";
}

export function urgencyLabel(prazo: string | null, now = new Date()): string {
  const urgency = deadlineUrgency(prazo, now);
  if (urgency === "continuous") return "Fluxo contínuo ou prazo não informado";
  if (urgency === "confirm") return "Prazo a confirmar";
  const deadline = parseDeadline(prazo)!;
  const days = Math.round((deadline.getTime() - saoPauloToday(now).getTime()) / 86_400_000);
  if (days === 0) return "Encerra hoje";
  return urgency === "closing" ? `Encerra em ${days} dias` : `Prazo em ${days} dias`;
}

function matchesSetores(item: { setores?: string[] }, selected: string[]): boolean {
  return selected.length === 0 || (item.setores ?? []).some((setor) => selected.includes(setor));
}

export function filterEditais(editais: MatchedEdital[], filters: RadarFilters): MatchedEdital[] {
  return editais.filter((edital) => {
    const urgency = deadlineUrgency(edital.prazo);
    const eligible = filters.eligibility === "all" || edital.elegibilidade?.status === filters.eligibility;
    const deadline = filters.deadline === "all"
      || urgency === filters.deadline
      || (filters.deadline === "continuous" && urgency === "continuous");
    return matchesSetores(edital, filters.setores) && eligible && deadline;
  });
}

export function filterEntities(entities: MatchedEntity[], selectedSetores: string[]): MatchedEntity[] {
  return entities.filter((entity) => matchesSetores(entity, selectedSetores));
}

export function sortEditais(editais: MatchedEdital[], order: EditalOrder): MatchedEdital[] {
  if (order === "affinity") return editais;
  return [...editais].sort((a, b) => {
    const aDate = parseDeadline(a.prazo);
    const bDate = parseDeadline(b.prazo);
    if (aDate && bDate && aDate.getTime() !== bDate.getTime()) return aDate.getTime() - bDate.getTime();
    if (aDate && !bDate) return -1;
    if (!aDate && bDate) return 1;
    return b.affinity - a.affinity;
  });
}

export function availableSetores(...groups: Array<Array<{ setores?: string[] }>>): string[] {
  return Array.from(new Set(groups.flatMap((group) => group.flatMap((item) => item.setores ?? [])))).sort();
}
