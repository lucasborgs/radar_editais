"use client";

import Link from "next/link";
import { cn } from "@/lib/utils";
import type { WritingMode } from "@/types/api";
import { modeLabel } from "./types";
import { ModeBadge } from "./ModeBadge";

/**
 * Header fino do workspace: nome do edital/alvo + badge do modo
 * (Proposta/Pitch) + completude do documento + voltar ao radar.
 */
export function WorkspaceHeader({
  title,
  mode,
  filled,
  total,
  wsMode,
  sessionId,
}: {
  title: string;
  mode: WritingMode;
  filled: number;
  total: number;
  wsMode?: "explorer" | "plan" | "escrita";
  sessionId?: string;
}) {
  return (
    <header className="h-12 shrink-0 border-b border-border bg-white flex items-center gap-3 px-4">
      <Link
        href="/"
        className="text-xs font-sans text-content-secondary hover:text-content-primary transition-colors shrink-0"
        title="Voltar ao radar"
      >
        ← Radar
      </Link>

      {wsMode && <ModeBadge mode={wsMode} />}

      <span
        className={cn(
          "shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full font-sans",
          mode === "pitch"
            ? "bg-purple-500/15 text-purple-700"
            : "bg-primary/15 text-primary",
        )}
      >
        {modeLabel(mode)}
      </span>

      <h1 className="font-heading text-sm font-bold text-content-primary truncate min-w-0 flex-1">
        {title}
      </h1>

      {sessionId && (
        <Link
          href={`/workspace/planning?session_id=${sessionId}`}
          className="shrink-0 text-xs font-medium text-content-secondary font-sans hover:text-content-primary transition-colors"
          title="Ver plano estruturado"
        >
          📋 Plano
        </Link>
      )}

      <span className="shrink-0 text-xs text-content-secondary font-sans">
        {filled}/{total} {total === 1 ? "seção" : "seções"}
      </span>
    </header>
  );
}
