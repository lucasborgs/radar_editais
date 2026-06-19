"use client";

import { cn } from "@/lib/utils";
import { MetricCard } from "@/components/ui/MetricCard";
import type { ApplicationItem } from "@/lib/api";

interface PipelineBriefingProps {
  applications: ApplicationItem[];
}

/** Greeting based on local hour — coworker-style briefing tone. */
function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Bom dia";
  if (h < 18) return "Boa tarde";
  return "Boa noite";
}

export function PipelineBriefing({ applications }: PipelineBriefingProps) {
  const total = applications.length;

  const urgent = applications.filter(
    (a) => a.days_left != null && a.days_left >= 0 && a.days_left < 7,
  ).length;

  const writing = applications.filter(
    (a) => a.status === "proposta_iniciada",
  ).length;

  // Soonest non-expired deadline.
  const upcoming = applications
    .filter((a) => a.days_left != null && a.days_left >= 0)
    .sort((a, b) => (a.days_left ?? 0) - (b.days_left ?? 0))[0];

  const upcomingValue = upcoming
    ? `${upcoming.days_left}d`
    : "—";
  const upcomingSubtext = upcoming
    ? upcoming.edital_title || upcoming.edital_id
    : "Sem prazos próximos";

  return (
    <section className="rounded-xl border border-border bg-surface shadow-card p-5 space-y-4">
      <div>
        <h2 className="font-heading text-lg font-bold text-content-primary">
          {greeting()} — seu pipeline hoje
        </h2>
        <p className="text-sm text-content-secondary font-sans mt-0.5">
          {total === 0
            ? "Nada em andamento por enquanto."
            : urgent > 0
              ? `Você tem ${urgent} ${urgent === 1 ? "prazo urgente" : "prazos urgentes"} esta semana.`
              : "Tudo sob controle — nenhum prazo urgente esta semana."}
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard label="No pipeline" value={total} subtext="propostas" />
        <MetricCard
          label="Urgentes"
          value={urgent}
          subtext="prazo < 7 dias"
          className={cn(urgent > 0 && "ring-1 ring-red-200")}
        />
        <MetricCard label="Em escrita" value={writing} subtext="propostas iniciadas" />
        <MetricCard
          label="Próximo prazo"
          value={upcomingValue}
          subtext={upcomingSubtext}
        />
      </div>
    </section>
  );
}
