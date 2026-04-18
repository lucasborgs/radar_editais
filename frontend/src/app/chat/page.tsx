"use client";

import { Suspense, useState, useRef, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { startWritingSession, sendWritingTurn } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { WritingMessage } from "@/types/api";
import { loadProfileFromStorage, EMPTY_PROFILE } from "@/types/profile";

// ── Typing indicator ────────────────────────────────────────────────────────

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

// ── Message bubble ──────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: WritingMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-3 text-sm font-sans leading-relaxed",
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
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

function WritingPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const editalId = searchParams.get("edital");

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sectionTitles, setSectionTitles] = useState<string[]>([]);
  const [messages, setMessages] = useState<WritingMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const profile = loadProfileFromStorage() ?? EMPTY_PROFILE;

  // Guard: sem perfil → onboarding
  useEffect(() => {
    if (!loadProfileFromStorage()) {
      router.replace(`/onboarding${editalId ? `?next=/chat?edital=${editalId}` : ""}`);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-start session if edital_id is in URL
  useEffect(() => {
    if (!editalId || sessionId) return;
    setInitializing(true);
    setError(null);
    startWritingSession(editalId, profile)
      .then((res) => {
        setSessionId(res.session_id);
        setSectionTitles(res.section_titles ?? []);
        setMessages([
          {
            role: "assistant",
            content: `Sessão iniciada para o edital ${editalId}.\n\nSeções disponíveis: ${(res.section_titles ?? []).join(", ") || "nenhuma detectada ainda"}.\n\nComo posso te ajudar a escrever a proposta?`,
            timestamp: new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Erro ao iniciar sessão"))
      .finally(() => setInitializing(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editalId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function handleSend() {
    if (!input.trim() || !sessionId || loading) return;

    const userMsg: WritingMessage = {
      role: "user",
      content: input.trim(),
      timestamp: new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await sendWritingTurn(sessionId, userMsg.content);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.assistant_message,
          timestamp: new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao enviar mensagem");
    } finally {
      setLoading(false);
    }
  }

  return (
    <DashboardLayout title="Sessão de Escrita">
      <div className="flex flex-col h-[calc(100vh-130px)]">
        {/* Header */}
        <div className="bg-white border border-border rounded-xl px-4 py-3 mb-4 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-content-primary font-sans">
              {editalId ? `Edital #${editalId}` : "Sessão de Escrita"}
            </p>
            {sectionTitles.length > 0 && (
              <p className="text-xs text-content-secondary font-sans mt-0.5">
                {sectionTitles.length} seções disponíveis
              </p>
            )}
          </div>
          {!editalId && (
            <a
              href="/editais"
              className="text-xs text-primary font-sans hover:underline"
            >
              Selecionar edital →
            </a>
          )}
        </div>

        {/* Error */}
        {error && (
          <div className="mb-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
            {error}
          </div>
        )}

        {/* No edital selected */}
        {!editalId && !initializing && (
          <div className="flex-1 flex items-center justify-center bg-white rounded-xl border border-border">
            <div className="text-center max-w-sm px-6">
              <p className="font-heading text-base font-bold text-content-primary mb-2">
                Nenhum edital selecionado
              </p>
              <p className="text-sm text-content-secondary font-sans mb-4">
                Acesse a lista de editais, selecione um e clique em &quot;Iniciar Escrita&quot;.
              </p>
              <a
                href="/editais"
                className="inline-block px-4 py-2 rounded-xl bg-primary text-white text-sm font-semibold font-sans hover:bg-primary-hover transition-colors"
              >
                Ver Editais
              </a>
            </div>
          </div>
        )}

        {/* Messages */}
        {(editalId || messages.length > 0) && (
          <>
            <div className="flex-1 overflow-y-auto space-y-3 pr-1 pb-2">
              {initializing && (
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
            <div className="mt-3 flex gap-2">
              <textarea
                rows={2}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder="Escreva sua mensagem... (Enter para enviar, Shift+Enter para nova linha)"
                className={cn(
                  "flex-1 rounded-xl border border-border px-4 py-3 text-sm font-sans",
                  "text-content-primary placeholder:text-content-secondary resize-none",
                  "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary"
                )}
                disabled={!sessionId || loading}
              />
              <button
                onClick={handleSend}
                disabled={!sessionId || loading || !input.trim()}
                className={cn(
                  "self-end px-4 py-3 rounded-xl bg-primary text-white text-sm font-semibold font-sans",
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
