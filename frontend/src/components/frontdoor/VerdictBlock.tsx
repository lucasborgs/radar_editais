"use client";

import { cn } from "@/lib/utils";
import type { MatchVerdict } from "@/lib/api";

// Veredito LLM (Estágio 2, KG v2 PR7). Recomendação = prioridade de leitura.
// Componente compartilhado pelo card do radar (MatchedEditalCard) e pela ficha
// (/oportunidades/[id]).
export const RECO_CONFIG = {
  alta: { label: "Prioridade alta", className: "bg-emerald-500/10 text-emerald-600" },
  media: { label: "Prioridade média", className: "bg-amber-500/10 text-amber-600" },
  baixa: { label: "Prioridade baixa", className: "bg-zinc-500/10 text-zinc-500" },
} as const;

export function VerdictBlock({ verdict }: { verdict: MatchVerdict }) {
  const reco = RECO_CONFIG[verdict.recomendacao] ?? RECO_CONFIG.media;
  return (
    <div className="mt-2 pt-2 border-t border-border space-y-1.5">
      <span
        className={cn(
          "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
          reco.className,
        )}
      >
        {reco.label}
      </span>
      <p className="text-xs text-content-secondary leading-snug">
        {verdict.racional_afinidade}
      </p>
      {verdict.fit_mecanismo && (
        <p className="text-[11px] text-content-tertiary leading-snug">
          {verdict.fit_mecanismo}
        </p>
      )}
      {verdict.red_flags_elegibilidade.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {verdict.red_flags_elegibilidade.map((flag, i) => (
            <span
              key={i}
              className="inline-flex items-center rounded-full bg-red-500/8 px-2 py-0.5 text-[11px] text-red-600"
            >
              ⚠ {flag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
