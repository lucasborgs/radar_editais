"use client";

// DocumentCanvas — painel direito (canvas/artifacts) da conversa de escrita
// (spec_frontend_chat_first Fase 3). Duas abas:
//   • Documento: seletor de seção (status + progresso), edição inline da seção
//     ativa, salvar (com indicador de não-salvo), export Markdown e visualização
//     da proposta completa.
//   • Checklist: requisitos do edital + auto-revisão (3 passes).
// A seleção de seção aqui dirige o chat de seção da página (section-start),
// exatamente como na antiga /chat (3-pane).

import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import { Tabs } from "@/components/ui/Tabs";
import { Modal } from "@/components/ui/Modal";
import {
  completionStats,
  buildFullProposal,
  type ProposalSection,
  type SectionStatus,
} from "@/lib/writing";
import { ChecklistPanel, type ChecklistItem } from "@/components/writing/ChecklistPanel";

// ── Ícones de status das seções (paridade com a antiga /chat) ──────────────────
const STATUS_ICON: Record<SectionStatus, string> = {
  pending: "·",
  draft: "◑",
  reviewed: "✓",
};
const STATUS_CLS: Record<SectionStatus, string> = {
  pending: "text-content-secondary",
  draft: "text-primary",
  reviewed: "text-green-600",
};

interface DocumentCanvasProps {
  // Documento
  sections: ProposalSection[];
  activeSection: string | null;
  onSelectSection: (title: string) => void;
  docContent: string;
  onDocChange: (value: string) => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean; // há edição local não salva na seção ativa
  drafts: Record<string, string>; // textos salvos por seção (proposta completa)
  onExport: () => void;
  initializing: boolean;
  hasEdital: boolean;
  // Checklist
  checklist: ChecklistItem[];
  onChecklistToggle: (id: string, status: ChecklistItem["status"]) => void;
  onAutoReview: () => void;
  reviewing: boolean;
}

export function DocumentCanvas({
  sections,
  activeSection,
  onSelectSection,
  docContent,
  onDocChange,
  onSave,
  saving,
  dirty,
  drafts,
  onExport,
  initializing,
  hasEdital,
  checklist,
  onChecklistToggle,
  onAutoReview,
  reviewing,
}: DocumentCanvasProps) {
  const [showFullProposal, setShowFullProposal] = useState(false);

  const { done, total } = useMemo(() => completionStats(sections), [sections]);
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const fullProposal = useMemo(
    () => buildFullProposal(sections, drafts),
    [sections, drafts]
  );

  // ── Aba Documento ─────────────────────────────────────────────────────────
  const documentoTab = (
    <div className="flex flex-col h-full">
      {sections.length === 0 ? (
        <div className="flex-1 flex items-center justify-center p-4">
          {initializing ? (
            <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          ) : (
            <p className="text-xs text-content-secondary font-sans text-center">
              {hasEdital ? "Carregando seções..." : "Selecione um edital"}
            </p>
          )}
        </div>
      ) : (
        <>
          {/* Progresso */}
          <div className="px-4 pt-3 pb-3 border-b border-border shrink-0">
            <div className="flex justify-between items-center mb-1.5">
              <span className="text-xs font-sans text-content-secondary">Progresso</span>
              <span className="text-xs font-data font-semibold text-primary">
                {done}/{total}
              </span>
            </div>
            <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary rounded-full transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>

          {/* Seletor de seção */}
          <div className="border-b border-border max-h-44 overflow-y-auto py-1 shrink-0">
            {sections.map((s) => (
              <button
                key={s.title}
                type="button"
                onClick={() => onSelectSection(s.title)}
                className={cn(
                  "w-full text-left px-4 py-2 flex items-center gap-2.5 transition-colors text-sm font-sans",
                  activeSection === s.title
                    ? "bg-primary/8 text-primary font-medium"
                    : "text-content-primary hover:bg-gray-50",
                  s.isGeneral && "border-t border-border mt-1 pt-2.5"
                )}
              >
                <span
                  className={cn(
                    "text-xs font-data w-3 shrink-0",
                    STATUS_CLS[s.status]
                  )}
                >
                  {STATUS_ICON[s.status]}
                </span>
                <span className="truncate">{s.title}</span>
              </button>
            ))}
          </div>

          {/* Editor inline da seção ativa */}
          {activeSection ? (
            <div className="flex-1 flex flex-col p-4 min-h-0">
              <div className="flex items-center justify-between mb-2">
                <p className="text-xs font-semibold text-content-primary font-sans truncate">
                  {activeSection}
                </p>
                <div className="flex items-center gap-2 shrink-0">
                  {dirty && (
                    <span
                      className="text-[10px] font-sans text-amber-600"
                      title="Há alterações não salvas nesta seção"
                    >
                      • não salvo
                    </span>
                  )}
                  <button
                    onClick={onSave}
                    disabled={saving}
                    className="text-xs font-sans text-primary hover:underline disabled:opacity-50"
                  >
                    {saving ? "Salvando..." : "Salvar"}
                  </button>
                </div>
              </div>
              <textarea
                className={cn(
                  "flex-1 w-full rounded-lg border border-border px-3 py-2.5 text-sm font-sans",
                  "text-content-primary placeholder:text-content-secondary resize-none",
                  "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
                  "leading-relaxed"
                )}
                value={docContent}
                onChange={(e) => onDocChange(e.target.value)}
                placeholder="O conteúdo desta seção aparecerá aqui. Use o chat para gerar rascunhos e peça para salvar na seção."
              />
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center p-6 text-center">
              <p className="text-sm text-content-secondary font-sans">
                Selecione uma seção para editar o documento.
              </p>
            </div>
          )}

          {/* Rodapé: proposta completa + export */}
          <div className="border-t border-border px-4 py-2.5 flex items-center gap-4 shrink-0">
            <button
              onClick={() => setShowFullProposal(true)}
              className="text-xs font-sans text-primary hover:text-primary-hover transition-colors"
            >
              Ver proposta →
            </button>
            <button
              onClick={onExport}
              className="text-xs font-sans text-content-secondary hover:text-content-primary transition-colors"
            >
              Copiar Markdown
            </button>
          </div>
        </>
      )}

      {/* Modal da proposta completa (buildFullProposal) */}
      <Modal
        open={showFullProposal}
        onClose={() => setShowFullProposal(false)}
        title="Proposta completa"
        size="lg"
        footer={
          <button
            onClick={() => navigator.clipboard.writeText(fullProposal)}
            disabled={!fullProposal}
            className="text-xs font-sans text-primary hover:underline disabled:opacity-50"
          >
            Copiar tudo
          </button>
        }
      >
        {fullProposal ? (
          <pre className="whitespace-pre-wrap font-sans text-sm text-content-primary leading-relaxed">
            {fullProposal}
          </pre>
        ) : (
          <p className="text-sm text-content-secondary font-sans text-center py-8">
            Nenhuma seção com rascunho ainda.
          </p>
        )}
      </Modal>
    </div>
  );

  // ── Aba Checklist ─────────────────────────────────────────────────────────
  const checklistTab = (
    <ChecklistPanel
      items={checklist}
      onToggle={onChecklistToggle}
      onAutoReview={onAutoReview}
      reviewing={reviewing}
    />
  );

  return (
    // O Tabs do repo renderiza tablist + um único painel; embrulhamos cada
    // painel em altura cheia para o conteúdo (editor/checklist) ocupar o canvas.
    <Tabs
      className="flex flex-col h-full [&>[role=tabpanel]]:flex-1 [&>[role=tabpanel]]:min-h-0 [&>[role=tabpanel]]:pt-0 [&>[role=tablist]]:px-2 [&>[role=tablist]]:shrink-0"
      defaultValue="documento"
      items={[
        { value: "documento", label: "Documento", content: documentoTab },
        { value: "checklist", label: "Checklist", content: checklistTab },
      ]}
    />
  );
}
