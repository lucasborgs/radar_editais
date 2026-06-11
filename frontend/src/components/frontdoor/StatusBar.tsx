"use client";

import { cn } from "@/lib/utils";

// Barra de status sob o header (spec §3): completude do perfil em % com barra
// visual + atalhos. "ver radar atual ↓" só aparece se houver radar no transcript
// (rola até o último card). "editar perfil" abre o card de diff em modo edição
// com TODOS os campos atuais. É a mitigação do risco "ranking se perde no scroll".
export function StatusBar({
  completeness,
  hasRadar,
  onSeeRadar,
  onEditProfile,
}: {
  completeness: number;
  hasRadar: boolean;
  onSeeRadar: () => void;
  onEditProfile: () => void;
}) {
  return (
    <div className="border-b border-border bg-white">
      <div className="mx-auto flex max-w-2xl items-center gap-3 px-4 py-2">
        <span className="shrink-0 text-xs font-medium text-content-secondary font-sans">
          Perfil
        </span>
        <div className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-gray-100">
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${completeness}%` }}
          />
        </div>
        <span className="shrink-0 text-xs font-medium text-content-primary font-sans tabular-nums">
          {completeness}%
        </span>

        <div className="ml-auto flex items-center gap-3">
          {hasRadar && (
            <button
              type="button"
              onClick={onSeeRadar}
              className="text-xs font-sans text-primary transition-opacity hover:opacity-80"
            >
              ver radar atual ↓
            </button>
          )}
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
