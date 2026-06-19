"use client";

import { cn } from "@/lib/utils";

/**
 * Chips de sugestão do estado vazio (first-run) do front-door. Clicar num chip
 * envia a mensagem imediatamente (não só pré-preenche) — é o atalho de partida
 * para quem ainda não sabe o que perguntar. Copy em PT-BR, definida pelo caller.
 */
export function SuggestionChips({
  suggestions,
  onPick,
  disabled,
}: {
  suggestions: string[];
  onPick: (text: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {suggestions.map((s) => (
        <button
          key={s}
          type="button"
          disabled={disabled}
          onClick={() => onPick(s)}
          className={cn(
            "rounded-full border border-border bg-surface px-3.5 py-2 text-left text-sm font-sans",
            "text-content-primary transition-colors",
            "hover:border-primary hover:bg-primary/5",
            "disabled:cursor-not-allowed disabled:opacity-50"
          )}
        >
          {s}
        </button>
      ))}
    </div>
  );
}
