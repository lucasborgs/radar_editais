import type { EditalStatus } from "@/types/edital";

/** Colour associated with each data source */
export const SOURCE_COLORS: Record<string, string> = {
  FINEP: "#e07b39",
  FAPESP: "#4f86c6",
  BNDES: "#2e7d32",
};

/** Status badge visual config */
export const STATUS_CONFIG: Record<
  EditalStatus,
  { label: string; className: string }
> = {
  ABERTA: {
    label: "Aberto",
    className: "bg-[#1DB954]/15 text-[#169c46]",
  },
  ENCERRADA: {
    label: "Encerrado",
    className: "bg-gray-500/15 text-gray-600",
  },
  Desconhecido: {
    label: "Desconhecido",
    className: "bg-blue-500/15 text-blue-700",
  },
};

/** Score color for KG match results (0-10 scale) */
export function scoreColor(score: number): string {
  if (score >= 7.5) return "#1DB954";
  if (score >= 5) return "#f59e0b";
  if (score >= 3) return "#f97316";
  return "#ef4444";
}

/** Company size labels */
export const PORTE_LABELS: Record<string, string> = {
  MEI: "MEI",
  ME: "Microempresa (ME)",
  EPP: "Pequeno Porte (EPP)",
  MEDIO: "Médio Porte",
  GRANDE: "Grande Empresa",
};

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
