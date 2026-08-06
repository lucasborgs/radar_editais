"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import {
  cancelWritingTurn,
  getWritingDocument,
  generateWritingProposal,
  saveDocumentSection,
  getLibraryItems,
  getEditalById,
  autoReviewChecklist,
  refineSection,
  sendWritingTurn,
  workspaceMode,
  writingTurnStream,
  type WritingRefineResponse,
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
  const [refining, setRefining] = useState<string | null>(null);

  function parseCommand(input: string): { command?: "profile" | "review"; message: string } {
    const match = input.trim().match(/^\/(profile|review|help)\b/);
    if (!match) return { message: input };
    if (match[1] === "help") return { message: "" };
    return {
      command: match[1] as "profile" | "review",
      message: input.slice(match[0].length).trim(),
    };
  }
  const [refineFeedback, setRefineFeedback] = useState<{
    title: string;
    feedback: WritingRefineResponse;
  } | null>(null);

  const [loadError, setLoadError] = useState<string | null>(null);
  const [docLoading, setDocLoading] = useState(true);
  const [mobileTab, setMobileTab] = useState<"doc" | "chat">("chat");
  const [mobileDrawer, setMobileDrawer] = useState(false);

  const [findings, setFindings] = useState<Finding[]>([]);
  const [reviewing, setReviewing] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);

  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());
  const undoSnapshots = useRef<Record<string, string>>({});

  // F4: plan-first — plano pendente de confirmação
  const [planPending, setPlanPending] = useState<Record<string, unknown> | null>(null);
  const [generating, setGenerating] = useState(false);

  // Sprint 1 — streaming + tool trace + stop
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [streamingTrace, setStreamingTrace] = useState<Array<{ name: string }>>([]);
  const [turnError, setTurnError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const lastTurnRef = useRef<{ text: string; sectionHint?: string } | null>(null);
  const lastRetryRef = useRef<number>(0);

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
    hasPlan: boolean;
  }> => {
    if (!sessionId) return { sections: [], editalId: "", hasPlan: false };
    const doc = await getWritingDocument(sessionId);
    const next = doc.sections.map((s) => ({ title: s.title, content: s.content }));
    setSections(next);
    setEditalId(doc.edital_id);
    setMode(modeFromEditalId(doc.edital_id));
    // F4: restaura estado do plano pendente
    if (doc.plan_pending && doc.plan) {
      setPlanPending(doc.plan);
    } else if (!doc.plan_pending) {
      setPlanPending(null);
    }
    // Contrato do plano (Bloco 2): "plano carregado" só quando __plan__ real.
    const hasPlan = !!(
      doc.plan &&
      typeof doc.plan === "object" &&
      Object.keys(doc.plan).length > 0
    );
    return { sections: next, editalId: doc.edital_id, hasPlan };
  }, [sessionId]);

  useEffect(() => {
    if (!authChecked || !sessionId) return;
    let cancelled = false;
    (async () => {
      setDocLoading(true);
      setLoadError(null);
      try {
        const { sections: next, editalId: id, hasPlan } = await reloadDocument();
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

        // Mensagem inicial — contrato do plano (Bloco 2):
        //   plano persistido → "Plano de proposta carregado";
        //   só outline/seções → "Proposta carregada";
        //   nada → mensagem inicial.
        if (!cancelled) {
          const anyContent = next.some((s) => s.content.trim());
          const welcome = anyContent
            ? "Pronto para revisar a proposta. Edite as seções à esquerda ou continue conversando no chat."
            : hasPlan
              ? "Plano de proposta carregado. Converse para começar a preencher cada seção."
              : next.length > 0
                ? "Proposta carregada. Converse para continuar."
                : "Converse sobre o edital ou peça para começar a sua proposta.";
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

  // ── Estrutura: navegação e anexos ─────────────────────────────────────────
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

      // Detecta comandos de ação (/profile, /review, /help)
      const { command, message } = parseCommand(content);

      // /help: mostra comandos disponíveis
      if (!message && command === undefined && content === "/help") {
        setWorking(true);
        setMessages((prev) => [...prev, { role: "user", content, timestamp: nowTime() }]);
        setInput("");
        const helpText = `**Comandos disponíveis:**
  • \`/profile\` — extrair sugestão de perfil de uma URL
  • \`/review\` — revisar uma seção com o Critic
  • \`/help\` — mostrar esta mensagem

O chat fala direto com a escrita da proposta.`;
        setMessages((prev) => [...prev, { role: "assistant", content: helpText, timestamp: nowTime() }]);
        setWorking(false);
        return;
      }

      // Ações one-shot: /profile, /review
      if (command === "profile" || command === "review") {
        setMessages((prev) => [...prev, { role: "user", content, timestamp: nowTime() }]);
        setInput("");
        setWorking(true);
        try {
          const res = await workspaceMode(sessionId, command, message || content);
          if (res.error) {
            toast.error(res.error);
            return;
          }
          setMessages((prev) => [...prev, { role: "assistant", content: res.response, timestamp: nowTime() }]);
        } catch (e) {
          toast.error(e instanceof Error ? e.message : "Erro ao executar ação.");
        } finally {
          setWorking(false);
        }
        return;
      }

      // Fluxo normal de escrita (streaming first, fallback batch)
      setMessages((prev) => [...prev, { role: "user", content: message || content, timestamp: nowTime() }]);
      setInput("");
      setPending(null);
      setWorking(true);
      setTurnError(null);
      setStreamingText("");
      setStreamingTrace([]);

      lastTurnRef.current = { text: message || content, sectionHint };
      const ac = new AbortController();
      abortRef.current = ac;
      if (!idempotencyKeyRef.current) {
        idempotencyKeyRef.current = crypto.randomUUID();
      }

      let streamOk = false;
      const tryStream = async (): Promise<Record<string, unknown> | null> => {
        return new Promise((resolve) => {
          writingTurnStream(
            sessionId,
            message || content,
            sectionHint,
            [],
            null,
            {
              onToken: (text) => setStreamingText((prev) => (prev ?? "") + text),
              onTool: (name) => setStreamingTrace((prev) => [...prev, { name }]),
              onDone: (payload) => { streamOk = true; resolve(payload); },
              onError: (msg) => { resolve({ error: msg }); },
            },
            ac.signal,
            idempotencyKeyRef.current ?? undefined,
          ).catch((err: unknown) => { resolve({ error: err instanceof Error ? err.message : "Stream error" }); });
        });
      };

      const streamResult = await tryStream();

      if (streamOk && !(streamResult as Record<string, unknown>).error) {
        const payload = streamResult as Record<string, unknown>;
        if (payload.plan_pending && payload.plan) {
          setPlanPending(payload.plan as Record<string, unknown>);
        }

        const rawTrace = (payload.tool_trace ?? []) as Array<Record<string, unknown>>;
        const editedSections = Array.from(
          new Set(
            rawTrace
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
            content: (payload.assistant_message as string) ?? (streamingText ?? ""),
            timestamp: nowTime(),
            editedSections: editedSections.length > 0 ? editedSections : undefined,
            complianceFlags:
              ((payload.compliance_flags as unknown[])?.length ?? 0) > 0
                ? (payload.compliance_flags as { message: string }[])
                : undefined,
            truncated: (payload.truncated as boolean) || undefined,
            toolTrace: rawTrace.map((t) => ({
              name: t.name as string,
              input_summary: typeof t.input === "string" ? t.input as string : JSON.stringify(t.input).slice(0, 200),
              output_summary: typeof t.output === "string" ? t.output as string : undefined,
            })),
          },
        ]);

        if (payload.draft_ready || ((payload.sections_done as string[])?.length ?? 0) > 0 || payload.plan_pending) {
          setMobileTab("doc");
          try { await reloadDocument(); } catch { /* mantém estado local */ }
        } else {
          try { await reloadDocument(); } catch { /* ignore */ }
        }

        if (editedSections.length > 0) {
          setHighlighted((prev) => {
            const next = new Set(prev);
            editedSections.forEach((t) => next.add(t));
            return next;
          });
        }
        if (payload.pending_user_input) setPending(payload.pending_user_input as PendingUserInput);
      } else {
        // Fallback batch
        try {
          const res = await sendWritingTurn(sessionId, message || content, sectionHint, undefined, idempotencyKeyRef.current ?? undefined);

          // O backend pode retornar success:false (erro de geração/persistência)
          // sem lançar exceção HTTP. Nesse caso renderizamos um banner de erro
          // explícito — e NÃO uma bolha normal do assistente com "Erro ao processar:".
          if (res.success === false) {
            console.error(
              "writing turn failed (batch):",
              res.error ?? res.assistant_message,
            );
            setTurnError("A geração falhou no servidor. Tente novamente.");
            toast.error("A geração falhou no servidor. Tente novamente.");
            idempotencyKeyRef.current = null;
            setStreamingText(null);
            abortRef.current = null;
            setWorking(false);
            return;
          }

          if (res.plan_pending && res.plan) {
            setPlanPending(res.plan);
          }

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
              truncated: res.truncated || undefined,
              toolTrace: (res.tool_trace ?? []).map((t) => {
                const tr = t as unknown as Record<string, unknown>;
                return {
                  name: tr.name as string,
                  input_summary: typeof tr.input === "string" ? tr.input as string : JSON.stringify(tr.input).slice(0, 200),
                  output_summary: typeof tr.output === "string" ? tr.output as string : undefined,
                };
              }),
            },
          ]);

          if (res.draft_ready || (res.sections_done?.length ?? 0) > 0 || res.plan_pending) {
            setMobileTab("doc");
            try { await reloadDocument(); } catch { /* mantém estado local */ }
          } else {
            try { await reloadDocument(); } catch { /* ignore */ }
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
          setTurnError(e instanceof Error ? e.message : "Erro ao enviar mensagem ao agente.");
          toast.error(e instanceof Error ? e.message : "Erro ao enviar mensagem ao agente.");
        }
      }

      idempotencyKeyRef.current = null;
      setStreamingText(null);
      setStreamingTrace([]);
      abortRef.current = null;
      setWorking(false);
    },
    [sessionId, working, reloadDocument],
  );

  // F4: gera a proposta completa após confirmação do plano
  const handleGenerateFromPlan = useCallback(async () => {
    if (!sessionId || generating) return;
    setGenerating(true);
    try {
      const res = await generateWritingProposal(sessionId);
      setMessages((prev) => [...prev, {
        role: "assistant",
        content: res.sections_done.length > 0
          ? `Proposta gerada com ${res.sections_done.length} seção(ões): ${res.sections_done.join(", ")}.${res.failed_sections.length ? `\nNão consegui: ${res.failed_sections.join(", ")}.` : ""}`
          : "Não consegui gerar nenhuma seção.",
        timestamp: nowTime(),
      }]);
      setPlanPending(null);
      setMobileTab("doc");
      try {
        await reloadDocument();
      } catch { /* ignore */ }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Erro ao gerar proposta.");
    } finally {
      setGenerating(false);
    }
  }, [sessionId, generating, reloadDocument]);

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

  // ── Refinar seção (FASE 3) ────────────────────────────────────────────────
  const handleRefineSection = useCallback(
    async (title: string, instruction: string) => {
      if (!sessionId || refining) return;
      setRefining(title);
      try {
        const result = await refineSection(sessionId, title, instruction);
        setRefineFeedback({ title, feedback: result });
        if (result.section_updated) {
          await reloadDocument();
          toast.success(`"${title}" refinada com sucesso.`);
        } else if (result.error) {
          toast.error(result.error);
        } else {
          toast.info("Nenhuma alteração feita pelo refinamento.");
        }
      } catch (e) {
        toast.error(
          e instanceof Error ? e.message : "Erro ao refinar seção.",
        );
      } finally {
        setRefining(null);
      }
    },
    [sessionId, refining, reloadDocument],
  );

  // ── Render ────────────────────────────────────────────────────────────────
  if (authChecked && !token) {
    return (
      <div className="h-screen flex items-center justify-center bg-app-bg">
        <p className="text-sm text-content-secondary font-sans">
          Faça login para abrir o projeto.
        </p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="h-screen flex items-center justify-center bg-app-bg px-6">
        <div className="text-center max-w-sm">
          <p className="text-sm text-content-primary font-sans mb-2">{loadError}</p>
          <a href="/projects" className="text-sm text-primary font-sans hover:underline">
            ← Voltar para Projetos
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
        title={targetTitle || "Carregando…"}
        mode={mode}
        filled={filled}
        total={sections.length}
        sessionId={sessionId}
      />

      {/* Mobile: abas Documento | Chat + drawer da estrutura */}
      {sections.length > 0 && (
        <div className="md:hidden flex items-center border-b border-border bg-surface">
          <button
            onClick={() => setMobileDrawer(true)}
            title="Abrir estrutura"
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
      )}

      <div className="flex-1 flex min-h-0">
        {/* Estrutura — sidebar no desktop (sempre que houver seções) */}
        {sections.length > 0 && (
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
        )}

        {/* Estrutura — drawer overlay no mobile */}
        {mobileDrawer && sections.length > 0 && (
          <div className="md:hidden fixed inset-0 z-40 flex">
            <button
              aria-label="Fechar estrutura"
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

        {/* Editor — sempre que há seções */}
        {sections.length > 0 && (
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
                onRefineSection={handleRefineSection}
                registerScrollTo={registerScrollTo}
              />
            )}
          </div>
        )}

        {/* Chat — sempre visível, ocupa espaço restante */}
        <div className={cn(
          "flex-1 flex min-h-0",
          sections.length > 0
            ? mobileTab === "doc" ? "hidden md:flex md:max-w-sm" : "flex"
            : "flex",
        )}>
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
            fullWidth={sections.length === 0}
            streamingText={streamingText}
            agentTrace={streamingTrace.length > 0 ? streamingTrace : undefined}
            onStop={() => {
              abortRef.current?.abort();
              abortRef.current = null;
              cancelWritingTurn(sessionId).catch(() => {});
            }}
            turnError={turnError}
            onRetry={() => {
              const now = Date.now();
              if (now - lastRetryRef.current < 2000) return;
              lastRetryRef.current = now;
              const last = lastTurnRef.current;
              if (last) {
                setTurnError(null);
                void runTurn(last.text, last.sectionHint);
              }
            }}
          />
        </div>
      </div>

      {/* F4: plano pendente de confirmação */}
      {planPending && !generating && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="max-w-2xl w-full mx-4 bg-surface rounded-lg shadow-xl border border-border max-h-[80vh] flex flex-col">
            <div className="p-5 border-b border-border">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold text-content-primary font-sans">
                  {String((planPending as Record<string, unknown>).title ?? "Plano da Proposta")}
                </h2>
              </div>
              <p className="text-xs text-content-secondary font-sans mt-1">
                Revise o plano antes de gerar a proposta completa.
              </p>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              {/* Mismatch warnings */}
              {Array.isArray((planPending as Record<string, unknown>).mismatch_warnings) &&
               ((planPending as Record<string, unknown>).mismatch_warnings as string[]).length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                  <p className="text-xs font-semibold text-amber-800 font-sans mb-1">{String.fromCodePoint(9888)} Alertas de Misfit</p>
                  <ul className="space-y-1">
                    {((planPending as Record<string, unknown>).mismatch_warnings as string[]).map((w, i) => (
                      <li key={i} className="text-xs text-amber-700 font-sans flex gap-1">
                        <span className="shrink-0">{String.fromCodePoint(8226)}</span>
                        {w}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {/* Seções do plano */}
              {Array.isArray((planPending as Record<string, unknown>).sections) &&
               ((planPending as Record<string, unknown>).sections as Record<string, unknown>[]).map((sec, i) => (
                <div key={i} className="border border-border rounded-lg p-3">
                  <h3 className="text-sm font-semibold text-content-primary font-sans mb-1">
                    {String(sec.title ?? "")}
                  </h3>
                  {Array.isArray(sec.coverage) && (sec.coverage as string[]).length > 0 && (
                    <ul className="space-y-0.5 mb-1">
                      {(sec.coverage as string[]).map((c, j) => (
                        <li key={j} className="text-xs text-content-secondary font-sans flex gap-1">
                          <span className="text-primary shrink-0">{String.fromCodePoint(8594)}</span>
                          {c}
                        </li>
                      ))}
                    </ul>
                  )}
                  {Array.isArray(sec.missing_info) && (sec.missing_info as string[]).length > 0 && (
                    <div className="mt-1">
                      <p className="text-xs text-amber-600 font-sans">Info faltante:</p>
                      <ul className="space-y-0.5">
                        {(sec.missing_info as string[]).map((m, j) => (
                          <li key={j} className="text-xs text-amber-600 font-sans flex gap-1">
                            <span className="shrink-0">{String.fromCodePoint(9888)}</span>
                            {m}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))}
              {/* Perguntas críticas */}
              {Array.isArray((planPending as Record<string, unknown>).critical_questions) &&
               ((planPending as Record<string, unknown>).critical_questions as string[]).length > 0 && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                  <p className="text-xs font-semibold text-blue-800 font-sans mb-1">Perguntas Críticas</p>
                  <ul className="space-y-1">
                    {((planPending as Record<string, unknown>).critical_questions as string[]).map((q, i) => (
                      <li key={i} className="text-xs text-blue-700 font-sans flex gap-1">
                        <span className="shrink-0">?</span>
                        {q}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="p-4 border-t border-border flex gap-3 justify-end">
              <button
                onClick={() => setPlanPending(null)}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-content-secondary font-sans hover:bg-app-bg transition-colors"
              >
                Dispensar
              </button>
              <button
                onClick={() => void handleGenerateFromPlan()}
                className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white font-sans hover:bg-primary-dark transition-colors"
              >
                Gerar Proposta
              </button>
            </div>
          </div>
        </div>
      )}

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

      {/* Critic feedback after refinement (FASE 3) */}
      {refineFeedback && refineFeedback.feedback.critic_feedback && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="max-w-lg w-full mx-4 bg-surface rounded-lg shadow-xl border border-border">
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-content-primary font-sans">
                  Feedback da revisão — &ldquo;{refineFeedback.title}&rdquo;
                </h3>
                <button
                  onClick={() => setRefineFeedback(null)}
                  className="text-content-secondary hover:text-content-primary transition-colors text-lg leading-none"
                >
                  ✕
                </button>
              </div>
              <div className="space-y-2 text-xs font-sans text-content-primary">
                {refineFeedback.feedback.critic_feedback &&
                typeof refineFeedback.feedback.critic_feedback === "object" &&
                "issues" in (refineFeedback.feedback.critic_feedback as Record<string, unknown>) &&
                Array.isArray((refineFeedback.feedback.critic_feedback as Record<string, unknown>).issues) ? (
                  <>
                    <p className="font-medium text-content-secondary">
                      {(refineFeedback.feedback.critic_feedback as Record<string, unknown>).approved
                        ? "✅ Aprovado pelo revisor"
                        : "⚠ Revisor bloqueou — issues encontrados:"}
                    </p>
                    <ul className="space-y-1">
                      {((refineFeedback.feedback.critic_feedback as Record<string, unknown>).issues as string[]).map(
                        (issue, i) => (
                          <li key={i} className="flex gap-1">
                            <span className="text-amber-600 shrink-0">•</span>
                            {issue}
                          </li>
                        ),
                      )}
                    </ul>
                  </>
                ) : (
                  <p className="text-content-secondary">
                    Refinamento concluído. Verifique a seção no editor.
                  </p>
                )}
              </div>
              <div className="flex justify-end gap-2 mt-4">
                {refineFeedback.feedback.options?.includes("voltar") && (
                  <button
                    onClick={() => setRefineFeedback(null)}
                    className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-content-secondary font-sans hover:bg-app-bg transition-colors"
                  >
                    Voltar
                  </button>
                )}
                <button
                  onClick={() => setRefineFeedback(null)}
                  className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white font-sans hover:bg-primary-dark transition-colors"
                >
                  OK
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
