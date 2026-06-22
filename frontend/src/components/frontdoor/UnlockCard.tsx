"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { FieldEditor } from "./DiffCard";
import { coerceFieldValue } from "./profileFields";
import type { CompanyProfile } from "@/types/profile";
import type { ProfileGap } from "@/types/frontdoor";

// Card "Destravar mais matches" — Etapa 2 do onboarding progressivo (spec
// onboarding-input-ux, Decisão 3). Mostra os campos faltantes de maior impacto
// (missingHighImpact) com inputs tipados inline. Ao aplicar, devolve só os campos
// preenchidos (coeridos); o caller persiste e re-roda o radar — o usuário vê o
// efeito ("AI drafts, humans decide" + efeito composto).

export function UnlockCard({
  gaps,
  onApply,
  onDismiss,
}: {
  gaps: ProfileGap[];
  onApply: (updates: Partial<CompanyProfile>) => void;
  onDismiss: () => void;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});

  function handleApply() {
    const updates: Record<string, unknown> = {};
    for (const g of gaps) {
      const raw = draft[g.field];
      if (raw === undefined) continue;
      const coerced = coerceFieldValue(g.field, raw);
      const empty = Array.isArray(coerced)
        ? coerced.length === 0
        : coerced === "" || coerced === null;
      if (!empty) updates[g.field] = coerced;
    }
    if (Object.keys(updates).length > 0) {
      onApply(updates as Partial<CompanyProfile>);
    }
  }

  const hasInput = Object.values(draft).some((v) =>
    Array.isArray(v) ? v.length > 0 : v !== "" && v !== null && v !== undefined,
  );

  return (
    <div className="rounded-xl border border-primary/30 bg-surface px-4 py-3 text-sm font-sans shadow-card">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-content-secondary">
          Destravar mais matches
        </span>
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs text-content-secondary transition-colors hover:text-content-primary"
        >
          agora não
        </button>
      </div>

      <ul className="space-y-3">
        {gaps.map((g) => (
          <li key={g.field}>
            <label className="block text-content-primary">{g.prompt}</label>
            <p className="mb-1 text-xs text-content-secondary">{g.why}</p>
            <FieldEditor
              field={g.field}
              value={draft[g.field]}
              onChange={(v) => setDraft((d) => ({ ...d, [g.field]: v }))}
            />
          </li>
        ))}
      </ul>

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={handleApply}
          disabled={!hasInput}
          className={cn(
            "rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity",
            "hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          Aplicar e atualizar radar
        </button>
      </div>

      <p className="mt-2 text-xs text-content-secondary">
        Tem uma proposta antiga? Anexe pelo 📎 do campo de mensagem para enriquecer
        o perfil.
      </p>
    </div>
  );
}
