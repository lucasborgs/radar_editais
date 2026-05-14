"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import {
  listWritingSessions,
  getEditalById,
  type WritingSessionSummary,
} from "@/lib/api";

const STATUS_LABEL: Record<WritingSessionSummary["status"], string> = {
  active: "Ativa",
  completed: "Concluída",
  abandoned: "Abandonada",
};

const STATUS_CLS: Record<WritingSessionSummary["status"], string> = {
  active: "bg-[#1DB954]/15 text-[#169c46]",
  completed: "bg-blue-500/15 text-blue-700",
  abandoned: "bg-gray-500/15 text-gray-600",
};

function formatRelative(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    if (isNaN(then)) return iso;
    const diffMs = Date.now() - then;
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin < 1) return "agora";
    if (diffMin < 60) return `${diffMin} min atrás`;
    const diffH = Math.round(diffMin / 60);
    if (diffH < 24) return `${diffH}h atrás`;
    const diffD = Math.round(diffH / 24);
    if (diffD < 7) return `${diffD}d atrás`;
    return new Date(iso).toLocaleDateString("pt-BR");
  } catch {
    return iso;
  }
}

export default function SessionsPage() {
  const router = useRouter();
  const { getToken } = useAuth();
  const [sessions, setSessions] = useState<WritingSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [titles, setTitles] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getToken();
        if (!token) {
          setError("Faça login para ver suas sessões.");
          setLoading(false);
          return;
        }
        const res = await listWritingSessions(token);
        if (cancelled) return;
        setSessions(res.sessions ?? []);

        // Lazy-resolve titles for sessions that don't carry them
        const missing = (res.sessions ?? []).filter(s => !s.edital_title && s.edital_id);
        await Promise.all(
          missing.map(async (s) => {
            try {
              const card = await getEditalById(s.edital_id);
              if (!cancelled) {
                setTitles(prev => ({ ...prev, [s.edital_id]: card.title }));
              }
            } catch {
              /* ignore */
            }
          })
        );
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "Erro ao carregar sessões";
        if (msg.includes("404")) {
          setError("Listagem de sessões ainda não está disponível neste ambiente.");
        } else {
          setError(msg);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  return (
    <DashboardLayout title="Sessões de escrita">
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="font-heading text-xl font-bold text-content-primary">
              Suas sessões
            </h1>
            <p className="text-sm text-content-secondary font-sans mt-0.5">
              Continue uma proposta em andamento ou inicie uma nova.
            </p>
          </div>
          <button
            onClick={() => router.push("/editais")}
            className="px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors"
          >
            + Nova sessão
          </button>
        </div>

        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div
                key={i}
                className="bg-white rounded-xl border border-border p-4 animate-pulse h-20"
              />
            ))}
          </div>
        ) : error ? (
          <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800 font-sans">
            {error}
          </div>
        ) : sessions.length === 0 ? (
          <div className="text-center py-16 space-y-3">
            <p className="text-4xl">📝</p>
            <p className="text-sm font-sans text-content-secondary">
              Nenhuma sessão ainda.<br />
              Escolha um edital para começar sua primeira proposta.
            </p>
            <button
              onClick={() => router.push("/editais")}
              className="mt-2 px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors"
            >
              Escolher edital
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {sessions.map(s => {
              const title = s.edital_title || titles[s.edital_id] || s.edital_id;
              return (
                <Link
                  key={s.session_id}
                  href={`/chat?edital=${encodeURIComponent(s.edital_id)}`}
                  className={cn(
                    "block bg-white rounded-xl border border-border p-4 transition-colors",
                    "hover:border-primary/40 hover:shadow-card"
                  )}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-semibold text-content-primary font-sans truncate">
                        {title}
                      </p>
                      <div className="flex items-center gap-3 mt-1.5">
                        <span
                          className={cn(
                            "text-[10px] font-semibold px-2 py-0.5 rounded-full font-sans",
                            STATUS_CLS[s.status]
                          )}
                        >
                          {STATUS_LABEL[s.status]}
                        </span>
                        <span className="text-xs text-content-secondary font-sans">
                          {s.turn_count} {s.turn_count === 1 ? "turno" : "turnos"}
                        </span>
                        <span className="text-xs text-content-secondary font-sans">
                          atualizada {formatRelative(s.updated_at)}
                        </span>
                      </div>
                    </div>
                    <span className="text-xs text-primary font-sans shrink-0 self-center">
                      Continuar →
                    </span>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
