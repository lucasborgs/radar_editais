"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import {
  planningGenerate,
  getExistingPlan,
  startWritingSession,
  workspaceMode,
  getEditalById,
} from "@/lib/api";
import type { CompanyProfile } from "@/types/profile";
import type { Plan } from "@/types/api";
import { loadProfileFromStorage } from "@/types/profile";

const PLANNING_CTX_KEY = "planning_context";

interface PlanningCtx {
  question: string;
  analysis: string;
  editalId?: string;
}

function getPlanningContext(): PlanningCtx | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(PLANNING_CTX_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function clearPlanningContext() {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(PLANNING_CTX_KEY);
}

export default function PlanningPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session_id");
  const { loading: authLoading } = useAuth();

  const [profile, setProfile] = useState<CompanyProfile | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [adjustInput, setAdjustInput] = useState("");
  const [adjusting, setAdjusting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editalTitle, setEditalTitle] = useState<string>("");
  const isExisting = !!sessionId;

  useEffect(() => {
    const p = loadProfileFromStorage();
    setProfile(p);
  }, []);

  // Carrega plano (novo ou existente)
  useEffect(() => {
    if (authLoading) return;
    (async () => {
      if (sessionId) {
        // Plano existente de uma sessão
        try {
          const result = await getExistingPlan(sessionId);
          setPlan(result);
          const eid = (result as any)._edital_id;
          if (eid) {
            try {
              const card = await getEditalById(eid);
              setEditalTitle(card.title || eid);
            } catch {
              setEditalTitle(eid);
            }
          }
        } catch (e) {
          setError(
            e instanceof Error ? e.message : "Erro ao carregar plano.",
          );
        } finally {
          setLoading(false);
        }
        return;
      }

      // Novo plano (fluxo original do explore)
      const ctx = getPlanningContext();
      if (!ctx) {
        setError("Nenhum contexto de planejamento encontrado. Volte ao explorador.");
        setLoading(false);
        return;
      }

      if (ctx.editalId) {
        try {
          const card = await getEditalById(ctx.editalId);
          setEditalTitle(card.title || ctx.editalId);
        } catch {
          setEditalTitle(ctx.editalId);
        }
      }

      setGenerating(true);
      try {
        const result = await planningGenerate(
          ctx.question,
          ctx.analysis,
          ctx.editalId,
        );
        setPlan(result);
      } catch (e) {
        setError(
          e instanceof Error ? e.message : "Erro ao gerar plano.",
        );
      } finally {
        setGenerating(false);
        setLoading(false);
      }
    })();
  }, [authLoading, sessionId]);

  const handleApprove = useCallback(async () => {
    if (!plan || !profile) return;
    const ctx = getPlanningContext();
    try {
      const res = await startWritingSession(
        ctx?.editalId || "",
        profile,
        "proposal",
        plan,
      );
      clearPlanningContext();
      router.push(`/workspace/${res.session_id}`);
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : "Erro ao iniciar sessão.",
      );
    }
  }, [plan, profile, router]);

  const handleAdjustOld = useCallback(() => {
    // Fluxo original: voltar ao explorador para refinar pergunta
    clearPlanningContext();
    router.push("/");
  }, [router]);

  const handleAdjustWithChat = useCallback(async () => {
    if (!adjustInput.trim() || !sessionId) return;
    setSaving(true);
    try {
      // Envia ajuste via workspace /plan mode
      const res = await workspaceMode(sessionId, "plan", adjustInput);
      if (res.error) {
        toast.error(res.error);
        return;
      }
      // Recarrega o plano atualizado
      const updated = await getExistingPlan(sessionId);
      setPlan(updated);
      setAdjustInput("");
      setAdjusting(false);
      toast.success("Plano ajustado!");
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : "Erro ao ajustar plano.",
      );
    } finally {
      setSaving(false);
    }
  }, [adjustInput, sessionId]);

  const handleBackToWorkspace = useCallback(() => {
    if (sessionId) {
      router.push(`/workspace/${sessionId}`);
    } else {
      clearPlanningContext();
      router.push("/");
    }
  }, [router, sessionId]);

  if (loading || generating) {
    return (
      <div className="h-screen flex items-center justify-center bg-app-bg">
        <div className="text-center">
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <p className="text-sm text-content-secondary font-sans">
            {generating ? "Estruturando proposta…" : "Carregando…"}
          </p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center bg-app-bg px-6">
        <div className="text-center max-w-sm">
          <p className="text-sm text-content-primary font-sans mb-2">{error}</p>
          <button
            onClick={() => router.push("/")}
            className="text-sm text-primary font-sans hover:underline"
          >
            ← Voltar
          </button>
        </div>
      </div>
    );
  }

  if (!plan) return null;

  return (
    <div className="min-h-screen bg-app-bg">
      <div className="max-w-3xl mx-auto px-4 py-8">
        <h1 className="text-xl font-semibold text-content-primary font-sans mb-1">
          {plan.title}
        </h1>
        {editalTitle && (
          <p className="text-sm text-content-secondary font-sans mb-6">
            Edital: {editalTitle}
          </p>
        )}

        {/* Alignment */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-content-primary font-sans mb-3 uppercase tracking-wide">
            Alinhamento
          </h2>
          <div className="bg-white rounded-lg border border-border p-4 space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-content-secondary font-sans">
                Score:
              </span>
              <span className="text-sm font-semibold text-content-primary font-sans">
                {plan.alignment.match_score != null
                  ? `${(plan.alignment.match_score * 100).toFixed(0)}%`
                  : "—"}
              </span>
            </div>
            {plan.alignment.company_themes?.length > 0 && (
              <div>
                <span className="text-xs font-medium text-content-secondary font-sans">
                  Temas da empresa:
                </span>
                <div className="flex flex-wrap gap-1 mt-1">
                  {plan.alignment.company_themes.map((t) => (
                    <span
                      key={t}
                      className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary font-sans"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {plan.alignment.critical_gaps?.length > 0 && (
              <div>
                <span className="text-xs font-medium text-content-secondary font-sans">
                  Gaps críticos:
                </span>
                <ul className="mt-1 space-y-1">
                  {plan.alignment.critical_gaps.map((g, i) => (
                    <li
                      key={i}
                      className="text-xs text-content-secondary font-sans flex gap-1"
                    >
                      <span className="text-warning shrink-0">•</span>
                      {g}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>

        {/* Seções */}
        <section className="mb-8">
          <h2 className="text-sm font-semibold text-content-primary font-sans mb-3 uppercase tracking-wide">
            Seções da Proposta ({plan.sections.length})
          </h2>
          <div className="space-y-3">
            {plan.sections.map((sec) => (
              <div
                key={sec.id}
                className="bg-white rounded-lg border border-border p-4"
              >
                <h3 className="text-sm font-semibold text-content-primary font-sans mb-1">
                  {sec.title}
                </h3>
                {sec.estimated_length && (
                  <span className="text-xs text-content-secondary font-sans block mb-2">
                    ~{sec.estimated_length}
                  </span>
                )}
                <p className="text-xs text-content-secondary font-sans mb-2">
                  {sec.description}
                </p>
                {sec.key_points?.length > 0 && (
                  <ul className="space-y-0.5">
                    {sec.key_points.map((kp, i) => (
                      <li
                        key={i}
                        className="text-xs text-content-secondary font-sans flex gap-1"
                      >
                        <span className="text-primary shrink-0">→</span>
                        {kp}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Compliance hints */}
        {plan.compliance_hints?.length > 0 && (
          <section className="mb-8">
            <h2 className="text-sm font-semibold text-content-primary font-sans mb-3 uppercase tracking-wide">
              Compliance
            </h2>
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
              <ul className="space-y-1">
                {plan.compliance_hints.map((h, i) => (
                  <li
                    key={i}
                    className="text-xs text-content-secondary font-sans flex gap-1"
                  >
                    <span className="text-amber-600 shrink-0">⚠</span>
                    {h}
                  </li>
                ))}
              </ul>
            </div>
          </section>
        )}

        {/* Ações */}
        <div className="flex gap-3 pt-4 border-t border-border">
          <button
            onClick={handleBackToWorkspace}
            className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-content-secondary font-sans hover:bg-white/60 transition-colors"
          >
            {isExisting ? "← Voltar ao workspace" : "← Voltar"}
          </button>

          {isExisting ? (
            <>
              <button
                onClick={() => setAdjusting(!adjusting)}
                className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-white font-sans hover:bg-amber-600 transition-colors"
              >
                {adjusting ? "Cancelar ajuste" : "✏️ Ajustar plano"}
              </button>
              <button
                onClick={handleBackToWorkspace}
                className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white font-sans hover:bg-primary-dark transition-colors"
              >
                Ir para escrita →
              </button>
            </>
          ) : (
            <>
              <button
                onClick={handleAdjustOld}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-content-secondary font-sans hover:bg-white/60 transition-colors"
              >
                Ajustar plano
              </button>
              <button
                onClick={handleApprove}
                disabled={!profile}
                className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white font-sans hover:bg-primary-dark transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {profile
                  ? "Aprovar plano e começar a escrever"
                  : "Complete seu perfil para começar"}
              </button>
            </>
          )}
        </div>

        {/* Input de ajuste inline (só para plano existente) */}
        {isExisting && adjusting && (
          <div className="mt-6 p-4 bg-amber-50 border border-amber-200 rounded-lg">
            <label className="text-xs font-medium text-content-secondary font-sans block mb-2">
              O que deseja ajustar no plano?
            </label>
            <textarea
              value={adjustInput}
              onChange={(e) => setAdjustInput(e.target.value)}
              rows={3}
              className="w-full rounded-lg border border-border p-3 text-sm font-sans resize-none focus:outline-none focus:ring-2 focus:ring-primary/30"
              placeholder="Ex.: adicione uma seção de cronograma, remova a seção de orçamento..."
            />
            <button
              onClick={handleAdjustWithChat}
              disabled={!adjustInput.trim() || saving}
              className="mt-2 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white font-sans hover:bg-amber-700 transition-colors disabled:opacity-50"
            >
              {saving ? "Ajustando…" : "Aplicar ajuste"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
