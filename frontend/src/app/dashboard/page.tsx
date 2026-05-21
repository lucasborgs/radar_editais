"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { KnowledgeGraph } from "@/components/KnowledgeGraph";
import { getGraph, kgExplore } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { GraphData, GraphNode, KGChatMessage } from "@/lib/api";

const GREETING: KGChatMessage = {
  role: "assistant",
  content:
    "Olá! Sou o assistente do Radar de Editais. Posso te mostrar oportunidades " +
    "de fomento da FINEP — pergunte algo como \"quais editais aceitam startups?\" " +
    "ou clique num nó do grafo ao lado. Quando quiser um ranking personalizado " +
    "para a sua empresa, é só usar o Match.",
};

// ── Chat bubble ──────────────────────────────────────────────────────────────

function Bubble({ msg }: { msg: KGChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm font-sans ${
          isUser
            ? "bg-primary text-white whitespace-pre-wrap"
            : "bg-white border border-border text-content-primary prose prose-sm max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:font-semibold prose-headings:text-content-primary"
        }`}
      >
        {isUser ? msg.content : <ReactMarkdown>{msg.content}</ReactMarkdown>}
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const router = useRouter();
  const { data: graph, loading: graphLoading } = useAsync<GraphData>(
    () => getGraph(),
    []
  );

  const [messages, setMessages] = useState<KGChatMessage[]>([GREETING]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  const send = useCallback(
    async (text: string, editalIds: string[] = [], nodeId?: string, nodeType?: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      const userMsg: KGChatMessage = { role: "user", content: trimmed };
      // history = tudo menos o greeting inicial (não é contexto real de conversa)
      const history = messages.filter((m) => m !== GREETING);
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setSending(true);

      try {
        const { answer } = await kgExplore(
          trimmed,
          [...history, userMsg],
          editalIds,
          nodeId,
          nodeType,
        );
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: answer },
        ]);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content:
              "Não consegui falar com o servidor. Confirme se o backend está " +
              "rodando em http://localhost:8000.",
          },
        ]);
      } finally {
        setSending(false);
      }
    },
    [messages, sending]
  );

  const handleNodeClick = useCallback(
    (node: GraphNode) => {
      if (node.type === "edital" && node.edital_id) {
        send(`Me fale sobre o edital "${node.label}".`, [node.edital_id]);
        return;
      }
      const typeLabel: Record<string, string> = {
        tema: "tema",
        publico: "público-alvo",
        subprograma: "subprograma",
        home: "fonte de fomento",
      };
      const label = typeLabel[node.type] ?? node.type;
      send(
        `Apresente uma visão geral do ${label} "${node.label}": quais editais estão associados, valores disponíveis, prazos, perfis elegíveis e o que diferencia este ${label} no catálogo.`,
        [],
        node.id,
        node.type,
      );
    },
    [send]
  );

  return (
    <DashboardLayout title="Dashboard">
      <div className="flex gap-6 h-[calc(100vh-6.5rem)]">
        {/* ── Chat (esquerda) ─────────────────────────────── */}
        <div className="w-2/5 flex flex-col bg-app-bg rounded-xl border border-border overflow-hidden">
          <div className="px-4 py-3 border-b border-border bg-white">
            <p className="text-sm font-semibold text-content-primary font-sans">
              Converse com o catálogo
            </p>
            <p className="text-xs text-content-secondary font-sans mt-0.5">
              Sem cadastro · explore as oportunidades
            </p>
          </div>

          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto p-4 space-y-3"
          >
            {messages.map((m, i) => (
              <Bubble key={i} msg={m} />
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="bg-white border border-border rounded-xl px-3.5 py-2.5">
                  <div className="flex gap-1">
                    <span className="w-1.5 h-1.5 bg-content-secondary rounded-full animate-bounce" />
                    <span className="w-1.5 h-1.5 bg-content-secondary rounded-full animate-bounce [animation-delay:0.15s]" />
                    <span className="w-1.5 h-1.5 bg-content-secondary rounded-full animate-bounce [animation-delay:0.3s]" />
                  </div>
                </div>
              </div>
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
            className="p-3 border-t border-border bg-white flex gap-2"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Pergunte sobre os editais…"
              className="flex-1 rounded-lg border border-border px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white font-sans disabled:opacity-40 transition-opacity"
            >
              Enviar
            </button>
          </form>
        </div>

        {/* ── Grafo (direita) ─────────────────────────────── */}
        <div className="flex-1 relative bg-white rounded-xl border border-border overflow-hidden">
          <div className="absolute top-3 left-4 z-10 flex flex-wrap gap-x-3 gap-y-1 text-xs font-sans text-content-secondary max-w-[70%]">
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full bg-[#1DB954]" /> Edital
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full bg-[#f59e0b]" /> Tema
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full bg-[#8b5cf6]" /> Público
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full bg-[#0ea5e9]" /> Fonte
            </span>
            <span className="flex items-center gap-1">
              <span className="w-2.5 h-2.5 rounded-full bg-[#a855f7]" /> Programa
            </span>
          </div>
          {graphLoading || !graph ? (
            <div className="h-full flex items-center justify-center">
              <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
            </div>
          ) : (
            <KnowledgeGraph data={graph} onNodeClick={handleNodeClick} />
          )}
          <button
            onClick={() => router.push("/matching")}
            className="absolute bottom-4 right-4 z-10 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white font-sans shadow-card hover:bg-primary-hover transition-colors"
          >
            Calcular meu match →
          </button>
        </div>
      </div>
    </DashboardLayout>
  );
}
