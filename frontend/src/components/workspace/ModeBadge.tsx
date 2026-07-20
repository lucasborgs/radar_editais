"use client";

import { cn } from "@/lib/utils";

type Mode = "explorer" | "escrita";

const MODE_LABELS: Record<Mode, string> = {
  explorer: "Contexto",
  escrita: "Escrita",
};

const MODE_STYLES: Record<Mode, string> = {
  explorer: "bg-blue-100 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-900",
  escrita: "bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300 border-green-200 dark:border-green-900",
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
