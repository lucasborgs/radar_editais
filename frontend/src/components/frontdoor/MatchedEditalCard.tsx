"use client";

import { cn } from "@/lib/utils";
import { STATUS_CONFIG } from "@/lib/constants";
import type { EditalStatus } from "@/types/edital";
import type { MatchedEdital, MatchedExcerpt } from "@/lib/api";
import { deadlineUrgency, urgencyLabel, type DeadlineUrgency } from "@/lib/radar-utils";
import { temporalBadge, temporalDeadlineText } from "@/lib/opportunity-temporal";
import { VerdictBlock } from "./VerdictBlock";
import { PathBlock } from "./PathBlock";

function ScoreRing({ score }: { score: number }) {
  const color = score >= 0.7 ? "#1DB954" : score >= 0.55 ? "#f59e0b" : "#f97316";
  const pct = Math.min(100, Math.max(0, Math.round(score * 100)));
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

// A explicação do match v3 são os TRECHOS REAIS que casaram (empresa ↔ edital),
// não conceitos extraídos — "AI drafts, humans decide": o usuário lê o texto.
export function ExcerptRow({ excerpt }: { excerpt: MatchedExcerpt }) {
  return (
    <div
      className="rounded-lg bg-primary/5 px-2.5 py-1.5 text-[11px] leading-snug font-sans"
      title="Evidência usada para calcular a afinidade"
    >
      <p className="text-content-secondary line-clamp-2">
        <span className="font-medium text-content-primary">
          {excerpt.origin === "library_doc" ? "Da sua biblioteca:" : "Seu perfil:"}
        </span> {excerpt.company_text}
      </p>
      <p className="text-content-secondary line-clamp-2 mt-0.5">
        <span className="font-medium text-content-primary">
          {excerpt.section ? `Edital (${excerpt.section})` : "Edital"}:
        </span>{" "}
        {excerpt.edital_text}
      </p>
    </div>
  );
}

export function SetorChips({ setores }: { setores?: string[] }) {
  if (!setores || setores.length === 0) return null;
  return (
    <>
      {setores.map((s) => (
        <span
          key={s}
          className="inline-flex items-center rounded-full bg-content-secondary/10 px-2 py-0.5 text-[11px] text-content-secondary"
        >
          {s}
        </span>
      ))}
    </>
  );
}

export function MatchedEditalCard({
  edital,
  onStartWriting,
  onCompare,
  isCompared = false,
}: {
  edital: MatchedEdital;
  onStartWriting: (source: string, id: string) => void;
  onCompare?: () => void;
  isCompared?: boolean;
}) {
  const badge = temporalBadge(edital);
  const statusKey = (badge.tone === "active" ? "ABERTA" : "ENCERRADA") as EditalStatus;
  const statusCfg = STATUS_CONFIG[statusKey] ?? { label: badge.label, className: "" };
  const urgency: DeadlineUrgency = deadlineUrgency(edital);
  const prazo = temporalDeadlineText(edital);

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3 text-sm font-sans">
      <div className="flex items-start gap-3">
        <ScoreRing score={edital.affinity} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-[11px] font-medium text-content-secondary">
              {edital.source}/{edital.edital_id}
            </span>
          </div>
          <p className="text-sm font-medium text-content-primary leading-snug line-clamp-2">
            {edital.name}
          </p>
          <p className="text-[11px] text-content-secondary mt-0.5">Afinidade de escopo · não é probabilidade de aprovação</p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-xs text-content-secondary">
            {badge.tone === "review" ? (
              <span className="inline-flex items-center rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                {badge.label}
              </span>
            ) : (
              <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium", statusCfg.className)}>
                {statusCfg.label}
              </span>
            )}
            {prazo && urgency !== "continuous" && urgency !== "confirm" && (
              <span>⏰ {prazo}</span>
            )}
            {edital.valor && (
              <span>💰 {edital.valor}</span>
            )}
            <SetorChips setores={edital.setores} />
          </div>
          {(urgency === "closing" || urgency === "soon" || urgency === "continuous" || urgency === "confirm") && (
            <p className={cn(
              "mt-1 text-[11px] font-medium",
              urgency === "closing" ? "text-red-600 dark:text-red-300" : "text-content-secondary",
            )}>
              {urgencyLabel(edital)}
            </p>
          )}
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

      {edital.matched_excerpts.length > 0 && (
        <div className="flex flex-col gap-1.5 mt-2 pt-2 border-t border-border">
          {edital.matched_excerpts.slice(0, 3).map((x, i) => (
            <ExcerptRow key={i} excerpt={x} />
          ))}
        </div>
      )}
      {edital.matched_excerpts.length === 0 && (
        <p className="mt-2 pt-2 border-t border-border text-[11px] text-content-secondary">
          Resultado exploratório: ainda não há evidência textual real para explicar este match.
        </p>
      )}

      <PathBlock path={edital.caminho} explanation={edital.explicacao} />

      <div className="mt-2 flex gap-2">
        {onCompare && (
          <button
            onClick={onCompare}
            className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-content-primary hover:bg-app-bg"
          >
            {isCompared ? "Remover comparação" : "Comparar"}
          </button>
        )}
        <button
          onClick={() => onStartWriting(edital.source, edital.edital_id)}
          className="flex-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
        >
          Escrever proposta →
        </button>
      </div>
    </div>
  );
}
