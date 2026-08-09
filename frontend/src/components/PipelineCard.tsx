"use client";

import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import type { ApplicationItem, ApplicationStatus } from "@/lib/api";

const STATUS_ORDER: ApplicationStatus[] = [
  "matched",
  "brief_gerado",
  "proposta_iniciada",
  "submetida",
  "em_analise",
  "aprovada",
  "reprovada",
  "desistiu",
];

const STATUS_LABEL: Record<ApplicationStatus, string> = {
  matched: "Match",
  brief_gerado: "Brief",
  proposta_iniciada: "Em escrita",
  submetida: "Submetida",
  em_analise: "Em análise",
  aprovada: "Aprovada",
  reprovada: "Reprovada",
  desistiu: "Desistiu",
};

interface PipelineCardProps {
  app: ApplicationItem;
  onStatusChange: (id: string, status: ApplicationStatus) => void;
  busy?: boolean;
}

function DeadlineChip({ daysLeft }: { daysLeft: number | null }) {
  if (daysLeft == null) {
    return (
      <span className="text-xs text-content-secondary font-sans">—</span>
    );
  }
  const urgent = daysLeft < 7;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full font-sans",
        urgent
          ? "bg-red-500/15 text-red-700 dark:text-red-300"
          : "bg-content-secondary/15 text-content-secondary",
      )}
    >
      ⏰ {daysLeft < 0 ? `${Math.abs(daysLeft)}d atrás` : `${daysLeft}d`}
    </span>
  );
}

export function PipelineCard({ app, onStatusChange, busy }: PipelineCardProps) {
  const router = useRouter();
  const title = app.edital_title || app.edital_id;
  const clickable = !!app.session_id;

  const openSession = () => {
    if (!app.session_id) return;
    router.push(`/workspace/${app.session_id}`);
  };

  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface shadow-card p-3 space-y-2.5 transition-colors",
        clickable && "cursor-pointer hover:border-primary/40",
        busy && "opacity-60",
      )}
      onClick={clickable ? openSession : undefined}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter") openSession();
            }
          : undefined
      }
    >
      <p className="text-sm font-semibold text-content-primary font-sans leading-snug line-clamp-2">
        {title}
      </p>

      <div className="flex items-center justify-between gap-2">
        {app.match_score != null ? (
          <span className="font-data text-xs text-content-secondary tabular-nums">
            fit {Math.round(app.match_score)}
          </span>
        ) : (
          <span className="text-xs text-content-secondary font-sans">—</span>
        )}
        <DeadlineChip daysLeft={app.days_left} />
      </div>

      {app.session_id && (
        <div className="h-1.5 w-full rounded-full bg-content-secondary/10 overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-all"
            style={{ width: `${Math.min(100, Math.max(0, app.progress_pct))}%` }}
          />
        </div>
      )}

      {/* Status dropdown — para "mover" o card sem drag-and-drop. */}
      <select
        value={app.status}
        disabled={busy}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) =>
          onStatusChange(app.application_id, e.target.value as ApplicationStatus)
        }
        className={cn(
          "w-full text-[11px] font-sans rounded-lg border border-border bg-app-bg",
          "px-2 py-1 text-content-secondary focus:outline-none focus:border-primary/50",
          busy && "cursor-not-allowed",
        )}
      >
        {STATUS_ORDER.map((s) => (
          <option key={s} value={s}>
            Mover para: {STATUS_LABEL[s]}
          </option>
        ))}
      </select>
    </div>
  );
}

export { STATUS_ORDER, STATUS_LABEL };
