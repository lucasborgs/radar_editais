"use client";

import { useRef, type ChangeEvent, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";

// Formatos aceitos pelo extrator de documento (core/services/content_library.py
// ::extract_document_text → pdf/docx/txt/md; .doc antigo não, .ppt não).
const ACCEPT = ".pdf,.docx,.txt,.md";

/**
 * Composer fixo do front-door: 📎 anexo + textarea auto-crescente + enviar.
 * Enter envia, Shift+Enter quebra linha. O valor é controlado pelo caller para
 * que, em caso de erro de envio, a mensagem do usuário permaneça no input para
 * reenvio. O 📎 chama `onAttach`: anônimo → gate de login; logado → file picker
 * (a página decide; aqui só abrimos o seletor quando há `onPickFile`).
 */
export function Composer({
  value,
  onChange,
  onSend,
  onAttach,
  onPickFile,
  disabled,
  placeholder = "Digite uma mensagem…",
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  // Clique no 📎. Devolve `true` se já tratou (ex.: gate). Se devolver `false`/
  // void e houver `onPickFile`, abrimos o seletor de arquivo.
  onAttach?: () => boolean | void;
  onPickFile?: (file: File) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const handleAttach = () => {
    const handled = onAttach?.();
    if (handled !== true && onPickFile) fileRef.current?.click();
  };

  const handleFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && onPickFile) onPickFile(file);
    e.target.value = ""; // permite re-selecionar o mesmo arquivo
  };

  const canSend = value.trim().length > 0 && !disabled;
  const canAttach = !!onAttach || !!onPickFile;

  return (
    <div className="border-t border-border bg-surface px-4 py-3">
      <div className="mx-auto flex max-w-2xl items-end gap-2">
        {canAttach && (
          <>
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={handleFile}
            />
            <button
              type="button"
              onClick={handleAttach}
              disabled={disabled}
              aria-label="Anexar arquivo"
              className={cn(
                "shrink-0 rounded-full border border-border px-3 py-2.5 text-sm text-content-secondary",
                "transition-colors hover:bg-app-bg disabled:cursor-not-allowed disabled:opacity-40",
              )}
            >
              <span aria-hidden>📎</span>
            </button>
          </>
        )}
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
