"use client";

/**
 * PendingUserInputPrompt — banner destacado renderizado quando o agente
 * (Sprint 2 do Cenário B) usa a tool `request_user_info` para pedir uma
 * informação concreta ao usuário (CNPJ, valor, nome, etc).
 *
 * Substitui a convenção [COMPLETAR: ...] inline no texto por sinal estruturado:
 * o backend devolve `pending_user_input: {field, prompt}` no response do turn
 * (ou em get_info ao retomar sessão) e o frontend renderiza esse componente
 * em destaque acima do composer.
 *
 * UX: o usuário pode (a) responder direto no input embutido — vira o
 * próximo turn — ou (b) ignorar e mandar uma mensagem qualquer; em ambos os
 * casos o backend limpa pending_user_input ao receber o próximo turn.
 */

import { useState } from "react";
import type { PendingUserInput } from "@/types/api";
import { cn } from "@/lib/utils";

interface PendingUserInputPromptProps {
  pending: PendingUserInput;
  onAnswer: (answer: string) => void;
  disabled?: boolean;
}

export function PendingUserInputPrompt({
  pending,
  onAnswer,
  disabled = false,
}: PendingUserInputPromptProps) {
  const [draft, setDraft] = useState("");

  function submit() {
    const trimmed = draft.trim();
    if (!trimmed || disabled) return;
    onAnswer(trimmed);
    setDraft("");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div
      className={cn(
        "rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30",
        "px-4 py-3 mb-3 shadow-sm",
      )}
      role="region"
      aria-label="Pergunta do agente"
    >
      <div className="flex items-start gap-2">
        <span
          className="text-amber-600 text-xs font-semibold uppercase tracking-wide font-sans shrink-0 mt-0.5"
          aria-hidden="true"
        >
          ?
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-amber-800 dark:text-amber-300 font-sans uppercase tracking-wide mb-1">
            O agente precisa de uma informação
          </p>
          <p className="text-sm text-content-primary font-sans leading-snug mb-2">
            {pending.prompt}
          </p>
          <p className="text-[10px] text-amber-700 dark:text-amber-300/80 font-data uppercase tracking-wide mb-2">
            campo: {pending.field}
          </p>

          <div className="flex gap-2">
            <input
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              disabled={disabled}
              placeholder="Sua resposta..."
              className={cn(
                "flex-1 rounded-md border border-amber-300 dark:border-amber-800 bg-surface",
                "px-3 py-1.5 text-sm font-sans",
                "focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-amber-400",
                "disabled:opacity-50",
              )}
              aria-label={`Resposta para ${pending.field}`}
            />
            <button
              type="button"
              onClick={submit}
              disabled={disabled || !draft.trim()}
              className={cn(
                "rounded-md bg-amber-600 text-white text-sm font-sans font-medium",
                "px-3 py-1.5 hover:bg-amber-700 transition-colors",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
            >
              Responder
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
