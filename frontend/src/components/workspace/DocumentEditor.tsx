"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";
import { sectionAnchorId, type WorkspaceSection } from "./types";

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
 */
export function DocumentEditor({
  sections,
  highlightedSections,
  savingSection,
  onSaveSection,
  onSectionInteract,
  registerScrollTo,
}: {
  sections: WorkspaceSection[];
  highlightedSections: Set<string>;
  savingSection: string | null;
  onSaveSection: (title: string, content: string) => void;
  onSectionInteract: (title: string) => void;
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
    <div ref={containerRef} className="flex-1 overflow-y-auto bg-white">
      <div className="max-w-3xl mx-auto px-8 py-8 space-y-8">
        {sections.map((s) => (
          <SectionBlock
            key={s.title}
            section={s}
            highlighted={highlightedSections.has(s.title)}
            saving={savingSection === s.title}
            onSave={(content) => onSaveSection(s.title, content)}
            onInteract={() => onSectionInteract(s.title)}
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

function SectionBlock({
  section,
  highlighted,
  saving,
  onSave,
  onInteract,
}: {
  section: WorkspaceSection;
  highlighted: boolean;
  saving: boolean;
  onSave: (content: string) => void;
  onInteract: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(section.content);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
        <h2 className="font-heading text-base font-bold text-content-primary">
          {section.title}
        </h2>
        {saving && (
          <span className="text-[10px] text-content-secondary font-sans">salvando…</span>
        )}
      </div>

      {editing ? (
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={onKeyDown}
          placeholder="— rascunhe aqui ou peça ao agente"
          className={cn(
            "w-full resize-none rounded-md border border-primary/40 bg-white px-3 py-2",
            "text-sm font-mono text-content-primary leading-relaxed",
            "focus:outline-none focus:ring-2 focus:ring-primary/40",
          )}
        />
      ) : (
        <button
          type="button"
          onClick={enterEdit}
          className="w-full text-left rounded-md hover:bg-gray-50 transition-colors -mx-2 px-2 py-1"
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
