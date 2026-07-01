"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import {
  getWritingDocument,
  sendWritingTurn,
  saveDocumentSection,
  getLibraryItems,
  getEditalById,
  autoReviewChecklist,
} from "@/lib/api";
import type {
  ContentItemSummary,
  PendingUserInput,
  WritingMode,
} from "@/types/api";
import { cn } from "@/lib/utils";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { Explorer } from "@/components/workspace/Explorer";
import { DocumentEditor } from "@/components/workspace/DocumentEditor";
import { ExportModal } from "@/components/workspace/ExportModal";
import {
  WorkspaceChat,
  type WorkspaceChatHandle,
} from "@/components/workspace/WorkspaceChat";
import {
  filledCount,
  flattenReview,
  groupBySection,
  modeFromEditalId,
  type Finding,
  type WorkspaceMessage,
  type WorkspaceSection,
} from "@/components/workspace/types";

function nowTime(): string {
  return new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

export default function WorkspacePage() {
  const params = useParams();
  const sessionId = Array.isArray(params.sessionId) ? params.sessionId[0] : params.sessionId;
  const { getToken, loading: authLoading } = useAuth();

  const [token, setToken] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  const [sections, setSections] = useState<WorkspaceSection[]>([]);
  const [editalId, setEditalId] = useState<string>("");
  const [mode, setMode] = useState<WritingMode>("proposal");
  const [targetTitle, setTargetTitle] = useState<string>("");
  const [attachments, setAttachments] = useState<ContentItemSummary[]>([]);

  const [messages, setMessages] = useState<WorkspaceMessage[]>([]);
  const [input, setInput] = useState("");
  const [working, setWorking] = useState(false);
  const [pending, setPending] = useState<PendingUserInput | null>(null);
  const [savingSection, setSavingSection] = useState<string | null>(null);

  const [loadError, setLoadError] = useState<string | null>(null);
  const [docLoading, setDocLoading] = useState(true);
  const [mobileTab, setMobileTab] = useState<"doc" | "chat">("chat");
  const [mobileDrawer, setMobileDrawer] = useState(false);

  // Chat-first UX: começa sem documento; após gerar draft, mostra o split view.
  const [draftReady, setDraftReady] = useState(false);

  const [findings, setFindings] = useState<Finding[]>([]);
  const [reviewing, setReviewing] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);

  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());
  const undoSnapshots = useRef<Record<string, string>>({});

  const chatRef = useRef<WorkspaceChatHandle>(null);
  const scrollToSectionRef = useRef<(title: string) => void>(() => {});

  // ── Auth ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (authLoading) return;
    getToken().then((t) => {
      setToken(t);
      setAuthChecked(true);
    });
  }, [authLoading, getToken]);

  // ── Carrega documento (fonte de verdade) + anexos ─────────────────────────
  const reloadDocument = useCallback(async (): Promise<{
    sections: WorkspaceSection[];
    editalId: string;
  }> => {
    if (!sessionId) return { sections: [], editalId: "" };
    const doc = await getWritingDocument(sessionId);
    const next = doc.sections.map((s) => ({ title: s.title, content: s.content }));
    setSections(next);
    setEditalId(doc.edital_id);
    setMode(modeFromEditalId(doc.edital_id));
    return { sections: next, editalId: doc.edital_id };
  }, [sessionId]);

  useEffect(() => {
    if (!authChecked || !sessionId) return;
    let cancelled = false;
    (async () => {
      setDocLoading(true);
      setLoadError(null);
      try {
        const { sections: next, editalId: id } = await reloadDocument();
        if (cancelled) return;
        if (!id.startsWith("investidor:")) {
          try {
            const card = await getEditalById(id);
            if (!cancelled && card.title) setTargetTitle(card.title);
          } catch {
            if (!cancelled) setTargetTitle(id);
          }
        } else if (!cancelled) {
          setTargetTitle(id);
        }

        // Se já houver conteúdo salvo, mostra o split view direto.
        const hasContent = next.some((s) => s.content.trim());
        if (!cancelled && hasContent) {
          setDraftReady(true);
        }

        // Mensagem inicial.
        if (!cancelled) {
          const welcome = hasContent
            ? "Pronto para revisar a proposta. Edite as seções à esquerda ou continue conversando no chat."
            : "Bem-vindo(a) ao workspace. Converse sobre o edital, tire dúvidas " +
              "ou peça um rascunho quando estiver pronto(a).";
          setMessages([{ role: "assistant", content: welcome, timestamp: nowTime() }]);
        }
      } catch (e) {
        if (!cancelled) {
          setLoadError(
            e instanceof Error ? e.message : "Não foi possível carregar a sessão.",
          );
        }
      } finally {
        if (!cancelled) setDocLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authChecked, sessionId, reloadDocument]);

  // Anexos: itens da biblioteca do workspace.
  useEffect(() => {
    if (!token) return;
    getLibraryItems(token).then(setAttachments).catch(() => setAttachments([]));
  }, [token]);

  // ── Explorer: navegação e anexos ──────────────────────────────────────────
  const handleSelectSection = useCallback((title: string) => {
    scrollToSectionRef.current(title);
    setMobileTab("doc");
  }, []);

  const handleSelectAttachment = useCallback((item: ContentItemSummary) => {
    chatRef.current?.appendToComposer(`@${item.id}`);
    setMobileTab("chat");
  }, []);

  const handleSectionInteract = useCallback((title: string) => {
    setHighlighted((prev) => {
      if (!prev.has(title)) return prev;
      const next = new Set(prev);
      next.delete(title);
      return next;
    });
  }, []);

  // ── Edição manual inline ─────────────────────────────────────────────────
  const handleSaveSection = useCallback(
    async (title: string, content: string) => {
      if (!sessionId) return;
      setSavingSection(title);
      setSections((prev) => prev.map((s) => (s.title === title ? { ...s, content } : s)));
      try {
        await saveDocumentSection(sessionId, title, content);
      } catch (e) {
        toast.error(
          e instanceof Error ? `Erro ao salvar: ${e.message}` : "Erro ao salvar a seção.",
        );
        try {
          await reloadDocument();
        } catch {
          /* ignore */
        }
      } finally {
        setSavingSection(null);
      }
    },
    [sessionId, reloadDocument],
  );

  // ── Undo de edição do agente ──────────────────────────────────────────────
  const handleUndoSection = useCallback(
    async (title: string) => {
      if (!sessionId) return;
      const prevContent = undoSnapshots.current[title] ?? "";
      setSavingSection(title);
      setSections((prev) => prev.map((s) => (s.title === title ? { ...s, content: prevContent } : s)));
      handleSectionInteract(title);
      try {
        await saveDocumentSection(sessionId, title, prevContent);
        delete undoSnapshots.current[title];
        toast.success(`Edição de "${title}" desfeita.`);
      } catch (e) {
        toast.error(e instanceof Error ? `Erro ao desfazer: ${e.message}` : "Erro ao desfazer.");
        try {
          await reloadDocument();
        } catch {
          /* ignore */
        }
      } finally {
        setSavingSection(null);
      }
    },
    [sessionId, handleSectionInteract, reloadDocument],
  );

  // ── Turno ─────────────────────────────────────────────────────────────────
  const runTurn = useCallback(
    async (text: string, sectionHint?: string) => {
      const content = text.trim();
      if (!content || !sessionId || working) return;

      setMessages((prev) => [...prev, { role: "user", content, timestamp: nowTime() }]);
      setInput("");
      setPending(null);
      setWorking(true);

      try {
        const res = await sendWritingTurn(sessionId, content, sectionHint);

        const editedSections = Array.from(
          new Set(
            (res.tool_trace ?? [])
              .filter((t) => t.name === "save_draft" && t.saved_section)
              .map((t) => t.saved_section as string),
          ),
        );

        setSections((prevSections) => {
          for (const title of editedSections) {
            const before = prevSections.find((s) => s.title === title)?.content ?? "";
            undoSnapshots.current[title] = before;
          }
          return prevSections;
        });

        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: res.assistant_message,
            timestamp: nowTime(),
            editedSections: editedSections.length > 0 ? editedSections : undefined,
            complianceFlags:
              (res.compliance_flags?.length ?? 0) > 0 ? res.compliance_flags : undefined,
          },
        ]);

        // Se um draft foi gerado, recarrega o documento e mostra o split view.
        if (res.draft_ready || (res.sections_done?.length ?? 0) > 0) {
          setDraftReady(true);
          setMobileTab("doc");
          try {
            await reloadDocument();
          } catch {
            /* mantém estado local */
          }
        } else {
          // Turno conversacional comum: só recarrega se houve edição.
          try {
            await reloadDocument();
          } catch {
            /* ignore */
          }
        }

        if (editedSections.length > 0) {
          setHighlighted((prev) => {
            const next = new Set(prev);
            editedSections.forEach((t) => next.add(t));
            return next;
          });
        }
        if (res.pending_user_input) setPending(res.pending_user_input);
      } catch (e) {
        toast.error(
          e instanceof Error ? e.message : "Erro ao enviar mensagem ao agente.",
        );
      } finally {
        setWorking(false);
      }
    },
    [sessionId, working, reloadDocument],
  );

  const registerScrollTo = useCallback((fn: (title: string) => void) => {
    scrollToSectionRef.current = fn;
  }, []);

  // ── Revisar ───────────────────────────────────────────────────────────────
  const handleReview = useCallback(async () => {
    if (!sessionId || !token || reviewing) return;
    setReviewing(true);
    try {
      const { review } = await autoReviewChecklist(sessionId, token);
      const flat = flattenReview(review);
      setFindings(flat);
      if (flat.length === 0) {
        toast.success("Revisão concluída — nenhuma observação.");
      }
      if (review.error && review.error.length > 0) {
        toast.warning("A revisão rodou com falhas parciais em algum dos passes.");
      }
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : "Não foi possível revisar o documento.",
      );
    } finally {
      setReviewing(false);
    }
  }, [sessionId, token, reviewing]);

  const handleFixWithAI = useCallback(
    (sectionHint: string | null, finding: Finding) => {
      const where = sectionHint ? `na seção "${sectionHint}"` : "na proposta";
      const suggestion = finding.suggestion
        ? `\nSugestão da revisão: ${finding.suggestion}`
        : "";
      const prompt =
        `A revisão apontou ${where}: ${finding.text}.${suggestion}\n` +
        `Corrija isso no documento.`;
      setMobileTab("chat");
      void runTurn(prompt, sectionHint ?? undefined);
    },
    [runTurn],
  );

  // ── Render ────────────────────────────────────────────────────────────────
  if (authChecked && !token) {
    return (
      <div className="h-screen flex items-center justify-center bg-app-bg">
        <p className="text-sm text-content-secondary font-sans">
          Faça login para abrir o workspace.
        </p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="h-screen flex items-center justify-center bg-app-bg px-6">
        <div className="text-center max-w-sm">
          <p className="text-sm text-content-primary font-sans mb-2">{loadError}</p>
          <a href="/sessions" className="text-sm text-primary font-sans hover:underline">
            ← Voltar para sessões
          </a>
        </div>
      </div>
    );
  }

  const filled = filledCount(sections);
  const grouped = groupBySection(findings);
  const generalFindings = grouped.get("Geral") ?? [];
  const findingsBySection = new Map(
    Array.from(grouped.entries()).filter(([k]) => k !== "Geral"),
  );
  const findingCounts = new Map(
    Array.from(grouped.entries()).map(([k, v]) => [k, v.length]),
  );

  return (
    <div className="h-screen flex flex-col bg-app-bg overflow-hidden">
      <WorkspaceHeader
        title={targetTitle || editalId || "Carregando…"}
        mode={mode}
        filled={filled}
        total={draftReady ? sections.length : 0}
      />

      {draftReady && (
        <>
          {/* Mobile: abas Documento | Chat + botão do drawer do explorer */}
          <div className="md:hidden flex items-center border-b border-border bg-white">
            <button
              onClick={() => setMobileDrawer(true)}
              title="Abrir explorer"
              className="px-3 py-2 text-content-secondary hover:text-content-primary transition-colors"
            >
              ☰
            </button>
            {(["doc", "chat"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setMobileTab(tab)}
                className={cn(
                  "flex-1 py-2 text-sm font-sans transition-colors",
                  mobileTab === tab
                    ? "text-primary font-semibold border-b-2 border-primary"
                    : "text-content-secondary",
                )}
              >
                {tab === "doc" ? "Documento" : "Chat"}
              </button>
            ))}
          </div>
        </>
      )}

      <div className="flex-1 flex min-h-0">
        {draftReady ? (
          <>
            {/* Explorer — sidebar no desktop */}
            <div className="hidden md:flex">
              <Explorer
                sections={sections}
                attachments={attachments}
                findingCounts={findingCounts}
                reviewing={reviewing}
                onSelectSection={handleSelectSection}
                onSelectAttachment={handleSelectAttachment}
                onReview={() => void handleReview()}
                onExport={() => setExportOpen(true)}
              />
            </div>

            {/* Explorer — drawer overlay no mobile */}
            {mobileDrawer && (
              <div className="md:hidden fixed inset-0 z-40 flex">
                <button
                  aria-label="Fechar explorer"
                  onClick={() => setMobileDrawer(false)}
                  className="absolute inset-0 bg-black/40"
                />
                <div className="relative z-10 h-full shadow-xl animate-in slide-in-from-left">
                  <Explorer
                    sections={sections}
                    attachments={attachments}
                    findingCounts={findingCounts}
                    reviewing={reviewing}
                    onSelectSection={(t) => {
                      handleSelectSection(t);
                      setMobileDrawer(false);
                    }}
                    onSelectAttachment={(item) => {
                      handleSelectAttachment(item);
                      setMobileDrawer(false);
                    }}
                    onReview={() => {
                      void handleReview();
                      setMobileDrawer(false);
                    }}
                    onExport={() => {
                      setExportOpen(true);
                      setMobileDrawer(false);
                    }}
                  />
                </div>
              </div>
            )}

            {/* Editor */}
            <div
              className={cn(
                "flex-1 min-w-0 flex flex-col",
                mobileTab === "chat" ? "hidden md:flex" : "flex",
              )}
            >
              {docLoading ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="w-5 h-5 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                </div>
              ) : (
                <DocumentEditor
                  sections={sections}
                  highlightedSections={highlighted}
                  savingSection={savingSection}
                  findingsBySection={findingsBySection}
                  generalFindings={generalFindings}
                  onSaveSection={handleSaveSection}
                  onSectionInteract={handleSectionInteract}
                  onFixWithAI={handleFixWithAI}
                  registerScrollTo={registerScrollTo}
                />
              )}
            </div>

            {/* Chat */}
            <div className={cn(mobileTab === "doc" ? "hidden md:flex" : "flex w-full md:w-auto")}>
              <WorkspaceChat
                ref={chatRef}
                messages={messages}
                input={input}
                onInput={setInput}
                onSend={() => void runTurn(input)}
                working={working}
                pending={pending}
                onAnswerPending={(answer) => void runTurn(answer)}
                onUndoSection={(title) => void handleUndoSection(title)}
                token={token}
              />
            </div>
          </>
        ) : (
          /* Chat-first: workspace é só o chat até gerar o draft */
          <div className="flex-1 flex min-h-0">
            <WorkspaceChat
              ref={chatRef}
              messages={messages}
              input={input}
              onInput={setInput}
              onSend={() => void runTurn(input)}
              working={working}
              pending={pending}
              onAnswerPending={(answer) => void runTurn(answer)}
              onUndoSection={() => {}}
              token={token}
              fullWidth
            />
          </div>
        )}
      </div>

      {exportOpen && (
        <ExportModal
          sessionId={sessionId!}
          sections={sections}
          targetTitle={targetTitle || editalId}
          mode={mode}
          token={token}
          onClose={() => setExportOpen(false)}
        />
      )}
    </div>
  );
}
