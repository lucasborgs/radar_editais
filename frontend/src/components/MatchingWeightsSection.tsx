"use client";

import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  ApiError,
  approveWeightSuggestions,
  getPendingWeightSuggestions,
  getWorkspaceWeights,
  revertWorkspaceWeight,
  type PendingInsight,
  type PendingSuggestion,
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
  medium: "bg-amber-100 text-amber-700",
  low: "bg-gray-100 text-gray-600",
};

const STATUS_LABEL: Record<PendingSuggestion["status"], string> = {
  pending: "Aguardando revisão",
  approved: "Aplicada",
  superseded: "Substituída por outra",
};

const STATUS_STYLES: Record<PendingSuggestion["status"], string> = {
  pending: "bg-blue-50 text-blue-700 border-blue-200",
  approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
  superseded: "bg-gray-50 text-gray-500 border-gray-200",
};

function formatDelta(d: number): string {
  if (d > 0) return `+${d}`;
  return String(d);
}

function ScopeBadge({ scope }: { scope: WorkspaceWeight["scope"] }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans",
        scope === "workspace"
          ? "bg-primary/10 text-primary"
          : "bg-gray-100 text-gray-600",
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
  const [error, setError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [w, p] = await Promise.all([
        getWorkspaceWeights(token),
        getPendingWeightSuggestions(token),
      ]);
      setWeights(w.weights);
      setInsights(p.insights);
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

  if (loading) {
    return (
      <div className="bg-white rounded-xl border border-border p-4 h-40 animate-pulse" />
    );
  }

  return (
    <div className="space-y-4">
      {/* Pesos atuais */}
      <div className="bg-white rounded-xl border border-border overflow-hidden">
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
          <div className="bg-white rounded-xl border border-border p-4 text-xs text-content-secondary font-sans">
            Nenhuma sugestão pendente. Sugestões surgem após o
            ReflectionService analisar outcomes do workspace.
          </div>
        ) : (
          <ul className="space-y-3">
            {insights.map((ins) => (
              <li
                key={ins.insight_id}
                className="bg-white rounded-xl border border-border p-4"
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
          <div className="mt-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-800 font-sans">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
