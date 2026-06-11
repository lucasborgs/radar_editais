"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import type { ContentItemSummary } from "@/types/api";
import type { WorkspaceSection } from "./types";

const COLLAPSE_KEY = "radar:workspace-explorer-collapsed";

function loadCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(COLLAPSE_KEY) === "1";
}

/**
 * Explorer retrátil do workspace.
 *
 * Expandido: árvore "Proposta" (seções do outline, ✓ quando tem conteúdo) +
 * grupo "Anexos" (library_items — clicar insere @mention no chat) + rodapé com
 * Revisar/Exportar (DESABILITADOS em N1/N2; tooltip "em breve").
 *
 * Colapsado: barra fina de ícones (§ seções · 📎 anexos · ▶ revisar · ⬇ exportar).
 * Estado persistido em localStorage.
 */
export function Explorer({
  sections,
  attachments,
  onSelectSection,
  onSelectAttachment,
}: {
  sections: WorkspaceSection[];
  attachments: ContentItemSummary[];
  onSelectSection: (title: string) => void;
  onSelectAttachment: (item: ContentItemSummary) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  // Evita flash: lê localStorage só no client após o mount.
  useEffect(() => setCollapsed(loadCollapsed()), []);

  function toggle() {
    setCollapsed((c) => {
      const next = !c;
      try {
        window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  if (collapsed) {
    return (
      <div className="w-11 shrink-0 border-r border-border bg-white flex flex-col items-center py-3 gap-1">
        <button
          onClick={toggle}
          title="Expandir explorer"
          className="w-8 h-8 flex items-center justify-center rounded-lg text-content-secondary hover:bg-gray-50 transition-colors"
        >
          ☰
        </button>
        <div className="mt-2 flex flex-col gap-1 text-content-secondary">
          <span className="w-8 h-8 flex items-center justify-center" title="Seções">
            §
          </span>
          <span className="w-8 h-8 flex items-center justify-center" title="Anexos">
            📎
          </span>
          <span
            className="w-8 h-8 flex items-center justify-center opacity-40"
            title="Revisar (em breve)"
          >
            ▶
          </span>
          <span
            className="w-8 h-8 flex items-center justify-center opacity-40"
            title="Exportar (em breve)"
          >
            ⬇
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="w-60 shrink-0 border-r border-border bg-white flex flex-col">
      {/* topo: título + colapsar */}
      <div className="h-9 shrink-0 px-3 flex items-center justify-between border-b border-border">
        <span className="text-[10px] font-semibold text-content-secondary font-sans uppercase tracking-wide">
          Explorer
        </span>
        <button
          onClick={toggle}
          title="Colapsar explorer"
          className="text-content-secondary hover:text-content-primary transition-colors text-sm leading-none"
        >
          «
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-2">
        {/* árvore: Proposta */}
        <p className="px-3 py-1 text-[11px] font-semibold text-content-primary font-sans">
          ▾ Proposta
        </p>
        {sections.map((s) => {
          const hasContent = s.content.trim().length > 0;
          return (
            <button
              key={s.title}
              type="button"
              onClick={() => onSelectSection(s.title)}
              className="w-full text-left pl-5 pr-3 py-1.5 flex items-center gap-2 text-sm font-sans text-content-primary hover:bg-gray-50 transition-colors"
            >
              <span
                className={cn(
                  "w-3 shrink-0 text-xs font-data text-center",
                  hasContent ? "text-green-600" : "text-content-secondary/40",
                )}
              >
                {hasContent ? "✓" : "·"}
              </span>
              <span className="truncate">{s.title}</span>
            </button>
          );
        })}

        {/* árvore: Anexos */}
        <p className="px-3 pt-3 py-1 text-[11px] font-semibold text-content-primary font-sans">
          ▾ Anexos <span className="text-content-secondary font-normal">(@)</span>
        </p>
        {attachments.length === 0 ? (
          <p className="pl-5 pr-3 py-1.5 text-xs text-content-secondary font-sans">
            Nenhum item na biblioteca.
          </p>
        ) : (
          attachments.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectAttachment(item)}
              title={`Inserir @${item.title} no chat`}
              className="w-full text-left pl-5 pr-3 py-1.5 flex items-center gap-2 text-sm font-sans text-content-primary hover:bg-gray-50 transition-colors"
            >
              <span className="shrink-0 text-xs" aria-hidden>
                📎
              </span>
              <span className="truncate">{item.title}</span>
            </button>
          ))
        )}
      </div>

      {/* rodapé: Revisar / Exportar — desabilitados em N1/N2 (N3/N4) */}
      <div className="shrink-0 border-t border-border p-2 space-y-1">
        <button
          disabled
          title="em breve"
          className="w-full text-xs font-sans text-content-secondary/50 border border-border rounded-lg py-1.5 cursor-not-allowed"
        >
          ▶ Revisar
        </button>
        <button
          disabled
          title="em breve"
          className="w-full text-xs font-sans text-content-secondary/50 border border-border rounded-lg py-1.5 cursor-not-allowed"
        >
          ⬇ Exportar
        </button>
      </div>
    </div>
  );
}
