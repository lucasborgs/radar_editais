"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { PipelineBriefing } from "@/components/PipelineBriefing";
import { Skeleton } from "@/components/ui";
import {
  PipelineCard,
  STATUS_ORDER,
  STATUS_LABEL,
} from "@/components/PipelineCard";
import {
  listApplications,
  updateApplicationStatus,
  type ApplicationItem,
  type ApplicationStatus,
} from "@/lib/api";

export default function PipelinePage() {
  const { getToken } = useAuth();
  const [apps, setApps] = useState<ApplicationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [moving, setMoving] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getToken();
        if (!token) {
          setError("Faça login para ver seu pipeline.");
          setLoading(false);
          return;
        }
        const res = await listApplications(token);
        if (cancelled) return;
        setApps(res ?? []);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "Erro ao carregar o pipeline";
        if (cancelled) return;
        if (msg.includes("404")) {
          setError("Pipeline ainda não disponível neste ambiente.");
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

  async function handleStatusChange(id: string, status: ApplicationStatus) {
    const prev = apps;
    // Optimista: move o card de coluna localmente.
    setApps((cur) =>
      cur.map((a) => (a.application_id === id ? { ...a, status } : a)),
    );
    setMoving(id);
    try {
      const token = await getToken();
      if (!token) throw new Error("Sessão expirada.");
      await updateApplicationStatus(id, status, token);
    } catch {
      // Reverte em caso de falha.
      setApps(prev);
      alert("Não foi possível mover o card. Tente novamente.");
    } finally {
      setMoving(null);
    }
  }

  const byStatus = (status: ApplicationStatus) =>
    apps.filter((a) => a.status === status);

  return (
    <DashboardLayout title="Pipeline">
      <div className="space-y-6">
        {loading ? (
          <>
            <Skeleton className="rounded-xl border border-border shadow-card h-32" />
            <div className="flex gap-4 overflow-x-auto pb-4">
              {[1, 2, 3, 4].map((col) => (
                <div key={col} className="w-72 flex-shrink-0 flex flex-col">
                  <div className="flex items-center justify-between px-1 mb-2">
                    <Skeleton className="h-3 w-20" />
                    <Skeleton className="h-3 w-4" />
                  </div>
                  <div className="rounded-xl border border-border bg-app-bg p-2 space-y-2 min-h-[120px] flex-1">
                    {[1, 2].map((card) => (
                      <Skeleton
                        key={card}
                        className="rounded-xl border border-border h-24"
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : error ? (
          <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 px-4 py-3 text-sm text-amber-800 dark:text-amber-300 font-sans">
            {error}
          </div>
        ) : apps.length === 0 ? (
          <div className="text-center py-20 space-y-3">
            <p className="text-4xl">📋</p>
            <p className="text-sm font-sans text-content-secondary">
              Seu pipeline está vazio.
              <br />
              Faça um match e gere um brief para começar.
            </p>
            <Link
              href="/radar"
              className="inline-block mt-2 px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors"
            >
              Ir para o Radar
            </Link>
          </div>
        ) : (
          <>
            <PipelineBriefing applications={apps} />

            <div className="flex gap-4 overflow-x-auto pb-4">
              {STATUS_ORDER.map((status) => {
                const items = byStatus(status);
                return (
                  <div
                    key={status}
                    className="w-72 flex-shrink-0 flex flex-col"
                  >
                    <div className="flex items-center justify-between px-1 mb-2">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-content-secondary font-sans">
                        {STATUS_LABEL[status]}
                      </h3>
                      <span className="font-data text-xs text-content-secondary tabular-nums">
                        {items.length}
                      </span>
                    </div>
                    <div className="rounded-xl border border-border bg-app-bg p-2 space-y-2 min-h-[120px] flex-1">
                      {items.length === 0 ? (
                        <p className="text-xs text-content-secondary font-sans text-center py-6">
                          Vazio
                        </p>
                      ) : (
                        items.map((app) => (
                          <PipelineCard
                            key={app.application_id}
                            app={app}
                            onStatusChange={handleStatusChange}
                            busy={moving === app.application_id}
                          />
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </DashboardLayout>
  );
}
