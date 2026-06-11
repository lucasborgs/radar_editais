"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { getEditalById, type RadarItem, type RadarResponse } from "@/lib/api";
import type { KGMatchResult, EditalCard } from "@/types/edital";
import type { InvestorMatch } from "@/types/investidor";
import { scoreColor } from "@/lib/constants";
import { BriefView } from "./BriefView";

const TYPE_LABEL: Record<string, string> = {
  edital: "Edital",
  desafio: "Desafio",
  programa: "Programa",
  investidor: "Investidor",
};

const TYPE_CLASS: Record<string, string> = {
  edital: "bg-[#e07b39]/15 text-[#b35e22]",
  desafio: "bg-purple-100 text-purple-700",
  programa: "bg-blue-100 text-blue-700",
  investidor: "bg-emerald-100 text-emerald-700",
};

function formatBRL(v: number | null | undefined): string {
  if (v == null) return "—";
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0,
  }).format(v);
}

// "Por quê" de uma linha: a justificativa do matcher (evento ou entidade).
function itemReason(item: RadarItem): string {
  const p = item.payload as Partial<KGMatchResult & InvestorMatch>;
  return p.justificativa ?? "";
}

// ── Expansão de item ────────────────────────────────────────────────────────
function EventExpansion({
  item,
  onBrief,
  onProposta,
}: {
  item: RadarItem;
  onBrief: (editalId: string) => Promise<void>;
  onProposta: (editalId: string) => void;
}) {
  const router = useRouter();
  const [card, setCard] = useState<EditalCard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [briefing, setBriefing] = useState(false);

  // Carrega o detalhe público do catálogo (uma vez, ao expandir).
  useEffect(() => {
    let alive = true;
    getEditalById(item.id)
      .then((c) => alive && setCard(c))
      .catch(() => alive && setError(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [item.id]);

  const reason = itemReason(item);

  return (
    <div className="mt-2 space-y-2 border-t border-border pt-2 text-xs font-sans text-content-secondary">
      {loading && <p>Carregando detalhe…</p>}
      {error && <p>Não consegui carregar o detalhe deste item.</p>}
      {card && (
        <div className="space-y-1">
          {card.objective && (
            <p className="text-content-primary">{card.objective}</p>
          )}
          <div className="flex flex-wrap gap-x-4 gap-y-0.5">
            <span>Prazo: {card.deadline || "—"}</span>
            <span>
              Valor: {formatBRL(card.value_range?.min_brl)} –{" "}
              {formatBRL(card.value_range?.max_brl)}
            </span>
            {card.eligible_entities?.length > 0 && (
              <span>Elegíveis: {card.eligible_entities.join(", ")}</span>
            )}
          </div>
        </div>
      )}
      {reason && (
        <p>
          <span className="font-medium text-content-primary">Por que casa: </span>
          {reason}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          onClick={() => router.push(`/editais/${item.id}`)}
          className="rounded-lg border border-border px-2.5 py-1 text-[11px] font-medium text-content-primary transition-colors hover:bg-app-bg"
        >
          Ver página completa
        </button>
        <button
          type="button"
          disabled={briefing}
          onClick={async () => {
            setBriefing(true);
            try {
              await onBrief(item.id);
            } finally {
              setBriefing(false);
            }
          }}
          className="rounded-lg border border-border px-2.5 py-1 text-[11px] font-medium text-content-primary transition-colors hover:bg-app-bg disabled:opacity-50"
        >
          {briefing ? "Gerando…" : "Gerar brief GO/NO-GO"}
        </button>
        <button
          type="button"
          onClick={() => onProposta(item.id)}
          className="rounded-lg bg-primary px-2.5 py-1 text-[11px] font-medium text-white transition-opacity hover:opacity-90"
        >
          Começar proposta
        </button>
      </div>
    </div>
  );
}

function EntityExpansion({
  item,
  onPitch,
}: {
  item: RadarItem;
  onPitch: (investidorId: string) => void;
}) {
  const p = item.payload as InvestorMatch;
  return (
    <div className="mt-2 space-y-1 border-t border-border pt-2 text-xs font-sans text-content-secondary">
      {p.match_dimensions?.tese && (
        <p>
          <span className="font-medium text-content-primary">Tese: </span>
          {p.match_dimensions.tese}
        </p>
      )}
      {p.match_dimensions?.estagio && (
        <p>
          <span className="font-medium text-content-primary">Estágio: </span>
          {p.match_dimensions.estagio}
        </p>
      )}
      {p.estagio_alvo && p.estagio_alvo.length > 0 && (
        <p>Estágios-alvo: {p.estagio_alvo.join(", ")}</p>
      )}
      {p.justificativa && <p>{p.justificativa}</p>}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {p.site && (
          <a
            href={p.site}
            target="_blank"
            rel="noreferrer"
            className="rounded-lg border border-border px-2.5 py-1 text-[11px] font-medium text-content-primary transition-colors hover:bg-app-bg"
          >
            Site do investidor ↗
          </a>
        )}
        <button
          type="button"
          onClick={() => onPitch(item.id)}
          className="rounded-lg bg-primary px-2.5 py-1 text-[11px] font-medium text-white transition-opacity hover:opacity-90"
        >
          Escrever pitch
        </button>
      </div>
    </div>
  );
}

// ── Linha do radar ──────────────────────────────────────────────────────────
function RadarRow({
  item,
  expanded,
  onToggle,
  brief,
  onBrief,
  onProposta,
  onPitch,
}: {
  item: RadarItem;
  expanded: boolean;
  onToggle: () => void;
  brief: import("@/lib/api").OpportunityBrief | null;
  onBrief: (editalId: string) => Promise<void>;
  onProposta: (editalId: string) => void;
  onPitch: (investidorId: string) => void;
}) {
  const type = item.opportunity_type;
  const reason = itemReason(item);
  const provisorio = item.verificacao === "provisorio";

  return (
    <li
      className={cn(
        "rounded-lg border px-3 py-2 transition-colors",
        item.tier === "fraco" ? "border-border bg-app-bg/40" : "border-border bg-white",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-start gap-2 text-left"
      >
        <span
          className="mt-1 h-2 w-2 shrink-0 rounded-full"
          style={{ backgroundColor: scoreColor(item.score) }}
          aria-hidden
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-medium text-content-primary font-sans">
              {item.title}
            </span>
            <span
              className={cn(
                "rounded-full px-1.5 py-0.5 text-[10px] font-medium",
                TYPE_CLASS[type] ?? "bg-gray-100 text-gray-600",
              )}
            >
              {TYPE_LABEL[type] ?? type}
            </span>
            {provisorio && (
              <span className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] font-medium text-gray-500">
                não verificado
              </span>
            )}
          </div>
          {reason && (
            <p className="mt-0.5 line-clamp-1 text-xs text-content-secondary font-sans">
              {reason}
            </p>
          )}
          {item.why_now && (
            <p className="text-[11px] text-content-secondary font-sans">{item.why_now}</p>
          )}
        </div>
        <span className="shrink-0 text-xs tabular-nums text-content-secondary">
          {item.score.toFixed(1)}
        </span>
      </button>

      {expanded &&
        (item.kind_class === "evento" ? (
          <EventExpansion item={item} onBrief={onBrief} onProposta={onProposta} />
        ) : (
          <EntityExpansion item={item} onPitch={onPitch} />
        ))}

      {expanded && brief && (
        <div className="mt-2">
          <BriefView brief={brief} />
        </div>
      )}
    </li>
  );
}

// ── Card ────────────────────────────────────────────────────────────────────
export function RadarCard({
  data,
  onBrief,
  onProposta,
  onPitch,
  briefs,
}: {
  data: RadarResponse;
  // Gera o brief para um edital e devolve o resultado (page-level: auth/gate).
  // Retorna null se a ação virou gate (anônimo) — a linha não mostra brief então.
  onBrief: (editalId: string) => Promise<void>;
  onProposta: (editalId: string) => void;
  // Escrever pitch (item investidor): cria sessão mode=pitch e abre o workspace.
  onPitch: (investidorId: string) => void;
  // Briefs já gerados, chaveados por edital_id (estado mantido pela página).
  briefs: Record<string, import("@/lib/api").OpportunityBrief>;
}) {
  const [showAll, setShowAll] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const items = data.radar ?? [];
  const visible = showAll ? items : items.slice(0, 5);

  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-white px-4 py-3 text-sm font-sans text-content-secondary">
        Nenhuma oportunidade casou com o perfil atual. Refine a descrição ou
        adicione TRL / porte / UF para melhorar o radar.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-white px-4 py-3 shadow-card">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-content-secondary font-sans">
          Radar · {items.length}{" "}
          {items.length === 1 ? "oportunidade" : "oportunidades"}
        </span>
      </div>

      <ul className="space-y-1.5">
        {visible.map((item) => (
          <RadarRow
            key={`${item.kind_class}:${item.id}`}
            item={item}
            expanded={expandedId === item.id}
            onToggle={() =>
              setExpandedId((cur) => (cur === item.id ? null : item.id))
            }
            brief={briefs[item.id] ?? null}
            onBrief={onBrief}
            onProposta={onProposta}
            onPitch={onPitch}
          />
        ))}
      </ul>

      {items.length > 5 && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="mt-2 text-xs font-sans text-primary transition-opacity hover:opacity-80"
        >
          {showAll ? "ver menos" : `ver ranking completo (${items.length})`}
        </button>
      )}
    </div>
  );
}
