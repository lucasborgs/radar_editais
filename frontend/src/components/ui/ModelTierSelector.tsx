"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { getCommands, type ModelTier, type ModelTierInfo } from "@/lib/api";

const FALLBACK_TIERS: ModelTierInfo[] = [
  { tier: "fast", model: "", label: "Rápido", use_case: "Tarefas comuns, baixa latência" },
  { tier: "auto", model: "", label: "Automático", use_case: "Default — equilíbrio entre velocidade e qualidade" },
  { tier: "pro",  model: "", label: "Pro",       use_case: "Tarefas de alto valor (drafts críticos, brief, review)" },
];

interface Props {
  value: ModelTier;
  onChange: (tier: ModelTier) => void;
  disabled?: boolean;
  className?: string;
}

export function ModelTierSelector({ value, onChange, disabled, className }: Props) {
  const [tiers, setTiers] = useState<ModelTierInfo[]>(FALLBACK_TIERS);

  useEffect(() => {
    let cancelled = false;
    getCommands()
      .then((res) => {
        if (!cancelled && res.model_tiers?.length) setTiers(res.model_tiers);
      })
      .catch(() => {
        // Mantém fallback — UI continua funcional mesmo sem /commands
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div
      className={cn(
        "inline-flex items-center rounded-lg border border-border bg-surface p-0.5",
        disabled && "opacity-50 pointer-events-none",
        className
      )}
      role="radiogroup"
      aria-label="Modelo de LLM"
    >
      {tiers.map((t) => {
        const active = t.tier === value;
        return (
          <button
            key={t.tier}
            type="button"
            role="radio"
            aria-checked={active}
            title={`${t.label} — ${t.use_case}${t.model ? ` (${t.model})` : ""}`}
            onClick={() => onChange(t.tier)}
            className={cn(
              "px-2.5 py-1 text-xs font-sans rounded-md transition-colors",
              active
                ? "bg-primary text-white font-semibold"
                : "text-content-secondary hover:text-content-primary hover:bg-content-secondary/5"
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
