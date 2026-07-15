"use client";

import { cn } from "@/lib/utils";

type Mode = "explorer" | "plan" | "escrita";

const MODE_LABELS: Record<Mode, string> = {
  explorer: "Contexto",
  plan: "Plano",
  escrita: "Escrita",
};

const MODE_STYLES: Record<Mode, string> = {
  explorer: "bg-blue-100 text-blue-800 border-blue-200",
  plan: "bg-amber-100 text-amber-800 border-amber-200",
  escrita: "bg-green-100 text-green-800 border-green-200",
};

export function ModeBadge({
  mode,
  className,
}: {
  mode: Mode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium font-sans leading-none",
        MODE_STYLES[mode],
        className,
      )}
      title={`Modo atual: ${mode}`}
    >
      {MODE_LABELS[mode]}
    </span>
  );
}
