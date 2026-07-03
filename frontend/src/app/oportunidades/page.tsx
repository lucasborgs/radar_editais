"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { DataTable, Modal, MetricCard, StatusBadge } from "@/components/ui";
import type { Column } from "@/components/ui";
import { truncate } from "@/lib/utils";
import { getDashboardStats, getOpportunities } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { DashboardStats, EditalStatus } from "@/types/edital";
import type { OpportunityEntry } from "@/types/oportunidade";

type TypeFilter = "all" | "edital" | "programa" | "investidor";

const TYPE_OPTIONS: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "edital", label: "Editais" },
  { value: "programa", label: "Programas" },
  { value: "investidor", label: "Investidores" },
];

const TYPE_BADGE: Record<string, { label: string; className: string }> = {
  edital: { label: "Edital", className: "bg-primary/15 text-primary" },
  programa: { label: "Programa", className: "bg-blue-500/15 text-blue-700 dark:text-blue-300" },
  investidor: { label: "Investidor", className: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
};

function TypeBadge({ type }: { type: string }) {
  const cfg = TYPE_BADGE[type] ?? { label: type, className: "bg-content-secondary/15 text-content-secondary" };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium font-sans ${cfg.className}`}>
      {cfg.label}
    </span>
  );
}

const COLUMNS: Column<OpportunityEntry>[] = [
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
    key: "type",
    header: "Tipo",
    render: (v) => <TypeBadge type={String(v)} />,
  },
  {
    key: "status",
    header: "Status",
    render: (v, row) =>
      row.type === "edital" ? <StatusBadge status={(v as EditalStatus) ?? "Desconhecido"} /> : <span className="text-xs text-content-secondary">—</span>,
  },
  {
    key: "deadline",
    header: "Prazo",
    numeric: true,
    render: (v, row) =>
      row.type === "edital" ? <span className="text-xs">{String(v || "—")}</span> : <span className="text-xs text-content-secondary">—</span>,
  },
  {
    key: "themes",
    header: "Temas",
    render: (v) => {
      const arr = v as string[];
      return (
        <span className="text-xs text-content-secondary">
          {arr.length > 0 ? truncate(arr.join(", "), 40) : "—"}
        </span>
      );
    },
  },
];

export default function OportunidadesPage() {
  const router = useRouter();
  const { data: stats } = useAsync<DashboardStats>(() => getDashboardStats(), []);
  const { data: opportunities, loading, error } = useAsync<OpportunityEntry[]>(() => getOpportunities({ limit: 500 }), []);
  const [typeFilter, setTypeFilter] = useState<TypeFilter>("all");
  const [themeQuery, setThemeQuery] = useState("");
  const [detailItem, setDetailItem] = useState<OpportunityEntry | null>(null);

  const filtered = useMemo(() => {
    let items = opportunities ?? [];
    if (typeFilter !== "all") {
      items = items.filter((o) => o.type === typeFilter);
    }
    if (themeQuery.trim()) {
      const q = themeQuery.trim().toLowerCase();
      items = items.filter((o) =>
        o.themes.some((t) => t.toLowerCase().includes(q)),
      );
    }
    return items;
  }, [opportunities, typeFilter, themeQuery]);

  const handleRowClick = (row: OpportunityEntry) => {
    if (row.type === "edital") {
      router.push(`/editais/${row.id}`);
    } else {
      setDetailItem(row);
    }
  };

  return (
    <DashboardLayout title="Oportunidades">
      <div className="space-y-5">
        {/* KPI row */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <MetricCard
            label="Total de Oportunidades"
            value={stats ? stats.total_oportunidades : "—"}
            subtext="editais + programas + investidores"
          />
          <MetricCard
            label="Editais Abertos"
            value={stats ? (stats.by_status["ABERTA"] ?? 0) : "—"}
            subtext="com prazo vigente"
          />
          <MetricCard
            label="Programas"
            value={stats ? stats.n_programas : "—"}
            subtext="linhas de fomento"
          />
          <MetricCard
            label="Investidores"
            value={stats ? stats.n_investidores : "—"}
            subtext="parceiros de capital"
          />
        </div>

        {/* Filter pills + theme search */}
        <div className="flex flex-wrap items-center gap-3">
          {TYPE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setTypeFilter(opt.value)}
              className={`rounded-full px-4 py-1.5 text-sm font-medium font-sans transition-colors ${
                typeFilter === opt.value
                  ? "bg-primary text-white"
                  : "bg-content-secondary/10 text-content-secondary hover:bg-content-secondary/20"
              }`}
            >
              {opt.label}
            </button>
          ))}
          <div className="ml-auto">
            <input
              value={themeQuery}
              onChange={(e) => setThemeQuery(e.target.value)}
              placeholder="Filtrar por tema…"
              className="w-48 rounded-lg border border-border bg-surface px-3 py-1.5 text-sm font-sans text-content-primary placeholder:text-content-secondary outline-none focus:border-primary transition-colors"
            />
          </div>
        </div>

        {/* Count row */}
        {!loading && !error && (
          <div className="flex items-center justify-between">
            <p className="text-xs text-content-secondary font-sans">
              <span className="font-data font-medium text-content-primary">
                {filtered.length}
              </span>{" "}
              oportunidades encontradas
              {opportunities && filtered.length !== opportunities.length &&
                ` de ${opportunities.length} carregadas`}
            </p>
          </div>
        )}

        <DataTable
          data={filtered}
          columns={COLUMNS}
          loading={loading}
          emptyMessage="Nenhuma oportunidade corresponde aos filtros selecionados."
          onRowClick={handleRowClick}
        />
      </div>

      {/* Detail modal for programas/investidores */}
      <Modal
        open={!!detailItem}
        onClose={() => setDetailItem(null)}
        title={detailItem?.title}
        size="md"
      >
        {detailItem && (
          <div className="space-y-4">
            <div>
              <TypeBadge type={detailItem.type} />
            </div>
            {detailItem.description && (
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-content-secondary mb-1 font-sans">
                  Descrição
                </h4>
                <p className="text-sm text-content-primary font-sans whitespace-pre-wrap">
                  {detailItem.description}
                </p>
              </div>
            )}
            {detailItem.themes.length > 0 && (
              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-content-secondary mb-1 font-sans">
                  Temas
                </h4>
                <div className="flex flex-wrap gap-1.5">
                  {detailItem.themes.map((t) => (
                    <span
                      key={t}
                      className="inline-flex items-center rounded-full bg-content-secondary/10 px-2.5 py-0.5 text-xs font-medium text-content-secondary font-sans"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>
    </DashboardLayout>
  );
}
