"use client";

import { useRef, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";

/**
 * Composer fixo do front-door: textarea auto-crescente + botão enviar. Sem
 * botão de anexo no M1 (gate de login do 📎 é M3/M4). Enter envia, Shift+Enter
 * quebra linha. O valor é controlado pelo caller para que, em caso de erro de
 * envio, a mensagem do usuário permaneça no input para reenvio.
 */
export function Composer({
  value,
  onChange,
  onSend,
  disabled,
  placeholder = "Digite uma mensagem…",
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const canSend = value.trim().length > 0 && !disabled;

  return (
    <div className="border-t border-border bg-white px-4 py-3">
      <div className="mx-auto flex max-w-2xl items-end gap-2">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          placeholder={placeholder}
          className={cn(
            "flex-1 resize-none rounded-2xl border border-border px-4 py-2.5 text-sm font-sans",
            "text-content-primary placeholder:text-content-secondary",
            "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
            "max-h-40 leading-relaxed transition-colors"
          )}
        />
        <button
          type="button"
          onClick={onSend}
          disabled={!canSend}
          aria-label="Enviar"
          className={cn(
            "shrink-0 rounded-full bg-primary px-4 py-2.5 text-sm font-sans font-medium text-white",
            "transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          )}
        >
          Enviar
        </button>
      </div>
    </div>
  );
}
