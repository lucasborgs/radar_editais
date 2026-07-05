"use client";

import { cn } from "@/lib/utils";
import { STATUS_CONFIG } from "@/lib/constants";
import type { EditalStatus } from "@/types/edital";
import type { MatchedEdital, MatchedEditalPath } from "@/lib/api";
import { VerdictBlock } from "./VerdictBlock";

function ScoreRing({ score }: { score: number }) {
  const color = score >= 0.7 ? "#1DB954" : score >= 0.55 ? "#f59e0b" : "#f97316";
  const pct = Math.round(score * 100);
  return (
    <div className="relative flex items-center justify-center shrink-0" title={`${pct}%`}>
      <svg className="h-10 w-10 -rotate-90" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r="15.5" fill="none" stroke="currentColor" strokeWidth="2"
          className="text-border" />
        <circle cx="18" cy="18" r="15.5" fill="none" stroke={color} strokeWidth="2"
          strokeDasharray={`${pct * 0.31} 31`} strokeLinecap="round" />
      </svg>
      <span className="absolute text-[10px] font-semibold font-sans" style={{ color }}>{pct}</span>
    </div>
  );
}

function JustificationBadge({ path }: { path: MatchedEditalPath }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-primary/8 px-2 py-0.5 text-[11px] font-sans text-content-secondary">
      <span className="max-w-[120px] truncate">«{path.src}»</span>
      <span className="text-content-tertiary">↔</span>
      <span className="max-w-[120px] truncate">«{path.dst}»</span>
    </span>
  );
}

export function MatchedEditalCard({
  edital,
  onStartWriting,
}: {
  edital: MatchedEdital;
  onStartWriting: (source: string, id: string) => void;
}) {
  const statusKey = (edital.status?.toUpperCase() as EditalStatus) ?? "Desconhecido";
  const statusCfg = STATUS_CONFIG[statusKey] ?? { label: edital.status ?? "—", className: "" };

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3 text-sm font-sans">
      <div className="flex items-start gap-3">
        <ScoreRing score={edital.score} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-[11px] font-medium text-content-secondary">
              {edital.source}/{edital.edital_id}
            </span>
          </div>
          <p className="text-sm font-medium text-content-primary leading-snug line-clamp-2">
            {edital.name}
          </p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-xs text-content-secondary">
            <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium", statusCfg.className)}>
              {statusCfg.label}
            </span>
            {edital.prazo && (
              <span>⏰ {edital.prazo}</span>
            )}
            {edital.valor && (
              <span>💰 {edital.valor}</span>
            )}
            <span>🎯 {(edital.affinity * 10).toFixed(1)}</span>
          </div>
        </div>
      </div>

      {edital.elegibilidade?.status === "nao_verificada" && (
        <div
          className="mt-2 pt-2 border-t border-border text-[11px] text-amber-600"
          title={edital.elegibilidade.unknown.join("; ")}
        >
          ⚠️ Elegibilidade não verificada — complete o perfil ({edital.elegibilidade.unknown.length} critério
          {edital.elegibilidade.unknown.length > 1 ? "s" : ""}: {edital.elegibilidade.unknown.join("; ")})
        </div>
      )}

      {edital.verdict && <VerdictBlock verdict={edital.verdict} />}

      {edital.paths.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border">
          {edital.paths.map((p, i) => (
            <JustificationBadge key={i} path={p} />
          ))}
        </div>
      )}

      <button
        onClick={() => onStartWriting(edital.source, edital.edital_id)}
        className="mt-2 w-full rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
      >
        Escrever proposta →
      </button>
    </div>
  );
}
