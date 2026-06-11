"use client";

import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { toast } from "sonner";
import { ChatBubble } from "@/components/chat/ChatBubble";
import { ChatMessageList } from "@/components/chat/ChatMessageList";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { FrontDoorHeader } from "@/components/frontdoor/FrontDoorHeader";
import { SuggestionChips } from "@/components/frontdoor/SuggestionChips";
import { Composer } from "@/components/frontdoor/Composer";
import { kgExplore } from "@/lib/api";
import type { KGChatMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";

// ── Estado persistido ──────────────────────────────────────────────────────────
// O transcript do front-door vive no navegador (M1: motor provisório kg-explore,
// stateless no backend). M2 adiciona perfil/diff; aqui só o histórico de turnos.
const HISTORY_KEY = "frontdoor_history";

// Boas-vindas do assistente (estado vazio). Não faz parte do `history` enviado
// ao backend — é só a abertura da conversa.
const WELCOME =
  "Oi! Me conte o que sua empresa faz — ou explore o que existe de fomento por aí.";

// Chips de partida do first-run. Clicar envia a mensagem.
const SUGGESTIONS = [
  "O que existe de fomento para IA em saúde?",
  "Quais editais estão com prazo aberto?",
  "Como funciona subvenção da FINEP?",
  "Que apoio há para hardware/deep tech?",
];

function loadHistory(): KGChatMessage[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    // Sanidade: só aceita array de turnos {role, content}.
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (m): m is KGChatMessage =>
        m &&
        (m.role === "user" || m.role === "assistant") &&
        typeof m.content === "string"
    );
  } catch {
    return [];
  }
}

// ── Bolha ───────────────────────────────────────────────────────────────────────
// Usuário em texto puro; assistente em markdown (mesmo padrão do chat do dashboard).
function Bubble({ msg }: { msg: KGChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <ChatBubble role={msg.role}>
      {isUser ? (
        <span className="whitespace-pre-wrap">{msg.content}</span>
      ) : (
        <div className="prose prose-sm max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:font-semibold prose-headings:text-content-primary">
          <ReactMarkdown>{msg.content}</ReactMarkdown>
        </div>
      )}
    </ChatBubble>
  );
}

// ── Página ────────────────────────────────────────────────────────────────────
export default function FrontDoorPage() {
  const { session } = useAuth();
  const isAuthed = !!session;

  const [messages, setMessages] = useState<KGChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // Evita flash do estado vazio antes de hidratar o localStorage no cliente.
  const [hydrated, setHydrated] = useState(false);

  // Carrega o transcript ao montar.
  useEffect(() => {
    setMessages(loadHistory());
    setHydrated(true);
  }, []);

  // Persiste a cada turno (depois de hidratar, para não sobrescrever com []).
  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(messages));
    } catch {
      // quota cheia / modo privado — ignora; a conversa segue só em memória.
    }
  }, [messages, hydrated]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      const userMsg: KGChatMessage = { role: "user", content: trimmed };
      // history = todos os turnos já trocados + a mensagem atual.
      const history = [...messages, userMsg];
      setMessages(history);
      setInput("");
      setSending(true);

      try {
        const { answer } = await kgExplore(trimmed, history);
        setMessages((prev) => [...prev, { role: "assistant", content: answer }]);
      } catch (e) {
        // Erro: remove o turno do usuário do transcript e o devolve ao input
        // para reenvio (a spec pede que a mensagem permaneça no composer).
        setMessages((prev) => prev.filter((m) => m !== userMsg));
        setInput(trimmed);
        toast.error(
          e instanceof Error
            ? e.message
            : "Não consegui falar com o servidor. Tente novamente."
        );
      } finally {
        setSending(false);
      }
    },
    [messages, sending]
  );

  const handleReset = useCallback(() => {
    try {
      window.localStorage.removeItem(HISTORY_KEY);
    } catch {
      // ignora — o estado em memória é limpo de qualquer forma.
    }
    setMessages([]);
    setInput("");
    toast.success("Conversa reiniciada.");
  }, []);

  const isEmpty = hydrated && messages.length === 0;

  return (
    <div className="flex h-[100dvh] flex-col bg-app-bg">
      <FrontDoorHeader isAuthed={isAuthed} onReset={handleReset} />

      <ChatMessageList
        className="mx-auto w-full max-w-2xl"
        deps={[sending, hydrated]}
      >
        {/* Boas-vindas sempre no topo da conversa. */}
        <Bubble msg={{ role: "assistant", content: WELCOME }} />

        {/* Estado vazio: chips de sugestão logo após o convite. */}
        {isEmpty && (
          <div className="pt-1">
            <SuggestionChips
              suggestions={SUGGESTIONS}
              onPick={(s) => void send(s)}
              disabled={sending}
            />
          </div>
        )}

        {messages.map((msg, i) => (
          <Bubble key={i} msg={msg} />
        ))}

        {sending && (
          <div className="flex items-start">
            <TypingIndicator />
          </div>
        )}
      </ChatMessageList>

      <Composer
        value={input}
        onChange={setInput}
        onSend={() => void send(input)}
        disabled={sending}
        placeholder="Conte o que sua empresa faz, ou pergunte sobre fomento…"
      />
    </div>
  );
}
