"use client";

import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  ApiError,
  approveWeightSuggestions,
  getPendingWeightSuggestions,
  getWeightChanges,
  getWorkspaceWeights,
  revertWeightChange,
  revertWorkspaceWeight,
  type PendingInsight,
  type PendingSuggestion,
  type WeightChange,
  type WeightDimension,
  type WorkspaceWeight,
} from "@/lib/api";

const DIMENSION_LABEL: Record<WeightDimension, string> = {
  elegibilidade: "Elegibilidade",
  tematico: "Temático",
  trl: "TRL",
  mecanismo: "Mecanismo",
  contrapartida: "Contrapartida",
};

const CONFIDENCE_STYLES: Record<PendingInsight["confidence"], string> = {
  high: "bg-emerald-100 text-emerald-700",
  medium: "bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  low: "bg-content-secondary/10 text-content-secondary",
};

const STATUS_LABEL: Record<PendingSuggestion["status"], string> = {
  pending: "Aguardando revisão",
  approved: "Aplicada",
  superseded: "Substituída por outra",
};

const STATUS_STYLES: Record<PendingSuggestion["status"], string> = {
  pending: "bg-blue-50 dark:bg-blue-950/30 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-900",
  approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
  superseded: "bg-content-secondary/5 text-content-secondary border-border",
};

function formatDelta(d: number): string {
  if (d > 0) return `+${d}`;
  return String(d);
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("pt-BR", {
      day: "2-digit",
      month: "short",
    });
  } catch {
    return iso;
  }
}

// Linha de revert: o backend grava confidence=null e rationale "revert de <id>".
// Uma aplicação automática sempre tem confidence="high" (guarda do auto-apply).
const isRevertEntry = (c: WeightChange) => c.confidence === null;

function ScopeBadge({ scope }: { scope: WorkspaceWeight["scope"] }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans",
        scope === "workspace"
          ? "bg-primary/10 text-primary"
          : "bg-content-secondary/10 text-content-secondary",
      )}
    >
      {scope === "workspace" ? "personalizado" : "global"}
    </span>
  );
}

export function MatchingWeightsSection({ token }: { token: string | null }) {
  const [loading, setLoading] = useState(true);
  const [weights, setWeights] = useState<WorkspaceWeight[]>([]);
  const [insights, setInsights] = useState<PendingInsight[]>([]);
  const [changes, setChanges] = useState<WeightChange[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [w, p, c] = await Promise.all([
        getWorkspaceWeights(token),
        getPendingWeightSuggestions(token),
        getWeightChanges(token),
      ]);
      setWeights(w.weights);
      setInsights(p.insights);
      setChanges(c.changes);
    } catch (e: unknown) {
      const msg = e instanceof ApiError
        ? e.message
        : e instanceof Error
          ? e.message
          : "Erro ao carregar pesos";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleApprove(
    insightId: string,
    suggestion: PendingSuggestion,
  ) {
    if (!token) return;
    const key = `${insightId}:${suggestion.dimension}`;
    setActingOn(key);
    try {
      await approveWeightSuggestions(
        insightId,
        [{ dimension: suggestion.dimension, delta: suggestion.delta }],
        token,
      );
      toast.success(
        `Peso de ${DIMENSION_LABEL[suggestion.dimension]} atualizado`,
      );
      await reload();
    } catch (e: unknown) {
      const msg = e instanceof ApiError
        ? `${e.message} (${e.requestId ?? "sem id"})`
        : e instanceof Error
          ? e.message
          : "Erro ao aplicar sugestão";
      toast.error(msg);
    } finally {
      setActingOn(null);
    }
  }

  async function handleRevert(dimension: WeightDimension) {
    if (!token) return;
    const key = `revert:${dimension}`;
    setActingOn(key);
    try {
      await revertWorkspaceWeight(dimension, token);
      toast.success(
        `${DIMENSION_LABEL[dimension]} voltou ao peso global`,
      );
      await reload();
    } catch (e: unknown) {
      const msg = e instanceof ApiError
        ? e.message
        : e instanceof Error
          ? e.message
          : "Erro ao reverter peso";
      toast.error(msg);
    } finally {
      setActingOn(null);
    }
  }

  async function handleRevertChange(change: WeightChange) {
    if (!token) return;
    const key = `change:${change.id}`;
    setActingOn(key);
    try {
      const res = await revertWeightChange(change.id, token);
      if (res.reverted) {
        toast.success(
          `Ajuste de ${DIMENSION_LABEL[change.dimension]} revertido`,
        );
      } else {
        toast.info("Esse ajuste já havia sido revertido");
      }
      await reload();
    } catch (e: unknown) {
      const msg = e instanceof ApiError
        ? e.message
        : e instanceof Error
          ? e.message
          : "Erro ao reverter ajuste";
      toast.error(msg);
    } finally {
      setActingOn(null);
    }
  }

  // Ajustes automáticos ativos (não-revertidos, e não as próprias linhas de
  // revert) — alimentam o banner e o botão de reverter.
  const activeAutoChanges = changes.filter(
    (c) => !isRevertEntry(c) && c.reverted_at === null,
  );
  const activeDimensions = new Set(activeAutoChanges.map((c) => c.dimension));

  if (loading) {
    return (
      <div className="bg-surface rounded-xl border border-border p-4 h-40 animate-pulse" />
    );
  }

  return (
    <div className="space-y-4">
      {/* Banner: ajustes automáticos da reflexão ("AI acts, Human can correct") */}
      {activeDimensions.size > 0 && (
        <div className="rounded-xl border border-primary/30 bg-primary/5 px-4 py-3">
          <p className="text-sm font-medium text-content-primary font-sans">
            {activeDimensions.size === 1
              ? "1 dimensão ajustada automaticamente"
              : `${activeDimensions.size} dimensões ajustadas automaticamente`}{" "}
            pela reflexão
          </p>
          <p className="text-xs text-content-secondary font-sans mt-0.5">
            Aplicados com alta confiança a partir dos seus resultados. Revise no
            histórico abaixo e reverta qualquer um se discordar.
          </p>
        </div>
      )}

      {/* Pesos atuais */}
      <div className="bg-surface rounded-xl border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <p className="text-sm font-semibold text-content-primary font-sans">
            Pesos efetivos do matching
          </p>
          <p className="text-xs text-content-secondary font-sans mt-0.5">
            Cada dimensão soma para 100 pontos. Pesos personalizados
            sobrescrevem o default global.
          </p>
        </div>
        <ul className="divide-y divide-border">
          {weights.map((w) => (
            <li
              key={w.dimension}
              className="px-4 py-3 flex items-center justify-between gap-3"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm font-medium text-content-primary font-sans">
                  {DIMENSION_LABEL[w.dimension]}
                </span>
                <ScopeBadge scope={w.scope} />
              </div>
              <div className="flex items-center gap-3">
                <span className="font-data text-base text-content-primary">
                  {w.weight.toFixed(0)}
                </span>
                {w.scope === "workspace" && (
                  <button
                    type="button"
                    disabled={actingOn === `revert:${w.dimension}`}
                    onClick={() => handleRevert(w.dimension)}
                    className={cn(
                      "text-xs font-medium text-content-secondary hover:text-content-primary",
                      "underline decoration-dotted underline-offset-2 disabled:opacity-50",
                      "font-sans",
                    )}
                  >
                    voltar ao global
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>

      {/* Sugestões pendentes */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-widest text-content-secondary font-sans mb-2">
          Sugestões do Reflection Service
        </h3>

        {insights.length === 0 ? (
          <div className="bg-surface rounded-xl border border-border p-4 text-xs text-content-secondary font-sans">
            Nenhuma sugestão pendente. Sugestões surgem após o
            ReflectionService analisar outcomes do workspace.
          </div>
        ) : (
          <ul className="space-y-3">
            {insights.map((ins) => (
              <li
                key={ins.insight_id}
                className="bg-surface rounded-xl border border-border p-4"
              >
                <div className="flex items-start justify-between gap-3 mb-2">
                  <p className="text-sm text-content-primary font-sans leading-relaxed flex-1">
                    {ins.insight}
                  </p>
                  <span
                    className={cn(
                      "shrink-0 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans",
                      CONFIDENCE_STYLES[ins.confidence],
                    )}
                  >
                    {ins.confidence}
                  </span>
                </div>
                <ul className="space-y-2 mt-3">
                  {ins.suggestions.map((s, i) => {
                    const key = `${ins.insight_id}:${s.dimension}`;
                    const acting = actingOn === key;
                    return (
                      <li
                        key={`${ins.insight_id}-${i}`}
                        className={cn(
                          "rounded-lg border px-3 py-2 text-xs font-sans",
                          STATUS_STYLES[s.status],
                        )}
                      >
                        <div className="flex items-center justify-between gap-3 flex-wrap">
                          <div className="min-w-0">
                            <span className="font-semibold">
                              {DIMENSION_LABEL[s.dimension]}
                            </span>
                            <span className="mx-2 font-data">
                              {formatDelta(s.delta)}
                            </span>
                            <span className="opacity-70">
                              {STATUS_LABEL[s.status]}
                            </span>
                          </div>
                          {s.status === "pending" && (
                            <button
                              type="button"
                              disabled={acting}
                              onClick={() => handleApprove(ins.insight_id, s)}
                              className={cn(
                                "shrink-0 rounded-md bg-content-primary px-3 py-1 text-white",
                                "text-xs font-medium hover:opacity-90 disabled:opacity-50",
                              )}
                            >
                              {acting ? "Aplicando…" : "Aprovar"}
                            </button>
                          )}
                        </div>
                        {s.rationale && (
                          <p className="mt-1 opacity-80 leading-relaxed">
                            {s.rationale}
                          </p>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))}
          </ul>
        )}

        {error && (
          <div className="mt-2 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-3 py-2 text-xs text-red-800 dark:text-red-300 font-sans">
            {error}
          </div>
        )}
      </div>

      {/* Histórico de ajustes automáticos (Item 1) */}
      {changes.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-widest text-content-secondary font-sans mb-2">
            Ajustes automáticos
          </h3>
          <ul className="space-y-2">
            {changes.map((c) => {
              const revertEntry = isRevertEntry(c);
              const reverted = c.reverted_at !== null;
              const acting = actingOn === `change:${c.id}`;

              // Linha de revert: audit dimmed, sem ação.
              if (revertEntry) {
                return (
                  <li
                    key={c.id}
                    className="rounded-lg border border-border bg-content-secondary/5 px-3 py-2 text-xs font-sans text-content-secondary flex items-center gap-2"
                  >
                    <span>↩</span>
                    <span className="font-medium">
                      {DIMENSION_LABEL[c.dimension]}
                    </span>
                    <span className="font-data">{formatDelta(c.delta)}</span>
                    <span className="opacity-70">
                      revertido · {formatDate(c.applied_at)}
                    </span>
                  </li>
                );
              }

              return (
                <li
                  key={c.id}
                  className={cn(
                    "rounded-lg border px-3 py-2 text-xs font-sans",
                    reverted
                      ? "border-border bg-content-secondary/5 text-content-secondary"
                      : "border-emerald-200 bg-emerald-50 dark:bg-emerald-950/20 dark:border-emerald-900 text-content-primary",
                  )}
                >
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="min-w-0 flex items-center gap-2 flex-wrap">
                      <span className="font-semibold">
                        {DIMENSION_LABEL[c.dimension]}
                      </span>
                      <span className="font-data">
                        {c.old_weight.toFixed(0)} → {c.new_weight.toFixed(0)}
                      </span>
                      <span className="opacity-70 font-data">
                        ({formatDelta(c.delta)})
                      </span>
                      <span className={cn("opacity-70", reverted && "line-through")}>
                        {formatDate(c.applied_at)}
                      </span>
                      {reverted && (
                        <span className="opacity-70">revertido</span>
                      )}
                    </div>
                    {!reverted && (
                      <button
                        type="button"
                        disabled={acting}
                        onClick={() => handleRevertChange(c)}
                        className={cn(
                          "shrink-0 text-xs font-medium text-content-secondary hover:text-content-primary",
                          "underline decoration-dotted underline-offset-2 disabled:opacity-50",
                        )}
                      >
                        {acting ? "Revertendo…" : "reverter"}
                      </button>
                    )}
                  </div>
                  {c.rationale && (
                    <p className="mt-1 opacity-80 leading-relaxed">
                      {c.rationale}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
