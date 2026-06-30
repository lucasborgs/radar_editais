"use client";

import { cn } from "@/lib/utils";

// Barra de status sob o header (spec §3): completude do perfil em % com barra
// visual + atalho. "editar perfil" abre o card de diff em modo edição com TODOS
// os campos atuais.
export function StatusBar({
  completeness,
  onEditProfile,
}: {
  completeness: number;
  onEditProfile: () => void;
}) {
  return (
    <div className="border-b border-border bg-surface">
      <div className="mx-auto flex max-w-2xl items-center gap-3 px-4 py-2">
        <span className="shrink-0 text-xs font-medium text-content-secondary font-sans">
          Perfil
        </span>
        <div className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-content-secondary/10">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${completeness}%` }}
          />
        </div>
        <span className="shrink-0 text-xs font-medium text-content-primary font-sans tabular-nums">
          {completeness}%
        </span>

        <div className="ml-auto flex items-center gap-3">
          <button
            type="button"
            onClick={onEditProfile}
            className={cn(
              "text-xs font-sans text-content-secondary transition-colors",
              "hover:text-content-primary",
            )}
          >
            editar perfil
          </button>
        </div>
      </div>
    </div>
  );
}
