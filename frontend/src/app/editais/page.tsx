"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { DataTable, SidebarFilter, StatusBadge, EMPTY_FILTER, MetricCard } from "@/components/ui";
import type { FilterState, Column } from "@/components/ui";
import { truncate } from "@/lib/utils";
import { getEditais, getDashboardStats } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
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
      <span className="font-medium">{truncate(String(v), 55)}</span>
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
    key: "category",
    header: "Categoria",
    render: (v) => (
      <span className="text-xs text-content-secondary">{String(v)}</span>
    ),
  },
];

// ── Page ────────────────────────────────────────────────────────────────────

export default function EditaisPage() {
  const router = useRouter();
  const { data: stats } = useAsync<DashboardStats>(() => getDashboardStats(), []);
  const [filters, setFilters] = useState<FilterState>(EMPTY_FILTER);
  const [allEditais, setAllEditais] = useState<EditalEntry[]>([]);
  const [availableSources, setAvailableSources] = useState<string[]>([]);
  const [availableThemes, setAvailableThemes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Track the last fetch to avoid race conditions
  const fetchIdRef = useRef(0);

  // Load available sources on mount — derived from editais data

  // Re-fetch when API-level filters change (single status or single source)
  // Multi-select and theme/date filters are applied client-side
  useEffect(() => {
    const id = ++fetchIdRef.current;
    setLoading(true);
    setError(null);

    const apiFilters = {
      status: filters.statuses.length === 1 ? filters.statuses[0] : undefined,
      limit: 500,
    };

    getEditais(apiFilters)
      .then((data) => {
        if (id !== fetchIdRef.current) return;
        setAllEditais(data);
        const srcs = Array.from(new Set(data.flatMap((e) => e.fonte_recurso))).sort();
        const thms = Array.from(new Set(data.flatMap((e) => e.themes))).sort();
        if (srcs.length > 0) setAvailableSources(srcs);
        setAvailableThemes(thms);
        setLoading(false);
      })
      .catch((err) => {
        if (id !== fetchIdRef.current) return;
        setError(err instanceof Error ? err.message : "Erro desconhecido");
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.statuses.join(","), filters.sources.join(",")]);

  // ── Client-side filtering (multi-select, themes, date range) ──────────────
  const filtered = allEditais.filter((e) => {
    if (filters.statuses.length > 1 && !filters.statuses.includes(e.status))
      return false;
    if (
      filters.sources.length > 0 &&
      !filters.sources.some((s) => e.fonte_recurso.includes(s))
    )
      return false;
    if (
      filters.themes.length > 0 &&
      !filters.themes.some((t) => e.themes.includes(t))
    )
      return false;
    return true;
  });

  return (
    <DashboardLayout
      title="Editais"
      sidebar={
        <SidebarFilter
          availableSources={availableSources}
          availableThemes={availableThemes}
          value={filters}
          onChange={setFilters}
          onReset={() => setFilters(EMPTY_FILTER)}
        />
      }
    >
      {/* Error banner */}
      {error && (
        <div className="mb-4 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-4 py-3 text-sm text-red-700 dark:text-red-300 font-sans">
          <strong>Erro ao carregar editais:</strong> {error}
          <br />
          <span className="text-xs">
            Verifique se o servidor FastAPI está rodando em{" "}
            <code className="font-data">http://localhost:8000</code>
          </span>
        </div>
      )}

      {/* KPI row (migrado do Dashboard) */}
      <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
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

      {/* Count row */}
      {!loading && !error && (
        <div className="mb-3 flex items-center justify-between">
          <p className="text-xs text-content-secondary font-sans">
            <span className="font-data font-medium text-content-primary">
              {filtered.length}
            </span>{" "}
            editais encontrados
            {allEditais.length !== filtered.length &&
              ` de ${allEditais.length} carregados`}
          </p>
        </div>
      )}

      <DataTable
        data={filtered}
        columns={COLUMNS}
        loading={loading}
        emptyMessage="Nenhum edital corresponde aos filtros selecionados."
        onRowClick={(row) => router.push(`/editais/${row.id}`)}
      />
    </DashboardLayout>
  );
}
