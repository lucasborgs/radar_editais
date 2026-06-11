"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import type { ProfileDiffItem } from "@/lib/api";
import type { CompanyProfile } from "@/types/profile";
import type { DiffStatus, DiffEntry } from "@/types/frontdoor";
import { fieldSpec, coerceFieldValue, renderFieldValue } from "./profileFields";

// Card de diff de perfil inline (D5). Estados:
//   • pending → linhas +/~ + ações Aceitar / Editar / Descartar;
//   • editing → cada linha vira input do tipo certo (number/select/multiselect/
//     text/textarea); Aceitar aplica os valores editados;
//   • accepted/dismissed → read-only com selo.
// O caller decide o que fazer no aceite (aplicar no perfil, disparar radar) —
// este componente só devolve os items finais (com `new` possivelmente editado).

const TITLES: Record<NonNullable<DiffEntry["origin"]> | "default", string> = {
  turn: "Atualização de perfil sugerida",
  document: "Extraído do documento",
  merge: "Importar da conversa?",
  manual: "Editar perfil",
  default: "Atualização de perfil sugerida",
};

function isAdd(item: ProfileDiffItem): boolean {
  const old = item.old;
  return old === null || old === undefined || old === "" ||
    (Array.isArray(old) && old.length === 0);
}

// Editor de uma linha (input tipado por campo).
function FieldEditor({
  item,
  value,
  onChange,
}: {
  item: ProfileDiffItem;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const spec = fieldSpec(item.field as keyof CompanyProfile);
  const base = cn(
    "w-full rounded-md border border-border px-2 py-1 text-sm font-sans",
    "text-content-primary focus:outline-none focus:ring-2 focus:ring-primary/40",
  );

  if (spec.kind === "select") {
    return (
      <select
        className={base}
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">—</option>
        {spec.options?.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }

  if (spec.kind === "multiselect") {
    const arr = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {spec.options?.map((o) => {
          const active = arr.includes(o.value);
          return (
            <button
              key={o.value}
              type="button"
              onClick={() =>
                onChange(
                  active ? arr.filter((x) => x !== o.value) : [...arr, o.value],
                )
              }
              className={cn(
                "px-2 py-0.5 rounded-full text-xs font-sans border transition-colors",
                active
                  ? "bg-primary text-white border-primary"
                  : "bg-white text-content-secondary border-border hover:border-primary/50",
              )}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    );
  }

  if (spec.kind === "textarea") {
    return (
      <textarea
        className={cn(base, "resize-none leading-relaxed")}
        rows={2}
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  return (
    <input
      type={spec.kind === "number" ? "number" : "text"}
      className={base}
      value={value === null || value === undefined ? "" : String(value)}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function DiffCard({
  items,
  status,
  origin,
  onAccept,
  onDismiss,
}: {
  items: ProfileDiffItem[];
  status: DiffStatus;
  origin?: DiffEntry["origin"];
  // Devolve os items com `new` final (pós-edição, valores já coeridos).
  onAccept: (finalItems: ProfileDiffItem[]) => void;
  onDismiss: () => void;
}) {
  const [editing, setEditing] = useState(false);
  // Valores em edição (chaveados por field), inicializados com o `new` proposto.
  const [draft, setDraft] = useState<Record<string, unknown>>(() =>
    Object.fromEntries(items.map((i) => [i.field, i.new])),
  );

  const decided = status !== "pending";
  const title = TITLES[origin ?? "default"];

  function handleAccept() {
    const finalItems = items.map((i) => ({
      ...i,
      new: editing
        ? coerceFieldValue(i.field as keyof CompanyProfile, draft[i.field])
        : i.new,
    }));
    onAccept(finalItems);
  }

  return (
    <div
      className={cn(
        "rounded-xl border bg-white px-4 py-3 text-sm font-sans",
        decided ? "border-border opacity-90" : "border-primary/30 shadow-card",
      )}
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-content-secondary">
          {title}
        </span>
        {decided && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-medium",
              status === "accepted"
                ? "bg-[#1DB954]/15 text-[#169c46]"
                : "bg-gray-200 text-gray-600",
            )}
          >
            {status === "accepted" ? "aplicado" : "descartado"}
          </span>
        )}
      </div>

      <ul className="space-y-1.5">
        {items.map((item) => {
          const add = isAdd(item);
          return (
            <li key={item.field} className="flex items-start gap-2">
              <span
                className={cn(
                  "mt-0.5 w-3 shrink-0 text-center font-mono text-sm",
                  add ? "text-[#1DB954]" : "text-amber-500",
                )}
                aria-hidden
              >
                {add ? "+" : "~"}
              </span>
              <div className="min-w-0 flex-1">
                <span className="text-content-secondary">{item.label}: </span>
                {editing && !decided ? (
                  <div className="mt-1">
                    <FieldEditor
                      item={item}
                      value={draft[item.field]}
                      onChange={(v) =>
                        setDraft((d) => ({ ...d, [item.field]: v }))
                      }
                    />
                  </div>
                ) : (
                  <span className="text-content-primary">
                    {!add && (
                      <span className="text-content-secondary line-through">
                        {renderFieldValue(item.old)}{" "}
                      </span>
                    )}
                    {renderFieldValue(item.new)}
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      {!decided && (
        <div className="mt-3 flex items-center gap-2">
          <button
            type="button"
            onClick={handleAccept}
            className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
          >
            ✓ Aceitar
          </button>
          {!editing && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-content-primary transition-colors hover:bg-app-bg"
            >
              ✎ Editar
            </button>
          )}
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-lg px-3 py-1.5 text-xs font-medium text-content-secondary transition-colors hover:bg-app-bg"
          >
            Descartar
          </button>
        </div>
      )}
    </div>
  );
}
