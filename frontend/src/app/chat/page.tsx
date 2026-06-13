"use client";

// Página de escrita (chat-first, spec_frontend_chat_first Fase 3). Reescreve o
// antigo 3-pane (checklist | chat | editor) no layout split-pane:
//
//   ┌──────────┬─────────────────────┬──────────────────────┐
//   │ Conversa │  Chat da proposta   │  Canvas (painel dir.)│
//   │ Sidebar  │  bolhas + composer  │  abas: Documento |   │
//   │ (oculto  │  (@ mentions, model │  Checklist           │
//   │  <md)    │  tier, pending)     │  (oculto <lg)        │
//   └──────────┴─────────────────────┴──────────────────────┘
//
// Mesmo shell da home (ConversationSidebar, h-[100dvh], sem DashboardLayout). O
// canvas é colapsável por um botão no header do chat. Contrato de URL inalterado:
// /chat?edital={id}; sem edital (ou sem perfil) redireciona para "/".

import { Suspense, useState, useEffect, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  startWritingSession,
  sendWritingTurn,
  startSectionChat,
  saveDocumentSection,
  exportDocument,
  getWritingDocument,
  type ModelTier,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  getProposalSections,
  type ProposalSection,
} from "@/lib/writing";
import { loadProfileFromStorage, EMPTY_PROFILE } from "@/types/profile";
import type { WritingMessage, PendingUserInput } from "@/types/api";
import { ConversationSidebar } from "@/components/layout/ConversationSidebar";
import { ModelTierSelector } from "@/components/ui/ModelTierSelector";
import { MentionsTextarea } from "@/components/ui/MentionsTextarea";
import { PendingUserInputPrompt } from "@/components/writing/PendingUserInputPrompt";
import { AttachToLibrary } from "@/components/writing/AttachToLibrary";
import { DocumentCanvas } from "@/components/writing/DocumentCanvas";
import type { ChecklistItem } from "@/components/writing/ChecklistPanel";
import { ChatBubble } from "@/components/chat/ChatBubble";
import { ChatMessageList } from "@/components/chat/ChatMessageList";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { useAuth } from "@/lib/auth";

const MODEL_TIER_STORAGE_KEY = "radar:model-tier";

function loadInitialTier(): ModelTier {
  if (typeof window === "undefined") return "auto";
  const stored = window.localStorage.getItem(MODEL_TIER_STORAGE_KEY);
  return stored === "fast" || stored === "pro" || stored === "auto" ? stored : "auto";
}

// ── Bolha de mensagem (mantém o footer "↗ Salvo no documento") ─────────────────
function MessageBubble({ msg }: { msg: WritingMessage }) {
  const isUser = msg.role === "user";
  return (
    <ChatBubble
      role={msg.role}
      timestamp={msg.timestamp}
      footer={
        !isUser && msg.draftSaved ? (
          <span className="mt-1 text-[10px] font-sans text-green-600 px-1">
            ↗ Salvo no documento
          </span>
        ) : undefined
      }
    >
      <pre className="whitespace-pre-wrap font-sans text-inherit">{msg.content}</pre>
    </ChatBubble>
  );
}

// ── Persistência local (sessionStorage) — inalterada vs. /chat antiga ──────────
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
  } catch {
    return null;
  }
}

function saveState(editalId: string, state: PersistedState) {
  try {
    sessionStorage.setItem(storageKey(editalId), JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ── Página ──────────────────────────────────────────────────────────────────
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

  // Contrato de URL inalterado: /chat?edital={id} é o fluxo de escrita; sem
  // edital (ou sem perfil) mandamos para o front-door "/" (onde se constrói o
  // perfil). Roda só uma vez na montagem.
  useEffect(() => {
    if (!editalId || !loadProfileFromStorage()) {
      router.replace("/");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sections, setSections] = useState<ProposalSection[]>([]);
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [sectionHistories, setSectionHistories] = useState<
    Record<string, WritingMessage[]>
  >({});
  const [sectionDrafts, setSectionDrafts] = useState<Record<string, string>>({});

  const [input, setInput] = useState("");
  const [modelTier, setModelTier] = useState<ModelTier>(loadInitialTier);

  const handleTierChange = useCallback((tier: ModelTier) => {
    setModelTier(tier);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MODEL_TIER_STORAGE_KEY, tier);
    }
  }, []);

  const [loading, setLoading] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [sectionLoading, setSectionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Canvas: visível por padrão em desktop, colapsável pelo header. Oculto <lg.
  const [canvasOpen, setCanvasOpen] = useState(true);

  // ── Checklist ───────────────────────────────────────────────────────────
  const [checklist, setChecklist] = useState<ChecklistItem[]>([]);
  const [checklistLoaded, setChecklistLoaded] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  const loadChecklist = useCallback(async () => {
    if (!sessionId || checklistLoaded) return;
    try {
      const res = await fetch(`${API_BASE}/writing/${sessionId}/checklist`);
      if (res.ok) {
        const data = await res.json();
        setChecklist(data.items ?? []);
        setChecklistLoaded(true);
      }
    } catch {
      /* silently ignore */
    }
  }, [sessionId, checklistLoaded]);

  async function handleChecklistToggle(id: string, status: ChecklistItem["status"]) {
    if (!sessionId) return;
    setChecklist((prev) => prev.map((i) => (i.id === id ? { ...i, status } : i)));
    await fetch(`${API_BASE}/writing/${sessionId}/checklist/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: id, status }),
    });
  }

  async function handleAutoReview() {
    if (!sessionId) return;
    setReviewing(true);
    try {
      const res = await fetch(
        `${API_BASE}/writing/${sessionId}/checklist/auto-review`,
        { method: "POST" }
      );
      if (res.ok) {
        const data = await res.json();
        setChecklist(data.items ?? []);
      }
    } finally {
      setReviewing(false);
    }
  }

  // Carrega a checklist quando a sessão fica pronta.
  useEffect(() => {
    if (sessionId && !checklistLoaded) void loadChecklist();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // ── Editor de documento ───────────────────────────────────────────────────
  const [docContents, setDocContents] = useState<Record<string, string>>({});
  const [docSaving, setDocSaving] = useState(false);
  // Sprint 2 do Cenário B: setado pela tool request_user_info (path agente).
  // Renderiza prompt destacado acima do composer; limpo no próximo turn.
  const [pendingUserInput, setPendingUserInput] =
    useState<PendingUserInput | null>(null);

  const activeDocContent = activeSection
    ? docContents[activeSection] ?? sectionDrafts[activeSection] ?? ""
    : "";
  // "não salvo": o texto local diverge do último salvo no DB (sectionDrafts).
  const dirty = activeSection
    ? activeDocContent !== (sectionDrafts[activeSection] ?? "")
    : false;

  function handleDocChange(value: string) {
    if (!activeSection) return;
    setDocContents((prev) => ({ ...prev, [activeSection]: value }));
  }

  async function handleDocSave() {
    if (!sessionId || !activeSection) return;
    setDocSaving(true);
    try {
      await saveDocumentSection(sessionId, activeSection, activeDocContent);
      // Sincroniza o "salvo no DB" local para o indicador de não-salvo zerar.
      setSectionDrafts((prev) => ({ ...prev, [activeSection]: activeDocContent }));
      setSections((prev) =>
        prev.map((s) =>
          s.title === activeSection && s.status === "pending"
            ? { ...s, status: "draft" }
            : s
        )
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Erro ao salvar a seção.");
    } finally {
      setDocSaving(false);
    }
  }

  async function handleExport() {
    if (!sessionId) return;
    try {
      const result = await exportDocument(sessionId);
      await navigator.clipboard.writeText(result.markdown);
      toast.success("Markdown copiado.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Erro ao exportar.");
    }
  }

  const messages = activeSection ? sectionHistories[activeSection] ?? [] : [];

  // Persiste o estado a cada mudança (sessionStorage por edital).
  useEffect(() => {
    if (!editalId || !sessionId) return;
    saveState(editalId, {
      sessionId,
      sections,
      sectionHistories,
      sectionDrafts,
      activeSection,
    });
  }, [editalId, sessionId, sections, sectionHistories, sectionDrafts, activeSection]);

  // ── Auto-start da sessão (valida cache stale no backend antes de confiar) ───
  useEffect(() => {
    if (!editalId || sessionId) return;
    let cancelled = false;

    async function init() {
      const saved = loadState(editalId!);
      if (saved?.sessionId) {
        try {
          const doc = await getWritingDocument(saved.sessionId);
          if (cancelled) return;
          // Backend é a fonte de verdade do texto salvo (section_drafts).
          const byTitle: Record<string, string> = {};
          for (const s of doc.sections) if (s.content) byTitle[s.title] = s.content;
          setSessionId(saved.sessionId);
          setSections(saved.sections);
          setSectionHistories(saved.sectionHistories);
          setDocContents(byTitle);
          setSectionDrafts(byTitle);
          setActiveSection(saved.activeSection);
          return;
        } catch {
          // 404/erro → cache stale; limpa e cai pro fluxo de criação.
          try {
            sessionStorage.removeItem(storageKey(editalId!));
          } catch {
            /* noop */
          }
        }
      }

      if (cancelled) return;
      setInitializing(true);
      setError(null);
      try {
        const res = await startWritingSession(editalId!, profile);
        if (cancelled) return;
        setSessionId(res.session_id);
        setSections(getProposalSections(res.section_titles ?? []));
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Erro ao iniciar sessão");
      } finally {
        if (!cancelled) setInitializing(false);
      }
    }

    init();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editalId]);

  // ── Seleção de seção → dirige o chat de seção (section-start) ───────────────
  const handleSelectSection = useCallback(
    async (title: string) => {
      if (!sessionId || title === activeSection) return;
      setActiveSection(title);
      // Em telas pequenas (canvas escondido), selecionar uma seção deixa o foco
      // no chat — não força abrir o canvas.

      if (!sectionHistories[title]) {
        setSectionLoading(true);
        try {
          const res = await startSectionChat(sessionId, title);
          const starterMsg: WritingMessage = {
            role: "assistant",
            content: res.starter_message,
            timestamp: new Date().toLocaleTimeString("pt-BR", {
              hour: "2-digit",
              minute: "2-digit",
            }),
          };
          setSectionHistories((prev) => ({ ...prev, [title]: [starterMsg] }));
          // Marca a seção como "draft" ao entrar pela primeira vez.
          setSections((prev) =>
            prev.map((s) =>
              s.title === title && s.status === "pending"
                ? { ...s, status: "draft" }
                : s
            )
          );
        } catch {
          setSectionHistories((prev) => ({ ...prev, [title]: [] }));
        } finally {
          setSectionLoading(false);
        }
      }
    },
    [sessionId, activeSection, sectionHistories]
  );

  async function handleSend(messageOverride?: string) {
    const content = (messageOverride ?? input).trim();
    if (!content || !sessionId || !activeSection || loading) return;

    const userMsg: WritingMessage = {
      role: "user",
      content,
      timestamp: new Date().toLocaleTimeString("pt-BR", {
        hour: "2-digit",
        minute: "2-digit",
      }),
    };
    setSectionHistories((prev) => ({
      ...prev,
      [activeSection]: [...(prev[activeSection] ?? []), userMsg],
    }));
    // Limpa apenas o input principal (não o override programático).
    if (messageOverride === undefined) setInput("");
    // O turn vai consumir/atualizar pending_user_input — limpamos local agora e
    // deixamos o response repopular se houver nova pergunta.
    setPendingUserInput(null);
    setLoading(true);

    try {
      const res = await sendWritingTurn(
        sessionId,
        userMsg.content,
        activeSection,
        modelTier
      );
      // Fluxo de agente: a seção é persistida via a tool save_draft (side
      // effect) e draft_content vem SEMPRE null. Re-buscamos o documento para
      // refletir o que foi salvo (o agente pode normalizar o título).
      const savedThisTurn = (res.tool_trace ?? []).some(
        (t) => t.name === "save_draft" && t.output.startsWith("Rascunho salvo")
      );
      const assistantMsg: WritingMessage = {
        role: "assistant",
        content: res.assistant_message,
        timestamp: new Date().toLocaleTimeString("pt-BR", {
          hour: "2-digit",
          minute: "2-digit",
        }),
        draftSaved: savedThisTurn,
      };
      setSectionHistories((prev) => ({
        ...prev,
        [activeSection]: [...(prev[activeSection] ?? []), assistantMsg],
      }));
      try {
        const doc = await getWritingDocument(sessionId);
        const byTitle: Record<string, string> = {};
        for (const s of doc.sections) if (s.content) byTitle[s.title] = s.content;
        setDocContents(byTitle);
        setSectionDrafts(byTitle);
        setSections((prev) =>
          prev.map((s) =>
            byTitle[s.title] && s.status === "pending"
              ? { ...s, status: "draft" }
              : s
          )
        );
      } catch {
        // Re-busca falhou — mantém estado local; o texto continua salvo no DB.
      }
      // Path agente: pode ter pedido info ao usuário no fim deste turn.
      if (res.pending_user_input) setPendingUserInput(res.pending_user_input);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao enviar mensagem");
    } finally {
      setLoading(false);
    }
  }

  function handleMarkReviewed() {
    if (!activeSection) return;
    setSections((prev) =>
      prev.map((s) =>
        s.title === activeSection ? { ...s, status: "reviewed" } : s
      )
    );
  }

  const activeStatus = sections.find((s) => s.title === activeSection)?.status;

  return (
    // Mesmo shell da home: ConversationSidebar à esquerda (oculto <md), página
    // full-height, sem DashboardLayout.
    <div className="flex h-[100dvh] bg-app-bg">
      <div className="hidden md:flex">
        <ConversationSidebar />
      </div>

      {/* ── Centro: chat da proposta ───────────────────────────────────────── */}
      <div className="flex flex-1 flex-col min-w-0 border-r border-border bg-white">
        {/* Header do chat */}
        <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0 gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-content-primary font-sans truncate">
              {activeSection ?? (editalId ? "Selecione uma seção" : "Nenhum edital")}
            </p>
            {!editalId && (
              <a
                href="/editais"
                className="text-xs text-primary font-sans hover:underline"
              >
                Selecionar edital →
              </a>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {/* Seletor de seção compacto para <lg, onde o canvas (e seu
                seletor) fica oculto — caso contrário não haveria como trocar de
                seção no mobile. Em ≥lg usa-se o seletor da aba Documento. */}
            {sections.length > 0 && (
              <select
                value={activeSection ?? ""}
                onChange={(e) => {
                  if (e.target.value) void handleSelectSection(e.target.value);
                }}
                className="lg:hidden text-xs font-sans rounded-lg border border-border bg-white px-2 py-1 text-content-primary max-w-[9rem] focus:outline-none focus:ring-2 focus:ring-primary/40"
                aria-label="Selecionar seção"
              >
                <option value="" disabled>
                  Seção…
                </option>
                {sections.map((s) => (
                  <option key={s.title} value={s.title}>
                    {s.title}
                  </option>
                ))}
              </select>
            )}
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
            {/* Colapsar/expandir o canvas (só faz sentido em ≥lg). */}
            <button
              onClick={() => setCanvasOpen((c) => !c)}
              className="hidden lg:inline-flex items-center gap-1.5 text-xs font-sans text-content-secondary border border-border rounded-lg px-2.5 py-1 hover:text-content-primary hover:border-content-secondary transition-colors"
              aria-pressed={canvasOpen}
              title={canvasOpen ? "Ocultar documento" : "Mostrar documento"}
            >
              {canvasOpen ? "Ocultar documento" : "Mostrar documento"}
            </button>
          </div>
        </div>

        {/* Erro */}
        {error && (
          <div className="mx-4 mt-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
            {error}
          </div>
        )}

        {/* Estado vazio (sem seção ativa) */}
        {!activeSection && !initializing && (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center max-w-xs px-6">
              <p className="font-heading text-base font-bold text-content-primary mb-2">
                {editalId ? "Escolha uma seção" : "Nenhum edital selecionado"}
              </p>
              <p className="text-sm text-content-secondary font-sans">
                {editalId
                  ? "Selecione uma seção (painel de documento à direita, ou o seletor no topo) para começar a escrever."
                  : 'Acesse um edital e clique em "Começar proposta".'}
              </p>
            </div>
          </div>
        )}

        {/* Mensagens + composer */}
        {activeSection && (
          <>
            <ChatMessageList deps={[loading, sectionLoading]}>
              {sectionLoading && (
                <div className="flex justify-start">
                  <div className="bg-white border border-border rounded-2xl rounded-bl-sm px-4 py-3">
                    <TypingIndicator />
                  </div>
                </div>
              )}
              {messages.map((msg, i) => (
                <MessageBubble key={i} msg={msg} />
              ))}
              {loading && (
                <div className="flex justify-start">
                  <div className="bg-white border border-border rounded-2xl rounded-bl-sm px-4 py-3">
                    <TypingIndicator />
                  </div>
                </div>
              )}
            </ChatMessageList>

            {/* Composer */}
            <div className="p-3 border-t border-border space-y-2 shrink-0">
              {pendingUserInput && (
                <PendingUserInputPrompt
                  pending={pendingUserInput}
                  onAnswer={(answer) => {
                    void handleSend(answer);
                  }}
                  disabled={loading}
                />
              )}
              <div className="flex items-center justify-between">
                <ModelTierSelector
                  value={modelTier}
                  onChange={handleTierChange}
                  disabled={loading}
                />
              </div>
              <div className="flex gap-2">
                {/* 📎 Anexa arquivo à biblioteca (mesmo caminho da /library); fica
                    disponível para @ menção logo após o upload. */}
                <AttachToLibrary
                  token={authToken}
                  disabled={!sessionId || loading}
                  onAttached={(item) =>
                    setInput((prev) =>
                      prev.endsWith(" ") || prev === ""
                        ? `${prev}@${item.id} `
                        : `${prev} @${item.id} `
                    )
                  }
                />
                <MentionsTextarea
                  rows={2}
                  value={input}
                  onChange={setInput}
                  onSubmitEnter={() => {
                    void handleSend();
                  }}
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
                  onClick={() => {
                    void handleSend();
                  }}
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
            </div>
          </>
        )}
      </div>

      {/* ── Direita: canvas (Documento | Checklist) ────────────────────────── */}
      {canvasOpen && (
        <div className="hidden lg:flex w-[28rem] shrink-0 flex-col bg-white">
          <DocumentCanvas
            sections={sections}
            activeSection={activeSection}
            onSelectSection={handleSelectSection}
            docContent={activeDocContent}
            onDocChange={handleDocChange}
            onSave={handleDocSave}
            saving={docSaving}
            dirty={dirty}
            drafts={sectionDrafts}
            onExport={handleExport}
            initializing={initializing}
            hasEdital={!!editalId}
            checklist={checklist}
            onChecklistToggle={handleChecklistToggle}
            onAutoReview={handleAutoReview}
            reviewing={reviewing}
          />
        </div>
      )}
    </div>
  );
}

export default function WritingPage() {
  return (
    <Suspense fallback={null}>
      <WritingPageInner />
    </Suspense>
  );
}
