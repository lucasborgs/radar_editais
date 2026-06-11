"use client";

import { cn } from "@/lib/utils";
import type { OpportunityBrief, BriefRecommendation } from "@/lib/api";

// Render inline do Brief GO/NO-GO (core/opportunity_brief_service). Não há
// componente reutilizável de brief no app hoje (o pipeline só mostra o veredito
// agregado), então este é o render canônico do front-door para o shape completo.

const REC_LABEL: Record<BriefRecommendation, string> = {
  GO: "GO",
  GO_COM_RESSALVAS: "GO com ressalvas",
  NO_GO: "NO-GO",
};

const REC_CLASS: Record<BriefRecommendation, string> = {
  GO: "bg-[#1DB954]/15 text-[#169c46]",
  GO_COM_RESSALVAS: "bg-amber-100 text-amber-700",
  NO_GO: "bg-red-100 text-red-700",
};

const DIM_LABELS: Record<string, string> = {
  elegibilidade: "Elegibilidade",
  tematico: "Temático",
  trl: "TRL",
  mecanismo: "Mecanismo",
  contrapartida: "Contrapartida",
};

function NarrativeList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-content-secondary">
        {title}
      </p>
      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-content-primary">
        {items.map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

export function BriefView({ brief }: { brief: OpportunityBrief }) {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-app-bg/50 p-3 text-sm font-sans">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "rounded-full px-2.5 py-0.5 text-xs font-semibold",
            REC_CLASS[brief.recommendation],
          )}
        >
          {REC_LABEL[brief.recommendation]}
        </span>
        <span className="text-xs text-content-secondary tabular-nums">
          score {brief.total_score}/100
        </span>
      </div>

      {brief.decision_matrix.length > 0 && (
        <div className="space-y-1">
          {brief.decision_matrix.map((row) => {
            const pct = row.max > 0 ? Math.round((row.score / row.max) * 100) : 0;
            const color = pct >= 80 ? "#1DB954" : pct >= 50 ? "#f59e0b" : "#ef4444";
            return (
              <div key={row.dimension} className="flex items-center gap-2">
                <span className="w-24 shrink-0 text-[11px] text-content-secondary">
                  {DIM_LABELS[row.dimension] ?? row.dimension}
                </span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${pct}%`, backgroundColor: color }}
                  />
                </div>
                <span className="w-10 shrink-0 text-right text-[11px] tabular-nums text-content-secondary">
                  {row.score}/{row.max}
                </span>
              </div>
            );
          })}
        </div>
      )}

      <div className="space-y-2">
        <NarrativeList title="Forças" items={brief.narrative.strengths} />
        <NarrativeList title="Riscos" items={brief.narrative.risks} />
        <NarrativeList title="Próximos passos" items={brief.narrative.next_steps} />
      </div>
    </div>
  );
}
