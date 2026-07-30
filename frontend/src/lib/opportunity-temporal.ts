import { parseDeadline } from "@/lib/utils";

export interface TemporalConsumerPayload {
  status?: string | null;
  deadline?: string | null;
  prazo?: string | null;
  temporal_mode?: string | null;
  validity_state?: string | null;
  temporal_value?: string | null;
  decision_source?: string | null;
  last_verified_at?: string | null;
}

export type TemporalBadgeTone = "active" | "closed" | "review";

function normalize(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function rawDeadline(payload: TemporalConsumerPayload): string | null {
  const value = payload.deadline ?? payload.prazo ?? null;
  return value && value.trim() ? value.trim() : null;
}

function formatIsoDate(value: string | null | undefined): string | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function formatVerificationDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(parsed);
}

export function isContinuousConfirmed(payload: TemporalConsumerPayload): boolean {
  return normalize(payload.validity_state) === "active"
    && normalize(payload.temporal_mode) === "continuous";
}

export function temporalBadge(payload: TemporalConsumerPayload): {
  label: string;
  tone: TemporalBadgeTone;
} {
  const state = normalize(payload.validity_state);
  if (state === "active") return { label: "Aberta", tone: "active" };
  if (state === "closed") return { label: "Encerrada", tone: "closed" };
  if (state === "needs_review") {
    // Confia no status scraped quando a validade temporal é incerta —
    // a maioria dos editais FAPESP não expõe deadline no site.
    if (normalize(payload.status) === "aberta") return { label: "Aberta", tone: "review" };
    return { label: "Validade a confirmar", tone: "review" };
  }
  return normalize(payload.status) === "encerrada"
    ? { label: "Encerrada", tone: "closed" }
    : { label: "Validade a confirmar", tone: "review" };
}

export function temporalDeadlineText(payload: TemporalConsumerPayload): string | null {
  const state = normalize(payload.validity_state);
  if (state === "needs_review") return null;
  if (isContinuousConfirmed(payload)) return "Fluxo contínuo";

  const fixed = formatIsoDate(payload.temporal_value);
  if (fixed) return fixed;

  const fallback = rawDeadline(payload);
  if (!fallback) return null;
  return parseDeadline(fallback) ?? fallback;
}

export function temporalVerificationNote(payload: TemporalConsumerPayload): string | null {
  const source = normalize(payload.decision_source);
  const date = formatVerificationDate(payload.last_verified_at);

  const sourceLabel = source === "human_review"
    ? "Revisão humana"
    : source === "source"
      ? "Fonte monitorada"
      : source === "legacy"
        ? "Catálogo legado"
        : "";

  if (sourceLabel && date) return `${sourceLabel} em ${date}`;
  if (sourceLabel) return sourceLabel;
  if (date) return `Verificado em ${date}`;
  return null;
}
