"use client";

import Link from "next/link";
import type { MatchedEntity } from "@/lib/api";
import { VerdictBlock } from "./VerdictBlock";
import { PathBlock } from "./PathBlock";
import { ExcerptRow, SetorChips } from "./MatchedEditalCard";

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

const KIND_STYLES: Record<string, string> = {
  programa: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
};

const KIND_LABELS: Record<string, string> = {
  programa: "Programa",
};

function KindBadge({ kind }: { kind: string }) {
  const cls = KIND_STYLES[kind] ?? "bg-content-secondary/15 text-content-secondary";
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium font-sans ${cls}`}>
      {KIND_LABELS[kind] ?? kind}
    </span>
  );
}

export function MatchedEntityCard({
  entity,
  onStartWriting,
}: {
  entity: MatchedEntity;
  onStartWriting?: (entityId: string, mode: "proposal") => void;
}) {
  // A unidade é a Oportunidade — o card leva à ficha do programa, chaveada
  // pelo native_id. Investidores ficaram fora do escopo ativo.
  const fichaHref = entity.entity_id
    ? `/oportunidades/${encodeURIComponent(entity.entity_id)}`
    : null;

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3 text-sm font-sans">
      <div className="flex items-start gap-3">
        <ScoreRing score={entity.affinity} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <KindBadge kind={entity.kind} />
          </div>
          {fichaHref ? (
            <Link
              href={fichaHref}
              className="text-sm font-medium text-content-primary leading-snug line-clamp-2 hover:text-primary transition-colors"
            >
              {entity.name}
            </Link>
          ) : (
            <p className="text-sm font-medium text-content-primary leading-snug line-clamp-2">
              {entity.name}
            </p>
          )}
          <p className="text-[11px] text-content-secondary mt-0.5">Afinidade de escopo · não é probabilidade de aprovação</p>
          {entity.description && (
            <p className="text-xs text-content-secondary mt-1 line-clamp-2">
              {entity.description}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
            <SetorChips setores={entity.setores} />
          </div>
          {fichaHref && (
            <div className="mt-1.5 text-xs">
              <Link href={fichaHref} className="text-primary hover:underline">
                Ver ficha ↗
              </Link>
            </div>
          )}
        </div>
      </div>

      {entity.matched_excerpts.length > 0 && (
        <div className="flex flex-col gap-1.5 mt-2 pt-2 border-t border-border">
          {entity.matched_excerpts.slice(0, 3).map((x, i) => (
            <ExcerptRow key={i} excerpt={x} />
          ))}
        </div>
      )}
      {entity.matched_excerpts.length === 0 && (
        <p className="mt-2 pt-2 border-t border-border text-[11px] text-content-secondary">
          Resultado exploratório: ainda não há evidência textual real para explicar esta aderência.
        </p>
      )}

      {/* Veredito LLM (Estágio 3) quando existir */}
      {entity.verdict && <VerdictBlock verdict={entity.verdict} />}

      <PathBlock path={entity.caminho} explanation={entity.explicacao} />

      {entity.entity_id && onStartWriting && (
        <button
          onClick={() => onStartWriting(entity.entity_id, "proposal")}
          className="mt-2 w-full rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
        >
          Escrever proposta →
        </button>
      )}
    </div>
  );
}
