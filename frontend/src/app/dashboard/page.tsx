"use client";

import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { MetricCard, DataTable, StatusBadge } from "@/components/ui";
import { STATUS_CONFIG } from "@/lib/constants";
import { truncate } from "@/lib/utils";
import { getDashboardStats, getEditais } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { Column } from "@/components/ui";
import type { EditalEntry, DashboardStats } from "@/types/edital";

// ── Table columns ───────────────────────────────────────────────────────────

const COLUMNS: Column<EditalEntry>[] = [
  {
    key: "id",
    header: "ID",
    numeric: true,
    render: (v) => (
      <span className="font-data text-xs text-content-secondary">{String(v)}</span>
    ),
  },
  {
    key: "title",
    header: "Título",
    render: (v) => (
      <span className="font-medium">{truncate(String(v), 60)}</span>
    ),
  },
  {
    key: "status",
    header: "Status",
    render: (v) => <StatusBadge status={v as EditalEntry["status"]} />,
  },
  {
    key: "deadline",
    header: "Prazo",
    numeric: true,
    render: (v) => (
      <span className="text-xs">{String(v || "—")}</span>
    ),
  },
  {
    key: "themes",
    header: "Temas",
    render: (v) => (
      <span className="text-xs text-content-secondary">
        {Array.isArray(v) ? (v as string[]).slice(0, 2).join(", ") : String(v)}
      </span>
    ),
  },
];

// ── Error banner ────────────────────────────────────────────────────────────

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="rounded-xl bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700 font-sans flex items-start gap-2">
      <span className="shrink-0 mt-0.5">⚠</span>
      <span>
        <strong>Erro ao carregar dados:</strong> {message}
        <br />
        <span className="text-xs text-red-600">
          Verifique se o servidor FastAPI está rodando em{" "}
          <code className="font-data">http://localhost:8000</code>
        </span>
      </span>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function DashboardPage() {
  const {
    data: stats,
    loading: statsLoading,
    error: statsError,
  } = useAsync<DashboardStats>(() => getDashboardStats(), []);

  const {
    data: editais,
    loading: editaisLoading,
    error: editaisError,
  } = useAsync<EditalEntry[]>(() => getEditais({ limit: 10 }), []);

  const error = statsError ?? editaisError;
  const loading = statsLoading || editaisLoading;

  // ── Derived chart data ────────────────────────────────────
  const byStatusData = stats
    ? [
        {
          label: STATUS_CONFIG.ABERTA.label,
          value: stats.by_status["ABERTA"] ?? 0,
          color: "#1DB954",
        },
        {
          label: STATUS_CONFIG.ENCERRADA.label,
          value: stats.by_status["ENCERRADA"] ?? 0,
          color: "#6B7280",
        },
        {
          label: STATUS_CONFIG.Desconhecido.label,
          value: stats.by_status["Desconhecido"] ?? 0,
          color: "#3b82f6",
        },
      ]
    : [];

  const totalForPct = byStatusData.reduce((s, d) => s + d.value, 0) || 1;

  return (
    <DashboardLayout title="Dashboard">
      <div className="space-y-6">
        {/* Error banner */}
        {error && !loading && <ErrorBanner message={error} />}

        {/* KPI Row */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard
            label="Total de Editais"
            value={stats ? stats.total_editais : "—"}
            subtext="chamadas FINEP monitoradas"
          />
          <MetricCard
            label="Editais Abertos"
            value={stats ? (stats.by_status["ABERTA"] ?? 0) : "—"}
            subtext="com prazo vigente"
          />
          <MetricCard
            label="Temáticas Únicas"
            value={stats ? stats.n_themes : "—"}
            subtext="categorias identificadas"
          />
          <MetricCard
            label="Fontes de Recurso"
            value={stats ? stats.n_fontes : "—"}
            subtext="programas distintos"
          />
        </div>

        {/* Charts row */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Status distribution */}
          <div className="bg-white rounded-xl border border-border p-5 shadow-card">
            <p className="text-sm font-semibold text-content-primary font-sans mb-4">
              Distribuição por Status
            </p>
            {statsLoading ? (
              <div className="h-[220px] flex items-center justify-center">
                <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
              </div>
            ) : (
              <div className="space-y-4 mt-4">
                {byStatusData.map(({ label, value, color }) => (
                  <div key={label}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-content-secondary font-sans">
                        {label}
                      </span>
                      <span className="font-data text-xs text-content-primary">
                        {value}
                      </span>
                    </div>
                    <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all"
                        style={{
                          width: `${(value / totalForPct) * 100}%`,
                          backgroundColor: color,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Recent editais table */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <p className="text-sm font-semibold text-content-primary font-sans">
              Editais Recentes
            </p>
            <a
              href="/editais"
              className="text-xs text-primary hover:text-primary-hover font-medium font-sans transition-colors"
            >
              Ver todos →
            </a>
          </div>
          <DataTable
            data={editais ?? []}
            columns={COLUMNS}
            loading={editaisLoading}
            emptyMessage="Nenhum edital disponível. Verifique se o backend está rodando."
          />
        </div>
      </div>
    </DashboardLayout>
  );
}
