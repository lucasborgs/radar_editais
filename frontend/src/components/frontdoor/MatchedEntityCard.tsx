"use client";

import Link from "next/link";
import type { MatchedEntity, InvestmentOffer } from "@/lib/api";
import { VerdictBlock } from "./VerdictBlock";

const brl = (n: number) => `R$ ${n.toLocaleString("pt-BR")}`;

function ticketLabel(t: InvestmentOffer["ticket_range"]): string | null {
  if (!t) return null;
  const { min_brl, max_brl } = t;
  if (min_brl && max_brl) return `${brl(min_brl)} – ${brl(max_brl)}`;
  if (max_brl) return `até ${brl(max_brl)}`;
  if (min_brl) return `a partir de ${brl(min_brl)}`;
  return null;
}

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

// KG v2: `kind` é slug (investidor/programa/ict). Estilo e rótulo de display por slug.
const KIND_STYLES: Record<string, string> = {
  investidor: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  programa: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  ict: "bg-purple-500/15 text-purple-700 dark:text-purple-300",
};

const KIND_LABELS: Record<string, string> = {
  investidor: "Investidor",
  programa: "Programa",
  ict: "ICT",
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
  onStartWriting?: (entityId: string, mode: "proposal" | "pitch") => void;
}) {
  const canWrite = entity.kind === "investidor" || entity.kind === "programa";
  const writeLabel = entity.kind === "investidor" ? "Escrever pitch →" : "Escrever proposta →";
  const writeMode = entity.kind === "investidor" ? ("pitch" as const) : ("proposal" as const);
  // D1/PR8: a unidade é a Oportunidade — o card leva à ficha (investidor →
  // ficha da OFERTA de investimento; programa → ficha do programa). ICT não tem
  // entity_id (é parceiro sugerido, não oportunidade) → não é clicável.
  const fichaHref = entity.entity_id
    ? `/oportunidades/${encodeURIComponent(entity.entity_id)}`
    : null;
  const ticket = entity.offer ? ticketLabel(entity.offer.ticket_range) : null;

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3 text-sm font-sans">
      <div className="flex items-start gap-3">
        <ScoreRing score={entity.score} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <KindBadge kind={entity.kind} />
            {entity.offer && (
              <span className="text-[11px] font-medium text-amber-700 dark:text-amber-300">
                Oportunidade de investimento
              </span>
            )}
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
          {entity.description && (
            <p className="text-xs text-content-secondary mt-1 line-clamp-2">
              {entity.description}
            </p>
          )}
          {entity.offer && (ticket || entity.offer.estagio_alvo.length > 0) && (
            <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
              {ticket && (
                <span className="inline-flex items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">
                  🎟 {ticket}
                </span>
              )}
              {entity.offer.estagio_alvo.map((e) => (
                <span
                  key={e}
                  className="inline-flex items-center rounded-full bg-content-secondary/10 px-2 py-0.5 text-[11px] text-content-secondary"
                >
                  {e}
                </span>
              ))}
            </div>
          )}
          <div className="flex items-center gap-3 mt-1.5 text-xs text-content-secondary">
            <span>🎯 {(entity.affinity * 10).toFixed(1)}</span>
            {entity.n_paths > 0 && (
              <span>{entity.n_paths} {"conexão"}{entity.n_paths !== 1 ? "ões" : ""}</span>
            )}
            {fichaHref && (
              <Link href={fichaHref} className="text-primary hover:underline">
                Ver ficha ↗
              </Link>
            )}
          </div>
        </div>
      </div>

      {entity.paths.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border">
          {entity.paths.map((p, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 rounded-full bg-primary/8 px-2 py-0.5 text-[11px] font-sans text-content-secondary"
            >
              <span className="max-w-[120px] truncate">«{p.src}»</span>
              <span className="text-content-tertiary">↔</span>
              <span className="max-w-[120px] truncate">«{p.dst}»</span>
            </span>
          ))}
        </div>
      )}

      {/* Veredito da oferta de investimento (Estágio 2, PR8.1) quando existir */}
      {entity.verdict && <VerdictBlock verdict={entity.verdict} />}

      {canWrite && entity.entity_id && onStartWriting && (
        <button
          onClick={() => onStartWriting(entity.entity_id!, writeMode)}
          className="mt-2 w-full rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
        >
          {writeLabel}
        </button>
      )}
    </div>
  );
}
