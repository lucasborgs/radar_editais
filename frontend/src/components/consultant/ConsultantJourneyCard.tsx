"use client";

import { useEffect, useState } from "react";
import type { ConsultantBrief, ConsultantBriefUpdate, ConsultantJourneyState } from "@/lib/api";

const EDITABLE_FIELDS: Array<{ field: keyof ConsultantBriefUpdate; label: string }> = [
  { field: "problem_hypothesis", label: "Problema" },
  { field: "affected_users", label: "Usuários afetados" },
  { field: "solution_hypothesis", label: "Hipótese de solução" },
  { field: "innovation_objective", label: "Objetivo de inovação" },
  { field: "stage_maturity", label: "Estágio e maturidade" },
  { field: "location_constraints", label: "Localização e restrições" },
];

function valueFor(brief: ConsultantBrief, field: keyof ConsultantBriefUpdate): string {
  const value = brief[field];
  return Array.isArray(value) ? value.join(", ") : String(value ?? "");
}

export function ConsultantJourneyCard({
  state,
  onConfirm,
  onUpdate,
  onSelect,
  onReassess,
  onOpenWriting,
}: {
  state: ConsultantJourneyState;
  onConfirm: () => void;
  onUpdate: (updates: ConsultantBriefUpdate) => Promise<void>;
  onSelect: (pathId: string, reason: string) => Promise<void>;
  onReassess: (pathId: string, reason: string) => Promise<void>;
  onOpenWriting: (pathId: string) => Promise<void>;
}) {
  const brief = state.brief;
  const paths = state.paths;
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [decisionReason, setDecisionReason] = useState<Record<string, string>>({});
  const [reassessmentReason, setReassessmentReason] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!brief) return;
    setDraft(Object.fromEntries(EDITABLE_FIELDS.map(({ field }) => [field, valueFor(brief, field)])));
  }, [brief]);

  if (!brief && paths.length === 0) return null;

  const save = async () => {
    if (!brief) return;
    setSaving(true);
    try {
      const updates: ConsultantBriefUpdate = {};
      for (const { field } of EDITABLE_FIELDS) {
        if (draft[field] !== valueFor(brief, field)) {
          (updates as Record<string, unknown>)[field] = draft[field] ?? "";
        }
      }
      if (Object.keys(updates).length > 0) await onUpdate(updates);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-2xl space-y-3 px-1 pb-2">
      {brief && (
        <section className="rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-content-primary">Brief do projeto</h2>
            <span className="rounded-full bg-primary/10 px-2 py-1 text-[10px] font-medium text-primary">
              {brief.status === "confirmed" ? "confirmado" : `revisão ${brief.version}`}
            </span>
          </div>
          <p className="mt-2 text-xs text-content-secondary">
            Intenção original · origem: {brief.source_refs.original_intention?.join(", ") || "conversa"}
          </p>
          <p className="mt-2 text-sm text-content-primary">{brief.original_intention}</p>

          {brief.status === "draft" && (
            <div className="mt-4 space-y-3">
              {EDITABLE_FIELDS.map(({ field, label }) => (
                <label key={field} className="block">
                  <span className="mb-1 block text-xs font-medium text-content-secondary">
                    {label} · origem: {brief.source_refs[field]?.join(", ") || "não informado"}
                  </span>
                  <textarea
                    value={draft[field] ?? ""}
                    onChange={(event) => setDraft((current) => ({ ...current, [field]: event.target.value }))}
                    rows={field === "problem_hypothesis" || field === "solution_hypothesis" ? 2 : 1}
                    className="w-full rounded-lg border border-border bg-app-bg px-3 py-2 text-sm text-content-primary outline-none focus:border-primary"
                  />
                </label>
              ))}
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving}
                className="rounded-lg border border-primary/30 px-3 py-2 text-xs font-medium text-primary hover:bg-primary/5 disabled:opacity-50"
              >
                {saving ? "Salvando…" : "Salvar revisão"}
              </button>
            </div>
          )}

          {state.gaps.length > 0 && (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
              <strong>Para decidir melhor:</strong> {state.gaps[0]}
            </div>
          )}
          {state.pending_confirmation && brief.status === "draft" && (
            <button
              type="button"
              onClick={onConfirm}
              className="mt-4 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-white hover:opacity-90"
            >
              Confirmar e criar projeto
            </button>
          )}
        </section>
      )}

      {state.project && (
        <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-900 dark:bg-emerald-950/20">
          <h2 className="text-sm font-semibold text-content-primary">Projeto confirmado</h2>
          <p className="mt-1 text-xs text-content-secondary">
            ID {state.project.id} · perfil usado em {state.project.profile_version || "versão não informada"}
          </p>
          {state.project.decisions.map((decision) => (
            <p key={decision} className="mt-2 text-sm text-content-primary">{decision}</p>
          ))}
        </section>
      )}

      {paths.map((path) => (
        <section key={path.id} className="rounded-xl border border-primary/25 bg-primary/5 p-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-content-primary">
              {path.kind === "open_innovation" ? "Inovação aberta" : "Caminho potencial"}
            </h2>
            <span className="text-xs font-medium text-primary">{path.kind || path.tipo}</span>
          </div>
          <p className="mt-1 text-[11px] text-content-secondary">
            Estado: {path.status === "reassess_needed" ? "reavaliação necessária" : path.status}
          </p>
          {path.kind === "open_innovation" && (
            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
              <span className="rounded-full bg-amber-100 px-2 py-1 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                Sem edital formal
              </span>
              <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                Não é elegibilidade
              </span>
            </div>
          )}
          <p className="mt-2 text-sm text-content-primary">{path.recommendation}</p>
          {path.facts.length > 0 && <p className="mt-2 text-xs text-content-secondary">Fato: {path.facts[0]}</p>}
          {path.inferences.length > 0 && <p className="mt-2 text-xs text-content-secondary">Inferência: {path.inferences[0]}</p>}
          {path.gaps.length > 0 && <p className="mt-2 text-xs text-content-secondary">Lacuna: {path.gaps[0]}</p>}
          <p className="mt-3 text-sm font-medium text-content-primary">Próximo passo: {path.next_step}</p>
          <p className="mt-2 text-xs text-content-secondary">
            Validade: {path.temporal_state === "active" ? "ativa" : "desconhecida — precisa de revisão"}
          </p>
          {path.kind === "open_innovation" && (
            <p className="mt-2 text-xs text-content-secondary">
              Fonte: {path.needs_review ? "pendente de revisão humana" : "revisada"} · coleta: {String(path.freshness?.collected_at || "data não informada")}
            </p>
          )}
          {path.kind === "open_innovation" && path.actors.length > 0 && (
            <p className="mt-2 text-xs text-content-secondary">
              Promotor: {String(path.actors[0].name ?? "não informado")}
            </p>
          )}
          {path.requirements.length > 0 && (
            <p className="mt-2 text-xs text-content-secondary">
              <strong>Regras/requisitos:</strong> {path.requirements.slice(0, 3).join(" · ")}
            </p>
          )}
          {path.rule_evaluations.length > 0 && (
            <div className="mt-3 space-y-1 text-xs text-content-secondary">
              {path.rule_evaluations.map((rule) => (
                <p key={`${rule.rule}-${rule.status}`}>
                  {rule.status === "satisfied" ? "Confirmada" : rule.status === "unknown" ? "Desconhecida" : "Não satisfeita"}: {rule.reason}
                </p>
              ))}
            </div>
          )}
          {path.actors.length > 0 && (
            <p className="mt-3 text-xs text-content-secondary">
              ICT possível: {String(path.actors[0].name ?? "capacidade catalogada")} — confirme competência, acesso e disponibilidade.
            </p>
          )}
          {path.evidence.length > 0 && (
            <details className="mt-3 text-xs text-content-secondary">
              <summary className="cursor-pointer font-medium">Ver evidências</summary>
              <div className="mt-2 space-y-1">
                {path.evidence.slice(0, 4).map((item, index) => (
                  <p key={`${item.ref}-${index}`}>
                    {item.document || item.label}{item.locator ? ` · ${item.locator}` : ""}
                    {item.quote ? ` — “${item.quote}”` : ""}
                    {item.source_url ? (
                      <a className="ml-1 text-primary underline" href={item.source_url} target="_blank" rel="noreferrer">
                        fonte
                      </a>
                    ) : null}
                  </p>
                ))}
              </div>
            </details>
          )}
          {state.selected_path_id === path.id ? (
            <div className="mt-4 space-y-2 text-xs">
              <p className="font-medium text-primary">Caminho escolhido. Valide as lacunas antes de avançar.</p>
              {path.decision?.reason ? (
                <p className="text-content-secondary">Motivo registrado: {path.decision.reason}</p>
              ) : null}
              <div className="flex gap-2">
                <input
                  value={reassessmentReason[path.id] ?? ""}
                  onChange={(event) => setReassessmentReason((current) => ({ ...current, [path.id]: event.target.value }))}
                  placeholder="O que mudou?"
                  className="min-w-0 flex-1 rounded-lg border border-border bg-app-bg px-3 py-2 text-content-primary outline-none focus:border-primary"
                />
                <button
                  type="button"
                  disabled={!reassessmentReason[path.id]?.trim()}
                  onClick={() => void onReassess(path.id, reassessmentReason[path.id]?.trim() ?? "")}
                  className="rounded-lg border border-amber-400/60 px-3 py-2 font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50 dark:text-amber-300"
                >
                  Reavaliar
                </button>
              </div>
              {path.formal_instrument !== false && (
                <button
                  type="button"
                  onClick={() => void onOpenWriting(path.id)}
                  className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-white hover:opacity-90"
                >
                  Abrir proposta técnica
                </button>
              )}
            </div>
          ) : (
            <div className="mt-4 space-y-2">
              <input
                value={decisionReason[path.id] ?? ""}
                onChange={(event) => setDecisionReason((current) => ({ ...current, [path.id]: event.target.value }))}
                placeholder="Por que este caminho faz sentido agora?"
                className="w-full rounded-lg border border-border bg-app-bg px-3 py-2 text-xs text-content-primary outline-none focus:border-primary"
              />
              <button
                type="button"
                disabled={!decisionReason[path.id]?.trim()}
                onClick={() => void onSelect(path.id, decisionReason[path.id]?.trim() ?? "")}
                className="rounded-lg border border-primary/40 px-3 py-2 text-xs font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
              >
                Escolher este caminho
              </button>
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
