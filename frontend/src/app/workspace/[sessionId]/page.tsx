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
import {
  WorkspaceChat,
  type WorkspaceChatHandle,
} from "@/components/workspace/WorkspaceChat";
import {
  filledCount,
  modeFromEditalId,
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
  const [mobileTab, setMobileTab] = useState<"doc" | "chat">("doc");

  // Co-edição: seções tocadas pelo agente (highlight) + pilha de undo por seção.
  const [highlighted, setHighlighted] = useState<Set<string>>(new Set());
  // Snapshot do conteúdo ANTERIOR por seção (último valor antes da edição do
  // agente). Em memória — undo de 1 passo basta (spec §2/§7).
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
  // Retorna {sections, editalId} — o estado é a fonte de verdade do documento
  // que o workspace recarrega após cada turno (spec §9).
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
        // Título do alvo: tenta o card do edital; fundos (investidor:) caem no id.
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
        // Mensagem inicial do agente (welcome local — N1 não chama section-start).
        if (!cancelled && next.length > 0) {
          setMessages([
            {
              role: "assistant",
              content:
                "Pronto para trabalhar nesta proposta. Peça um rascunho de seção, " +
                "tire dúvidas sobre o edital ou edite o documento direto à esquerda.",
              timestamp: nowTime(),
            },
          ]);
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
    // O resolver de mentions do backend usa @<uuid>; inserimos o token
    // resolvível. O autocomplete da MentionsTextarea mostra o nome.
    chatRef.current?.appendToComposer(`@${item.id}`);
    setMobileTab("chat");
  }, []);

  // Esmaece o highlight quando o usuário interage com a seção.
  const handleSectionInteract = useCallback((title: string) => {
    setHighlighted((prev) => {
      if (!prev.has(title)) return prev;
      const next = new Set(prev);
      next.delete(title);
      return next;
    });
  }, []);

  // ── Edição manual inline (N2) ─────────────────────────────────────────────
  const handleSaveSection = useCallback(
    async (title: string, content: string) => {
      if (!sessionId) return;
      setSavingSection(title);
      // Otimista: reflete já no editor; backend é a fonte de verdade no próximo load.
      setSections((prev) => prev.map((s) => (s.title === title ? { ...s, content } : s)));
      try {
        await saveDocumentSection(sessionId, title, content);
      } catch (e) {
        toast.error(
          e instanceof Error ? `Erro ao salvar: ${e.message}` : "Erro ao salvar a seção.",
        );
        // Reverte para o valor do backend.
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

  // ── Undo de edição do agente (N2) ─────────────────────────────────────────
  const handleUndoSection = useCallback(
    async (title: string) => {
      if (!sessionId) return;
      const prevContent = undoSnapshots.current[title] ?? "";
      setSavingSection(title);
      setSections((prev) => prev.map((s) => (s.title === title ? { ...s, content: prevContent } : s)));
      handleSectionInteract(title); // remove highlight
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

  // ── Turno (o coração — F2) ────────────────────────────────────────────────
  const runTurn = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || !sessionId || working) return;
      // O turno usa o perfil do workspace no backend (fallback) — igual ao
      // fluxo de escrita atual, que não reenvia o perfil a cada turno.

      setMessages((prev) => [...prev, { role: "user", content, timestamp: nowTime() }]);
      setInput("");
      setPending(null);
      setWorking(true);

      try {
        const res = await sendWritingTurn(sessionId, content, undefined);

        // Seções persistidas neste turno (W-D1: saved_section no tool_trace).
        const editedSections = Array.from(
          new Set(
            (res.tool_trace ?? [])
              .filter((t) => t.name === "save_draft" && t.saved_section)
              .map((t) => t.saved_section as string),
          ),
        );

        // Snapshot do conteúdo ANTERIOR (antes de recarregar) p/ a pilha de undo.
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

        // Recarrega SEMPRE do backend (spec §9 — não confiar no estado local).
        try {
          await reloadDocument();
        } catch {
          /* mantém estado local; texto segue salvo no DB */
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

  return (
    <div className="h-screen flex flex-col bg-app-bg overflow-hidden">
      <WorkspaceHeader
        title={targetTitle || editalId || "Carregando…"}
        mode={mode}
        filled={filled}
        total={sections.length}
      />

      {/* Mobile: abas Documento | Chat */}
      <div className="md:hidden flex border-b border-border bg-white">
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

      <div className="flex-1 flex min-h-0">
        {/* Explorer — drawer no mobile (escondido por simplicidade em viewport estreita) */}
        <div className="hidden md:flex">
          <Explorer
            sections={sections}
            attachments={attachments}
            onSelectSection={handleSelectSection}
            onSelectAttachment={handleSelectAttachment}
          />
        </div>

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
              onSaveSection={handleSaveSection}
              onSectionInteract={handleSectionInteract}
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
      </div>
    </div>
  );
}
