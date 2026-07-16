"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";
import { sectionAnchorId, type Finding, type WorkspaceSection } from "./types";

/**
 * Editor central do workspace: documento contínuo, uma âncora por seção.
 *
 * N1 (leitura): cada seção é um h2 + conteúdo via ReactMarkdown; seção vazia
 * mostra placeholder. Clicar numa seção (N2) troca o render por um textarea
 * auto-grow com o markdown cru; blur ou Cmd/Ctrl-S salva.
 *
 * Co-edição (N2): seções tocadas pelo agente no último turno entram em
 * `highlightedSections` (fundo suave). O highlight esmaece quando o usuário
 * entra/clica na seção — sinalizado via `onSectionInteract`.
 *
 * Revisar (N3): seções com findings do auto-review ganham um indicador ⚠︎n no
 * título; clicar abre um painel inline com os findings e o botão "Corrigir com
 * IA". Findings "Geral" aparecem num bloco no topo do documento.
 */
export function DocumentEditor({
  sections,
  highlightedSections,
  savingSection,
  findingsBySection,
  generalFindings,
  onSaveSection,
  onSectionInteract,
  onFixWithAI,
  onRefineSection,
  registerScrollTo,
}: {
  sections: WorkspaceSection[];
  highlightedSections: Set<string>;
  savingSection: string | null;
  // Findings por seção (do auto-review). Vazio = sem revisão / sem achados.
  findingsBySection: Map<string, Finding[]>;
  // Findings sem seção inferível ("Geral") — bloco no topo.
  generalFindings: Finding[];
  onSaveSection: (title: string, content: string) => void;
  onSectionInteract: (title: string) => void;
  // "Corrigir com IA": dispara um turno pré-preenchido com section_hint.
  onFixWithAI: (sectionHint: string | null, finding: Finding) => void;
  // Refinar seção (FASE 3): submete instrução do usuário via endpoint dedicate.
  onRefineSection: (title: string, instruction: string) => void;
  // O pai registra um callback p/ rolar até uma seção (clique no explorer).
  registerScrollTo: (fn: (title: string) => void) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    registerScrollTo((title: string) => {
      const el = document.getElementById(sectionAnchorId(title));
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [registerScrollTo]);

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto bg-surface">
      <div className="max-w-3xl mx-auto px-8 py-8 space-y-8">
        {generalFindings.length > 0 && (
          <GeneralFindingsBlock findings={generalFindings} onFixWithAI={onFixWithAI} />
        )}
        {sections.map((s) => (
          <SectionBlock
            key={s.title}
            section={s}
            highlighted={highlightedSections.has(s.title)}
            saving={savingSection === s.title}
            findings={findingsBySection.get(s.title) ?? []}
            onSave={(content) => onSaveSection(s.title, content)}
            onInteract={() => onSectionInteract(s.title)}
            onFixWithAI={onFixWithAI}
            onRefine={(instruction) => onRefineSection(s.title, instruction)}
          />
        ))}
        {sections.length === 0 && (
          <p className="text-sm text-content-secondary font-sans text-center py-12">
            Carregando documento…
          </p>
        )}
      </div>
    </div>
  );
}

// ── Bloco de findings "Geral" (sem seção) no topo do documento ──────────────
function GeneralFindingsBlock({
  findings,
  onFixWithAI,
}: {
  findings: Finding[];
  onFixWithAI: (sectionHint: string | null, finding: Finding) => void;
}) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-4 py-3">
      <p className="text-xs font-semibold text-amber-900 font-sans mb-2">
        ⚠︎ Observações gerais da revisão ({findings.length})
      </p>
      <ul className="space-y-2">
        {findings.map((f, i) => (
          <FindingRow key={i} finding={f} onFix={() => onFixWithAI(null, f)} />
        ))}
      </ul>
    </div>
  );
}

// ── Uma linha de finding (texto + badge + corrigir com IA) ──────────────────
const KIND_LABEL: Record<Finding["kind"], string> = {
  compliance: "Compliance",
  quality: "Qualidade",
  completeness: "Completude",
};

function FindingRow({ finding, onFix }: { finding: Finding; onFix: () => void }) {
  return (
    <li className="text-xs font-sans text-content-primary">
      <div className="flex items-start gap-2">
        <span className="shrink-0 inline-flex items-center gap-1 rounded-full bg-surface border border-amber-200 dark:border-amber-900 px-1.5 py-0.5 text-[10px] text-amber-800 dark:text-amber-300">
          {KIND_LABEL[finding.kind]}
          {finding.badge && <span className="opacity-70">· {finding.badge}</span>}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-content-primary">{finding.text}</p>
          {finding.suggestion && (
            <p className="text-content-secondary mt-0.5">{finding.suggestion}</p>
          )}
        </div>
      </div>
      <button
        type="button"
        onClick={onFix}
        className="mt-1 ml-[4.5rem] text-[11px] font-medium text-primary hover:underline"
      >
        ✨ Corrigir com IA
      </button>
    </li>
  );
}

function SectionBlock({
  section,
  highlighted,
  saving,
  findings,
  onSave,
  onInteract,
  onFixWithAI,
  onRefine,
}: {
  section: WorkspaceSection;
  highlighted: boolean;
  saving: boolean;
  findings: Finding[];
  onSave: (content: string) => void;
  onInteract: () => void;
  onFixWithAI: (sectionHint: string | null, finding: Finding) => void;
  onRefine: (instruction: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(section.content);
  const [showFindings, setShowFindings] = useState(false);
  const [showRefine, setShowRefine] = useState(false);
  const [refineInstruction, setRefineInstruction] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const refineInputRef = useRef<HTMLInputElement>(null);

  // Sincroniza o draft quando o conteúdo muda no backend (turno/undo) e não
  // estamos editando — evita sobrescrever o que o usuário digita.
  useEffect(() => {
    if (!editing) setDraft(section.content);
  }, [section.content, editing]);

  // Auto-grow do textarea.
  useEffect(() => {
    if (editing && textareaRef.current) {
      const el = textareaRef.current;
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
      el.focus();
    }
  }, [editing, draft]);

  function enterEdit() {
    onInteract(); // esmaece o highlight ao interagir
    setDraft(section.content);
    setEditing(true);
  }

  function commit() {
    setEditing(false);
    // Só persiste se mudou — evita PUT inútil ao só clicar e sair.
    if (draft !== section.content) onSave(draft);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      commit();
    }
  }

  const hasContent = section.content.trim().length > 0;

  return (
    <section
      id={sectionAnchorId(section.title)}
      onMouseEnter={highlighted ? onInteract : undefined}
      className={cn(
        "scroll-mt-4 rounded-lg transition-colors duration-700",
        highlighted && "bg-amber-50 ring-1 ring-amber-200 -mx-3 px-3 py-2",
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="font-heading text-base font-bold text-content-primary truncate">
            {section.title}
          </h2>
          {findings.length > 0 && (
            <button
              type="button"
              onClick={() => setShowFindings((v) => !v)}
              title={`${findings.length} ${findings.length === 1 ? "observação" : "observações"} da revisão`}
              className={cn(
                "shrink-0 inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium transition-colors",
                showFindings
                  ? "bg-amber-200 dark:bg-amber-900/60 text-amber-900 dark:text-amber-200"
                  : "bg-amber-100 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 hover:bg-amber-200 dark:hover:bg-amber-900/60",
              )}
            >
              ⚠︎{findings.length}
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {hasContent && (
            <button
              type="button"
              onClick={() => {
                setShowRefine((v) => !v);
                if (!showRefine) setTimeout(() => refineInputRef.current?.focus(), 50);
              }}
              className="text-[11px] font-medium text-primary hover:underline"
              title="Refinar seção com IA"
            >
              ✨ Refinar
            </button>
          )}
          {saving && (
            <span className="text-[10px] text-content-secondary font-sans">salvando…</span>
          )}
        </div>
      </div>

      {/* Refine inline input */}
      {showRefine && (
        <div className="mb-3 flex gap-2">
          <input
            ref={refineInputRef}
            type="text"
            value={refineInstruction}
            onChange={(e) => setRefineInstruction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && refineInstruction.trim()) {
                onRefine(refineInstruction.trim());
                setRefineInstruction("");
                setShowRefine(false);
              }
              if (e.key === "Escape") {
                setShowRefine(false);
                setRefineInstruction("");
              }
            }}
            placeholder="Ex: deixar mais técnico, encurtar, adicionar cronograma…"
            className="flex-1 rounded-md border border-border px-3 py-1.5 text-xs font-sans text-content-primary focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
          <button
            type="button"
            onClick={() => {
              if (refineInstruction.trim()) {
                onRefine(refineInstruction.trim());
                setRefineInstruction("");
                setShowRefine(false);
              }
            }}
            disabled={!refineInstruction.trim()}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-dark transition-colors disabled:opacity-50"
          >
            Refinar
          </button>
        </div>
      )}

      {/* Painel inline de findings da seção */}
      {findings.length > 0 && showFindings && (
        <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2">
          <ul className="space-y-2">
            {findings.map((f, i) => (
              <FindingRow key={i} finding={f} onFix={() => onFixWithAI(section.title, f)} />
            ))}
          </ul>
        </div>
      )}

      {editing ? (
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={onKeyDown}
          placeholder="— rascunhe aqui ou peça ao agente"
          className={cn(
            "w-full resize-none rounded-md border border-primary/40 bg-surface px-3 py-2",
            "text-sm font-mono text-content-primary leading-relaxed",
            "focus:outline-none focus:ring-2 focus:ring-primary/40",
          )}
        />
      ) : (
        <button
          type="button"
          onClick={enterEdit}
          className="w-full text-left rounded-md hover:bg-app-bg transition-colors -mx-2 px-2 py-1"
          title="Clique para editar"
        >
          {hasContent ? (
            <div className="prose prose-sm max-w-none prose-p:my-1.5 prose-li:my-0.5 prose-headings:font-semibold prose-headings:text-content-primary font-sans">
              <ReactMarkdown>{section.content}</ReactMarkdown>
            </div>
          ) : (
            <p className="text-sm text-content-secondary/70 font-sans italic">
              — rascunhe aqui ou peça ao agente
            </p>
          )}
        </button>
      )}
    </section>
  );
}
