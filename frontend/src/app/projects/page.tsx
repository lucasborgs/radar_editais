"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { Skeleton } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import {
  getConsultantState,
  listConversations,
  listWritingSessions,
  type ConsultantJourneyState,
  type WritingSessionSummary,
} from "@/lib/api";

const STATUS_LABEL: Record<WritingSessionSummary["status"], string> = {
  active: "Em andamento",
  completed: "Concluído",
  abandoned: "Arquivado",
};

function formatUpdatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Data indisponível";
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}

export default function ProjectsPage() {
  const { getToken } = useAuth();
  const [sessions, setSessions] = useState<WritingSessionSummary[]>([]);
  const [consultantStates, setConsultantStates] = useState<ConsultantJourneyState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getToken();
        if (!token) {
          if (!cancelled) setError("Entre para acessar seus projetos.");
          return;
        }
        const [response, conversations] = await Promise.all([
          listWritingSessions(token),
          listConversations(token),
        ]);
        const consultantSummaries = conversations.conversations.filter((item) => item.kind === "consultant");
        const loadedConsultants = await Promise.all(
          consultantSummaries.map(async (item) => {
            try {
              return (await getConsultantState(item.session_id, token)).state;
            } catch {
              return null;
            }
          }),
        );
        if (!cancelled) {
          setSessions(response.sessions.filter((session) => session.kind !== "frontdoor"));
          setConsultantStates(loadedConsultants.filter((state): state is ConsultantJourneyState => state !== null));
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "Não foi possível carregar seus projetos.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  const ordered = useMemo(
    () => [...sessions].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
    [sessions],
  );

  return (
    <DashboardLayout title="Projetos">
      <div className="space-y-6">
        <div>
          <p className="text-sm font-semibold text-primary">Projetos</p>
          <h1 className="mt-1 text-2xl font-semibold text-content-primary">Seus projetos</h1>
          <p className="mt-1 text-sm text-content-secondary">
            Retome projetos iniciados, caminhos avaliados e propostas em andamento.
          </p>
        </div>

        {loading ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {[0, 1, 2, 3].map((key) => (
              <Skeleton key={key} className="h-36 rounded-xl border border-border" />
            ))}
          </div>
        ) : error ? (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
            <p>{error}</p>
            <Link href="/login" className="mt-3 inline-block font-medium underline">
              Entrar
            </Link>
          </div>
        ) : ordered.length === 0 && consultantStates.length === 0 ? (
          <div className="rounded-xl border border-border bg-surface p-8 text-center">
            <h3 className="font-semibold text-content-primary">Nenhum projeto ainda</h3>
            <p className="mx-auto mt-2 max-w-md text-sm text-content-secondary">
              Comece pelo Consultor para transformar o contexto da sua empresa em um projeto.
            </p>
            <Link
              href="/"
              className="mt-5 inline-flex rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              Ir para o Consultor
            </Link>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {consultantStates.map((state) => (
              <Link
                key={state.conversation_id}
                href={`/?c=${encodeURIComponent(state.conversation_id)}`}
                className="group rounded-xl border border-primary/25 bg-primary/5 p-5 transition-colors hover:border-primary/50"
              >
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="rounded-full bg-primary/10 px-2 py-1 font-medium text-primary">Consultor</span>
                  <span className="text-content-secondary">
                    {state.project ? "Projeto confirmado" : "Brief em revisão"}
                  </span>
                </div>
                <h3 className="mt-4 line-clamp-2 font-semibold text-content-primary group-hover:text-primary">
                  {state.brief?.original_intention || "Nova intenção"}
                </h3>
                <div className="mt-4 flex items-center justify-between text-xs text-content-secondary">
                  <span>Revisão {state.revision}</span>
                  <span>{state.paths.length} {state.paths.length === 1 ? "caminho" : "caminhos"}</span>
                </div>
              </Link>
            ))}
            {ordered.map((session) => (
              <Link
                key={session.session_id}
                href={`/workspace/${session.session_id}`}
                className="group rounded-xl border border-border bg-surface p-5 transition-colors hover:border-primary/40"
              >
                <div className="flex items-center justify-between gap-3 text-xs">
                  <span className="rounded-full bg-primary/10 px-2 py-1 font-medium text-primary">
                    Execução
                  </span>
                  <span className="text-content-secondary">{STATUS_LABEL[session.status]}</span>
                </div>
                <h3 className="mt-4 line-clamp-2 font-semibold text-content-primary group-hover:text-primary">
                  {session.edital_title || session.edital_id}
                </h3>
                <div className="mt-4 flex items-center justify-between text-xs text-content-secondary">
                  <span>{session.turn_count} {session.turn_count === 1 ? "interação" : "interações"}</span>
                  <span>Atualizado em {formatUpdatedAt(session.updated_at)}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </DashboardLayout>
  );
}
