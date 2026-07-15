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
 * Painel retrátil de estrutura do projeto.
 *
 * Expandido: árvore "Proposta" (seções do outline, ✓ quando tem conteúdo,
 * ⚠︎n quando o auto-review encontrou findings) + grupo "Anexos" (library_items
 * — clicar insere @mention no chat) + rodapé com Revisar/Exportar.
 *
 * Colapsado: barra fina de ícones (§ seções · 📎 anexos · ▶ revisar · ⬇ exportar).
 * Estado persistido em localStorage.
 */
export function Explorer({
  sections,
  attachments,
  findingCounts,
  reviewing,
  onSelectSection,
  onSelectAttachment,
  onReview,
  onExport,
}: {
  sections: WorkspaceSection[];
  attachments: ContentItemSummary[];
  // Mapa título-da-seção → nº de findings (inclui chave "Geral").
  findingCounts: Map<string, number>;
  reviewing: boolean;
  onSelectSection: (title: string) => void;
  onSelectAttachment: (item: ContentItemSummary) => void;
  onReview: () => void;
  onExport: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  // Evita flash: lê localStorage só no client após o mount.
  useEffect(() => setCollapsed(loadCollapsed()), []);

  const totalFindings = Array.from(findingCounts.values()).reduce((a, b) => a + b, 0);

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
          title="Expandir estrutura"
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
          <button
            onClick={onReview}
            disabled={reviewing}
            title="Revisar"
            className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-40"
          >
            {reviewing ? (
              <span className="w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              "▶"
            )}
          </button>
          <button
            onClick={onExport}
            title="Exportar"
            className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-gray-50 transition-colors"
          >
            ⬇
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="w-60 shrink-0 border-r border-border bg-white flex flex-col">
      {/* topo: título + colapsar */}
      <div className="h-9 shrink-0 px-3 flex items-center justify-between border-b border-border">
        <span className="text-[10px] font-semibold text-content-secondary font-sans uppercase tracking-wide">
          Estrutura
        </span>
        <button
          onClick={toggle}
          title="Colapsar estrutura"
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
          const findings = findingCounts.get(s.title) ?? 0;
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
              <span className="truncate flex-1">{s.title}</span>
              {findings > 0 && (
                <span
                  title={`${findings} ${findings === 1 ? "observação" : "observações"} da revisão`}
                  className="shrink-0 inline-flex items-center gap-0.5 rounded-full bg-amber-100 text-amber-800 px-1.5 text-[10px] font-medium"
                >
                  ⚠︎{findings}
                </span>
              )}
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

      {/* rodapé: Revisar / Exportar */}
      <div className="shrink-0 border-t border-border p-2 space-y-1">
        <button
          onClick={onReview}
          disabled={reviewing}
          className="w-full text-xs font-sans text-content-primary border border-border rounded-lg py-1.5 hover:bg-gray-50 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-1.5"
        >
          {reviewing ? (
            <>
              <span className="w-3 h-3 border-2 border-primary border-t-transparent rounded-full animate-spin" />
              Revisando…
            </>
          ) : (
            <>
              ▶ Revisar
              {totalFindings > 0 && (
                <span className="inline-flex items-center rounded-full bg-amber-100 text-amber-800 px-1.5 text-[10px] font-medium">
                  {totalFindings}
                </span>
              )}
            </>
          )}
        </button>
        <button
          onClick={onExport}
          className="w-full text-xs font-sans text-content-primary border border-border rounded-lg py-1.5 hover:bg-gray-50 transition-colors"
        >
          ⬇ Exportar
        </button>
      </div>
    </div>
  );
}
