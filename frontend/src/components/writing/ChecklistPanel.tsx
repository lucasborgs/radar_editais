"use client";

// ChecklistPanel — aba "Checklist" do canvas de escrita (spec_frontend_chat_first
// Fase 3). Lista os requisitos extraídos do edital com status, permite alternar
// status item a item e disparar a auto-revisão (3 passes paralelos no backend:
// compliance + qualidade + completude). Extraído da antiga /chat (3-pane) para
// o canvas split-pane não virar um monolito.

import { cn } from "@/lib/utils";

export interface ChecklistItem {
  id: string;
  requirement: string;
  section: string;
  status: "pending" | "addressed" | "not_applicable";
  source: string;
  reason?: string;
}

const CHECKLIST_STATUS_ICON: Record<ChecklistItem["status"], string> = {
  pending: "⬜",
  addressed: "✅",
  not_applicable: "–",
};

export function ChecklistPanel({
  items,
  onToggle,
  onAutoReview,
  reviewing,
}: {
  items: ChecklistItem[];
  onToggle: (id: string, status: ChecklistItem["status"]) => void;
  onAutoReview: () => void;
  reviewing: boolean;
}) {
  const done = items.filter((i) => i.status === "addressed").length;

  return (
    <div className="flex flex-col h-full">
      {/* Cabeçalho: contagem + auto-revisar */}
      <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
        <div>
          <p className="text-xs font-semibold text-content-secondary font-sans uppercase tracking-wide">
            Requisitos
          </p>
          <p className="text-[10px] text-content-secondary font-sans">
            {done}/{items.length} cobertos
          </p>
        </div>
        <button
          onClick={onAutoReview}
          disabled={reviewing}
          className="text-[10px] font-sans text-primary hover:underline disabled:opacity-50"
        >
          {reviewing ? "Revisando..." : "Auto-revisar"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-2 space-y-1">
        {items.map((item) => (
          <button
            key={item.id}
            onClick={() =>
              onToggle(item.id, item.status === "addressed" ? "pending" : "addressed")
            }
            className="w-full text-left px-4 py-2 flex items-start gap-2 hover:bg-gray-50 transition-colors group"
          >
            <span className="text-xs shrink-0 mt-0.5">
              {CHECKLIST_STATUS_ICON[item.status]}
            </span>
            <div className="min-w-0">
              <p
                className={cn(
                  "text-xs font-sans leading-snug",
                  item.status === "addressed"
                    ? "text-content-secondary line-through"
                    : "text-content-primary"
                )}
              >
                {item.requirement}
              </p>
              {item.reason && (
                <p className="text-[10px] text-content-secondary font-sans mt-0.5">
                  {item.reason}
                </p>
              )}
              <p className="text-[10px] text-content-secondary/60 font-sans">
                {item.section}
              </p>
            </div>
          </button>
        ))}
        {items.length === 0 && (
          <p className="text-xs text-content-secondary font-sans text-center py-4 px-4">
            Nenhum requisito extraído para este edital.
          </p>
        )}
      </div>
    </div>
  );
}
