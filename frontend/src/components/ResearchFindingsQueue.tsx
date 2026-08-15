"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import {
  ApiError,
  getResearchFindings,
  promoteResearchFinding,
  type ResearchFinding,
} from "@/lib/api";

// Fila da Fase B do deep_research: findings que o sub-agente de pesquisa
// descobriu (verified=false) e ainda não foram revisados. O usuário promove à
// biblioteca o que quiser manter. Estilo coerente com MatchingWeightsSection.
export function ResearchFindingsQueue({
  token,
  onPendingFindingsChange,
}: {
  token: string | null;
  onPendingFindingsChange?: (hasPending: boolean | null) => void;
}) {
  const [loading, setLoading] = useState(true);
  const [findings, setFindings] = useState<ResearchFinding[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await getResearchFindings(token);
      setFindings(res.findings);
    } catch (e: unknown) {
      const msg = e instanceof ApiError
        ? e.message
        : e instanceof Error
          ? e.message
          : "Erro ao carregar findings";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    if (!onPendingFindingsChange) return;
    onPendingFindingsChange(!token || loading || error ? null : findings.length > 0);
  }, [error, findings.length, loading, onPendingFindingsChange, token]);

  async function handlePromote(finding: ResearchFinding) {
    if (!token) return;
    setActingOn(finding.id);
    try {
      await promoteResearchFinding(finding.id, token);
      toast.success("Finding promovido à biblioteca");
      await reload();
    } catch (e: unknown) {
      const msg = e instanceof ApiError
        ? `${e.message} (${e.requestId ?? "sem id"})`
        : e instanceof Error
          ? e.message
          : "Erro ao promover finding";
      toast.error(msg);
    } finally {
      setActingOn(null);
    }
  }

  if (!token) return null;
  // Some silenciosamente se não há fila pendente (componente aditivo na página).
  if (!loading && !error && findings.length === 0) return null;

  return (
    <div className="rounded-xl border border-border bg-surface p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="font-heading text-sm font-bold text-content-primary">
            Pesquisas pendentes de revisão
          </h2>
          <p className="text-xs text-content-secondary font-sans mt-0.5">
            Descobertas do deep_research. Promova à biblioteca o que quiser manter.
          </p>
        </div>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-3 py-2 text-xs text-red-700 dark:text-red-300 font-sans">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-xs text-content-secondary font-sans py-2">Carregando…</div>
      ) : (
        <ul className="space-y-2">
          {findings.map((f) => (
            <li
              key={f.id}
              className="rounded-lg border border-border bg-surface/60 px-3 py-2.5 space-y-1.5"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300">
                      não verificado
                    </span>
                    <span className="text-[10px] text-content-secondary font-sans">
                      {f.sources.length} fonte{f.sources.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <p className="text-sm font-medium font-sans text-content-primary mt-1 truncate">
                    {f.question}
                  </p>
                  {f.answer && (
                    <p className="text-xs text-content-secondary font-sans mt-0.5 line-clamp-2">
                      {f.answer}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => handlePromote(f)}
                  disabled={actingOn === f.id}
                  className={cn(
                    "shrink-0 px-3 py-1.5 rounded-lg text-xs font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors",
                    "disabled:opacity-40 disabled:cursor-not-allowed",
                  )}
                >
                  {actingOn === f.id ? "Promovendo…" : "Promover à biblioteca"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
