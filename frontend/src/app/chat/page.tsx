"use client";

import { Suspense, useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import {
  startWritingSession,
  sendWritingTurn,
  startSectionChat,
  saveDocumentSection,
  exportDocument,
  type DocumentSection,
} from "@/lib/api";

interface ChecklistItem {
  id: string;
  requirement: string;
  section: string;
  status: "pending" | "addressed" | "not_applicable";
  source: string;
  reason?: string;
}
import { cn } from "@/lib/utils";
import {
  getProposalSections,
  completionStats,
  buildFullProposal,
  type ProposalSection,
  type SectionStatus,
} from "@/lib/writing";
import { loadProfileFromStorage, EMPTY_PROFILE } from "@/types/profile";
import type { WritingMessage } from "@/types/api";
import { MentionsTextarea } from "@/components/ui/MentionsTextarea";
import { useAuth } from "@/lib/auth";

// ── Typing indicator ─────────────────────────────────────────────────────────

function TypingDots() {
  return (
    <div className="flex items-center gap-1 px-1 py-0.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 bg-content-secondary rounded-full animate-bounce"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}

// ── Message bubble ────────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: WritingMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[82%] rounded-2xl px-4 py-3 text-sm font-sans leading-relaxed",
          isUser
            ? "bg-primary text-white rounded-br-sm"
            : "bg-white border border-border text-content-primary rounded-bl-sm"
        )}
      >
        <pre className="whitespace-pre-wrap font-sans text-inherit">{msg.content}</pre>
        <p className={cn("text-[10px] mt-1.5", isUser ? "text-white/60" : "text-content-secondary")}>
          {msg.timestamp}
        </p>
      </div>
      {!isUser && msg.draftSaved && (
        <span className="mt-1 text-[10px] font-sans text-green-600 px-1">
          ↗ Salvo no documento
        </span>
      )}
    </div>
  );
}

// ── Document editor ───────────────────────────────────────────────────────────

function DocumentEditor({
  sectionTitle,
  content,
  onChange,
  onSave,
  saving,
}: {
  sectionTitle: string | null;
  content: string;
  onChange: (v: string) => void;
  onSave: () => void;
  saving: boolean;
}) {
  if (!sectionTitle) {
    return (
      <div className="flex-1 flex items-center justify-center p-6 text-center">
        <p className="text-sm text-content-secondary font-sans">
          Selecione uma seção para editar o documento
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
        <p className="text-xs font-semibold text-content-secondary font-sans uppercase tracking-wide">
          Documento
        </p>
        <button
          onClick={onSave}
          disabled={saving}
          className="text-xs font-sans text-primary hover:underline disabled:opacity-50"
        >
          {saving ? "Salvando..." : "Salvar"}
        </button>
      </div>
      <div className="p-4 flex-1 flex flex-col">
        <p className="text-xs font-semibold text-content-primary font-sans mb-2">{sectionTitle}</p>
        <textarea
          className={cn(
            "flex-1 w-full rounded-lg border border-border px-3 py-2.5 text-sm font-sans",
            "text-content-primary placeholder:text-content-secondary resize-none",
            "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
            "leading-relaxed"
          )}
          value={content}
          onChange={(e) => onChange(e.target.value)}
          placeholder="O conteúdo desta seção aparecerá aqui. Use o chat para gerar rascunhos e clique em 'Inserir na seção'."
        />
      </div>
    </div>
  );
}

// ── Section checklist ─────────────────────────────────────────────────────────

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

function SectionChecklist({
  sections,
  active,
  onSelect,
}: {
  sections: ProposalSection[];
  active: string | null;
  onSelect: (title: string) => void;
}) {
  const { done, total } = completionStats(sections);
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div className="flex flex-col h-full">
      {/* Progress */}
      <div className="px-4 pt-4 pb-3 border-b border-border">
        <div className="flex justify-between items-center mb-1.5">
          <span className="text-xs font-sans text-content-secondary">Progresso</span>
          <span className="text-xs font-data font-semibold text-primary">{done}/{total}</span>
        </div>
        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-primary rounded-full transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Sections */}
      <div className="flex-1 overflow-y-auto py-2">
        {sections.map((s) => (
          <button
            key={s.title}
            type="button"
            onClick={() => onSelect(s.title)}
            className={cn(
              "w-full text-left px-4 py-2.5 flex items-center gap-2.5 transition-colors",
              "text-sm font-sans",
              active === s.title
                ? "bg-primary/8 text-primary font-medium"
                : "text-content-primary hover:bg-gray-50",
              s.isGeneral && "border-t border-border mt-1 pt-3"
            )}
          >
            <span className={cn("text-xs font-data w-3 shrink-0", STATUS_CLS[s.status])}>
              {STATUS_ICON[s.status]}
            </span>
            <span className="truncate">{s.title}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Full proposal modal ───────────────────────────────────────────────────────

function ProposalModal({
  sections,
  drafts,
  onClose,
}: {
  sections: ProposalSection[];
  drafts: Record<string, string>;
  onClose: () => void;
}) {
  const text = buildFullProposal(sections, drafts);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-2xl border border-border w-full max-w-2xl max-h-[80vh] flex flex-col shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="font-heading text-base font-bold text-content-primary">Proposta completa</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigator.clipboard.writeText(text)}
              className="text-xs font-sans text-primary hover:underline"
            >
              Copiar tudo
            </button>
            <button
              onClick={onClose}
              className="ml-3 text-content-secondary hover:text-content-primary transition-colors text-lg leading-none"
            >
              ×
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-5">
          {text ? (
            <pre className="whitespace-pre-wrap font-sans text-sm text-content-primary leading-relaxed">
              {text}
            </pre>
          ) : (
            <p className="text-sm text-content-secondary font-sans text-center py-8">
              Nenhuma seção com rascunho ainda.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Checklist panel ───────────────────────────────────────────────────────────

const CHECKLIST_STATUS_ICON: Record<string, string> = {
  pending:         "⬜",
  addressed:       "✅",
  not_applicable:  "–",
};

function ChecklistPanel({
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
  const done = items.filter(i => i.status === "addressed").length;
  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
        <div>
          <p className="text-xs font-semibold text-content-secondary font-sans uppercase tracking-wide">Requisitos</p>
          <p className="text-[10px] text-content-secondary font-sans">{done}/{items.length} cobertos</p>
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
        {items.map(item => (
          <button
            key={item.id}
            onClick={() => onToggle(item.id, item.status === "addressed" ? "pending" : "addressed")}
            className="w-full text-left px-4 py-2 flex items-start gap-2 hover:bg-gray-50 transition-colors group"
          >
            <span className="text-xs shrink-0 mt-0.5">{CHECKLIST_STATUS_ICON[item.status]}</span>
            <div className="min-w-0">
              <p className={cn(
                "text-xs font-sans leading-snug",
                item.status === "addressed" ? "text-content-secondary line-through" : "text-content-primary"
              )}>
                {item.requirement}
              </p>
              {item.reason && (
                <p className="text-[10px] text-content-secondary font-sans mt-0.5">{item.reason}</p>
              )}
              <p className="text-[10px] text-content-secondary/60 font-sans">{item.section}</p>
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

// ── Storage helpers ───────────────────────────────────────────────────────────

function storageKey(editalId: string) {
  return `writing_session_${editalId}`;
}

interface PersistedState {
  sessionId: string;
  sections: ProposalSection[];
  sectionHistories: Record<string, WritingMessage[]>;
  sectionDrafts: Record<string, string>;
  activeSection: string | null;
}

function loadState(editalId: string): PersistedState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(storageKey(editalId));
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function saveState(editalId: string, state: PersistedState) {
  try {
    sessionStorage.setItem(storageKey(editalId), JSON.stringify(state));
  } catch { /* ignore */ }
}

// ── Page ──────────────────────────────────────────────────────────────────────

function WritingPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const editalId = searchParams.get("edital");
  const { getToken } = useAuth();
  const [authToken, setAuthToken] = useState<string | null>(null);
  useEffect(() => {
    getToken().then(setAuthToken);
  }, [getToken]);

  const profile = loadProfileFromStorage() ?? EMPTY_PROFILE;

  // Guard: sem perfil → onboarding
  useEffect(() => {
    if (!loadProfileFromStorage()) {
      router.replace(`/onboarding${editalId ? `?next=/chat?edital=${editalId}` : ""}`);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sections, setSections] = useState<ProposalSection[]>([]);
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [sectionHistories, setSectionHistories] = useState<Record<string, WritingMessage[]>>({});
  const [sectionDrafts, setSectionDrafts] = useState<Record<string, string>>({});

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [sectionLoading, setSectionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showModal, setShowModal] = useState(false);
  // Checklist state
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [checklistLoaded, setChecklistLoaded] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [showChecklist, setShowChecklist] = useState(false);

  async function loadChecklist() {
    if (!sessionId || checklistLoaded) return;
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/writing/${sessionId}/checklist`);
      if (res.ok) {
        const data = await res.json();
        setChecklist(data.items ?? []);
        setChecklistLoaded(true);
      }
    } catch { /* silently ignore */ }
  }

  async function handleChecklistToggle(id: string, status: ChecklistItem["status"]) {
    if (!sessionId) return;
    setChecklist(prev => prev.map(i => i.id === id ? { ...i, status } : i));
    await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/writing/${sessionId}/checklist/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: id, status }),
    });
  }

  async function handleAutoReview() {
    if (!sessionId) return;
    setReviewing(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/writing/${sessionId}/checklist/auto-review`, {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        setChecklist(data.items ?? []);
      }
    } finally {
      setReviewing(false);
    }
  }

  // Load checklist when session is ready
  useEffect(() => {
    if (sessionId && !checklistLoaded) loadChecklist();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Document editor state
  const [docContents, setDocContents] = useState<Record<string, string>>({});
  const [docSaving, setDocSaving] = useState(false);

  const activeDocContent = activeSection ? (docContents[activeSection] ?? sectionDrafts[activeSection] ?? "") : "";

  function handleDocChange(value: string) {
    if (!activeSection) return;
    setDocContents((prev) => ({ ...prev, [activeSection]: value }));
  }

  async function handleDocSave() {
    if (!sessionId || !activeSection) return;
    setDocSaving(true);
    try {
      await saveDocumentSection(sessionId, activeSection, activeDocContent);
    } finally {
      setDocSaving(false);
    }
  }

  async function handleExport() {
    if (!sessionId) return;
    const result = await exportDocument(sessionId);
    navigator.clipboard.writeText(result.markdown);
  }

  const bottomRef = useRef<HTMLDivElement>(null);
  const messages = activeSection ? (sectionHistories[activeSection] ?? []) : [];

  // Persist state on changes
  useEffect(() => {
    if (!editalId || !sessionId) return;
    saveState(editalId, { sessionId, sections, sectionHistories, sectionDrafts, activeSection });
  }, [editalId, sessionId, sections, sectionHistories, sectionDrafts, activeSection]);

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading, sectionLoading]);

  // Auto-start session
  useEffect(() => {
    if (!editalId || sessionId) return;

    // Try restoring from sessionStorage
    const saved = loadState(editalId);
    if (saved?.sessionId) {
      setSessionId(saved.sessionId);
      setSections(saved.sections);
      setSectionHistories(saved.sectionHistories);
      setSectionDrafts(saved.sectionDrafts);
      setActiveSection(saved.activeSection);
      return;
    }

    setInitializing(true);
    setError(null);
    startWritingSession(editalId, profile)
      .then((res) => {
        setSessionId(res.session_id);
        const proposalSections = getProposalSections(res.section_titles ?? []);
        setSections(proposalSections);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Erro ao iniciar sessão"))
      .finally(() => setInitializing(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editalId]);

  const handleSelectSection = useCallback(async (title: string) => {
    if (!sessionId || title === activeSection) return;
    setActiveSection(title);

    // Se a seção não tem histórico ainda, busca mensagem inicial
    if (!sectionHistories[title]) {
      setSectionLoading(true);
      try {
        const res = await startSectionChat(sessionId, title);
        const starterMsg: WritingMessage = {
          role: "assistant",
          content: res.starter_message,
          timestamp: new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }),
        };
        setSectionHistories((prev) => ({ ...prev, [title]: [starterMsg] }));
        // Marca seção como "draft" ao entrar pela primeira vez
        setSections((prev) =>
          prev.map((s) => s.title === title && s.status === "pending" ? { ...s, status: "draft" } : s)
        );
      } catch {
        setSectionHistories((prev) => ({ ...prev, [title]: [] }));
      } finally {
        setSectionLoading(false);
      }
    }
  }, [sessionId, activeSection, sectionHistories]);

  async function handleSend() {
    if (!input.trim() || !sessionId || !activeSection || loading) return;

    const userMsg: WritingMessage = {
      role: "user",
      content: input.trim(),
      timestamp: new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }),
    };
    setSectionHistories((prev) => ({
      ...prev,
      [activeSection]: [...(prev[activeSection] ?? []), userMsg],
    }));
    setInput("");
    setLoading(true);

    try {
      const res = await sendWritingTurn(sessionId, userMsg.content, activeSection);
      const draft = res.draft_content ?? null;
      const assistantMsg: WritingMessage = {
        role: "assistant",
        content: res.assistant_message,
        timestamp: new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }),
        draftSaved: !!draft,
      };
      setSectionHistories((prev) => ({
        ...prev,
        [activeSection]: [...(prev[activeSection] ?? []), assistantMsg],
      }));
      // Grantable: o backend já persistiu o draft em section_drafts (DB).
      // Aqui só refletimos no estado local — editor + modal/export.
      if (draft) {
        setDocContents((prev) => ({ ...prev, [activeSection]: draft }));
        setSectionDrafts((prev) => ({ ...prev, [activeSection]: draft }));
      }
      setSections((prev) =>
        prev.map((s) => s.title === activeSection && s.status === "pending" ? { ...s, status: "draft" } : s)
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao enviar mensagem");
    } finally {
      setLoading(false);
    }
  }

  function handleMarkReviewed() {
    if (!activeSection) return;
    setSections((prev) =>
      prev.map((s) => s.title === activeSection ? { ...s, status: "reviewed" } : s)
    );
  }

  const activeStatus = sections.find((s) => s.title === activeSection)?.status;

  return (
    <DashboardLayout title="Sessão de Escrita">
      {showModal && (
        <ProposalModal
          sections={sections}
          drafts={sectionDrafts}
          onClose={() => setShowModal(false)}
        />
      )}

      <div className="flex gap-0 h-[calc(100vh-130px)] rounded-xl border border-border overflow-hidden bg-white">

        {/* ── Left: checklist ──────────────────────────────────────────── */}
        <div className="w-48 shrink-0 border-r border-border flex flex-col">
          {sections.length > 0 ? (
            <>
              <SectionChecklist
                sections={sections}
                active={activeSection}
                onSelect={handleSelectSection}
              />
              <div className="p-3 border-t border-border space-y-1">
                <button
                  onClick={() => setShowChecklist(c => !c)}
                  className={cn(
                    "w-full text-xs font-sans text-center transition-colors py-1",
                    showChecklist ? "text-primary font-medium" : "text-content-secondary hover:text-content-primary"
                  )}
                >
                  {showChecklist ? "← Seções" : "Requisitos ✓"}
                </button>
                <button
                  onClick={() => setShowModal(true)}
                  className="w-full text-xs font-sans text-center text-primary hover:text-primary-hover transition-colors py-1"
                >
                  Ver proposta →
                </button>
                <button
                  onClick={handleExport}
                  className="w-full text-xs font-sans text-center text-content-secondary hover:text-content-primary transition-colors py-1"
                >
                  Copiar Markdown
                </button>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center p-4">
              {initializing ? (
                <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
              ) : (
                <p className="text-xs text-content-secondary font-sans text-center">
                  {editalId ? "Carregando seções..." : "Selecione um edital"}
                </p>
              )}
            </div>
          )}
        </div>

        {/* ── Checklist overlay (replaces checklist sidebar) */}
        {showChecklist && (
          <div className="w-72 shrink-0 border-r border-border flex flex-col bg-white">
            <ChecklistPanel
              items={checklist}
              onToggle={handleChecklistToggle}
              onAutoReview={handleAutoReview}
              reviewing={reviewing}
            />
          </div>
        )}

        {/* ── Middle: chat ─────────────────────────────────────────────── */}
        <div className="flex-[2] border-r border-border flex flex-col min-w-0">
          {/* Chat header */}
          <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
            <div>
              <p className="text-sm font-semibold text-content-primary font-sans">
                {activeSection ?? (editalId ? "Selecione uma seção" : "Nenhum edital")}
              </p>
              {!editalId && (
                <a href="/editais" className="text-xs text-primary font-sans hover:underline">
                  Selecionar edital →
                </a>
              )}
            </div>
            {activeSection && activeStatus === "draft" && (
              <button
                onClick={handleMarkReviewed}
                className="text-xs font-sans text-content-secondary border border-border rounded-lg px-2.5 py-1 hover:border-green-400 hover:text-green-600 transition-colors"
              >
                Concluída ✓
              </button>
            )}
            {activeSection && activeStatus === "reviewed" && (
              <span className="text-xs font-sans text-green-600">✓ Concluída</span>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="mx-4 mt-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
              {error}
            </div>
          )}

          {/* Empty state */}
          {!activeSection && !initializing && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center max-w-xs px-6">
                <p className="font-heading text-base font-bold text-content-primary mb-2">
                  {editalId ? "Escolha uma seção" : "Nenhum edital selecionado"}
                </p>
                <p className="text-sm text-content-secondary font-sans">
                  {editalId
                    ? "Clique em uma seção no painel à esquerda para começar a escrever."
                    : "Acesse um edital e clique em \"Iniciar Escrita\"."}
                </p>
              </div>
            </div>
          )}

          {/* Messages */}
          {activeSection && (
            <>
              <div className="flex-1 overflow-y-auto space-y-3 p-4 pb-2">
                {sectionLoading && (
                  <div className="flex justify-start">
                    <div className="bg-white border border-border rounded-2xl rounded-bl-sm px-4 py-3">
                      <TypingDots />
                    </div>
                  </div>
                )}
                {messages.map((msg, i) => (
                  <MessageBubble key={i} msg={msg} />
                ))}
                {loading && (
                  <div className="flex justify-start">
                    <div className="bg-white border border-border rounded-2xl rounded-bl-sm px-4 py-3">
                      <TypingDots />
                    </div>
                  </div>
                )}
                <div ref={bottomRef} />
              </div>

              {/* Input */}
              <div className="p-3 border-t border-border flex gap-2">
                <MentionsTextarea
                  rows={2}
                  value={input}
                  onChange={setInput}
                  onSubmitEnter={handleSend}
                  token={authToken}
                  placeholder="Escreva sua mensagem... (@ para mencionar biblioteca, Enter para enviar)"
                  className={cn(
                    "w-full rounded-xl border border-border px-3 py-2.5 text-sm font-sans",
                    "text-content-primary placeholder:text-content-secondary resize-none",
                    "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary"
                  )}
                  disabled={!sessionId || loading}
                />
                <button
                  onClick={handleSend}
                  disabled={!sessionId || loading || !input.trim()}
                  className={cn(
                    "self-end px-4 py-2.5 rounded-xl bg-primary text-white text-sm font-semibold font-sans",
                    "hover:bg-primary-hover transition-colors",
                    "disabled:opacity-50 disabled:cursor-not-allowed"
                  )}
                >
                  Enviar
                </button>
              </div>
            </>
          )}
        </div>

        {/* ── Right: document editor ───────────────────────────────────── */}
        <div className="flex-[3] flex flex-col min-w-0">
          <DocumentEditor
            sectionTitle={activeSection}
            content={activeDocContent}
            onChange={handleDocChange}
            onSave={handleDocSave}
            saving={docSaving}
          />
        </div>
      </div>
    </DashboardLayout>
  );
}

export default function WritingPage() {
  return (
    <Suspense fallback={null}>
      <WritingPageInner />
    </Suspense>
  );
}
