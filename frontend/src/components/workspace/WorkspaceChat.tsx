"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { cn } from "@/lib/utils";
import { AgentTrace, type AgentTraceStep } from "@/components/chat/AgentTrace";
import { ChatBubble } from "@/components/chat/ChatBubble";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { MentionsTextarea } from "@/components/ui/MentionsTextarea";
import { PendingUserInputPrompt } from "@/components/writing/PendingUserInputPrompt";
import type { PendingUserInput } from "@/types/api";
import type { WorkspaceMessage } from "./types";

// Handle imperativo: o explorer insere @mentions no composer via ref.
export interface WorkspaceChatHandle {
  appendToComposer: (text: string) => void;
}

/**
 * Chat à direita do workspace: transcript (ChatBubble) + composer (mentions).
 *
 * Renderiza:
 *  - streaming text + tool trace durante a geração;
 *  - botão "Parar" para cancelar o turno;
 *  - `pending_user_input` como prompt destacado (PendingUserInputPrompt);
 *  - `compliance_flags` como aviso âmbar no turno;
 *  - chips "§ {seção} atualizada · ↶ desfazer" por seção co-editada.
 */
export const WorkspaceChat = forwardRef<WorkspaceChatHandle, {
  messages: WorkspaceMessage[];
  input: string;
  onInput: (v: string) => void;
  onSend: () => void;
  working: boolean;
  pending: PendingUserInput | null;
  onAnswerPending: (answer: string) => void;
  onUndoSection: (title: string) => void;
  token: string | null;
  fullWidth?: boolean;
  // Sprint 1 — streaming + tool trace + stop
  streamingText?: string | null;
  agentTrace?: AgentTraceStep[];
  onStop?: () => void;
  // H2 — retry on turn error
  turnError?: string | null;
  onRetry?: () => void;
}>(function WorkspaceChat(
  {
    messages, input, onInput, onSend, working, pending,
    onAnswerPending, onUndoSection, token, fullWidth,
    streamingText, agentTrace, onStop,
    turnError, onRetry,
  },
  ref,
) {
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({
    appendToComposer(text: string) {
      const sep = input && !input.endsWith(" ") ? " " : "";
      onInput(input + sep + text + " ");
      composerRef.current?.focus();
    },
  }));

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, working, streamingText]);

  return (
    <div className={cn(fullWidth ? "w-full" : "w-[26rem] shrink-0", "border-l border-border bg-app-bg flex flex-col min-w-0")}>
      <div className="h-9 shrink-0 px-3 flex items-center border-b border-border bg-surface">
        <span className="text-[10px] font-semibold text-content-secondary font-sans uppercase tracking-wide">
          Agente
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((msg, i) => (
          <div key={i} className="space-y-1.5">
            <ChatBubble role={msg.role} timestamp={msg.timestamp}>
              <pre className="whitespace-pre-wrap font-sans text-inherit">{msg.content}</pre>
            </ChatBubble>

            {/* tool trace on completed messages */}
            {msg.role === "assistant" && (msg.toolTrace?.length ?? 0) > 0 && (
              <AgentTrace steps={msg.toolTrace!} />
            )}

            {msg.role === "assistant" && msg.truncated && (
              <p className="px-1 text-[11px] italic text-content-secondary font-sans">
                Resposta interrompida no limite de passos — continue a conversa para o agente retomar.
              </p>
            )}

            {msg.role === "assistant" && (msg.complianceFlags?.length ?? 0) > 0 && (
              <div className="rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 font-sans">
                {msg.complianceFlags!.map((flag, fi) => (
                  <p key={fi}>
                    ⚠︎ {String(flag.message ?? flag.reason ?? "Atenção a um requisito do edital.")}
                  </p>
                ))}
              </div>
            )}

            {msg.role === "assistant" && (msg.editedSections?.length ?? 0) > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {msg.editedSections!.map((title) => (
                  <span
                    key={title}
                    className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 px-2 py-0.5 text-[11px] font-sans text-amber-800 dark:text-amber-300"
                  >
                    <span className="truncate max-w-[12rem]">§ {title} atualizada</span>
                    <button
                      type="button"
                      onClick={() => onUndoSection(title)}
                      className="font-medium text-amber-900 dark:text-amber-200 hover:underline shrink-0"
                      title="Desfazer esta edição do agente"
                    >
                      ↶ desfazer
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        {/* Streaming bubble — texto incremental + tool trace ao vivo */}
        {streamingText != null && (
          <div className="space-y-1.5">
            <ChatBubble role="assistant">
              <pre className="whitespace-pre-wrap font-sans text-inherit">
                {streamingText}
                <span className="inline-block w-1.5 h-4 bg-content-secondary animate-pulse ml-0.5 align-text-bottom" />
              </pre>
            </ChatBubble>
            {(agentTrace?.length ?? 0) > 0 && (
              <AgentTrace steps={agentTrace!} />
            )}
          </div>
        )}

        {working && streamingText == null && (
          <div className="flex items-center gap-2 text-xs text-content-secondary font-sans px-1">
            <TypingIndicator />
            <span>agente trabalhando…</span>
          </div>
        )}

        {turnError && !working && (
          <div className="rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 px-3 py-2 text-xs text-red-800 dark:text-red-300 flex items-center justify-between gap-2 font-sans">
            <span>{turnError}</span>
            {onRetry && (
              <button
                onClick={onRetry}
                className="shrink-0 px-2 py-1 rounded-md bg-red-200 dark:bg-red-800 text-red-900 dark:text-red-200 font-semibold hover:bg-red-300 dark:hover:bg-red-700 transition-colors"
              >
                ↻ Tentar novamente
              </button>
            )}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 border-t border-border bg-surface p-3 space-y-2">
        {pending && (
          <PendingUserInputPrompt
            pending={pending}
            onAnswer={onAnswerPending}
            disabled={working}
          />
        )}
        <div className="flex gap-2">
          <MentionsTextarea
            ref={composerRef}
            rows={2}
            value={input}
            onChange={onInput}
            onSubmitEnter={onSend}
            token={token}
            placeholder="Peça ao agente… (@ menciona a biblioteca, Enter envia)"
            className={cn(
              "w-full rounded-xl border border-border px-3 py-2.5 text-sm font-sans",
              "text-content-primary placeholder:text-content-secondary resize-none",
              "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
            )}
            disabled={working}
          />
          {working ? (
            <button
              onClick={onStop}
              className={cn(
                "self-end px-4 py-2.5 rounded-xl bg-destructive text-white text-sm font-semibold font-sans",
                "hover:bg-destructive-hover transition-colors",
              )}
            >
              ■ Parar
            </button>
          ) : (
            <button
              onClick={onSend}
              disabled={!input.trim()}
              className={cn(
                "self-end px-4 py-2.5 rounded-xl bg-primary text-white text-sm font-semibold font-sans",
                "hover:bg-primary-hover transition-colors",
                "disabled:opacity-50 disabled:cursor-not-allowed",
              )}
            >
              Enviar
            </button>
          )}
        </div>
      </div>
    </div>
  );
});
