"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { DataTable, MetricCard, StatusBadge } from "@/components/ui";
import type { Column } from "@/components/ui";
import { truncate, parseDeadline } from "@/lib/utils";
import { getDashboardStats, getOpportunities } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { temporalBadge, temporalDeadlineText } from "@/lib/opportunity-temporal";
import type { DashboardStats, EditalStatus } from "@/types/edital";
import type { OpportunityEntry } from "@/types/oportunidade";

type TypeFilter = "all" | "edital" | "programa" | "investidor" | "ict";

const TYPE_OPTIONS: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "Todos" },
  { value: "edital", label: "Editais" },
  { value: "programa", label: "Programas" },
  { value: "investidor", label: "Investidores" },
  { value: "ict", label: "ICTs" },
];

const TYPE_BADGE: Record<string, { label: string; className: string }> = {
  edital: { label: "Edital", className: "bg-primary/15 text-primary" },
  programa: { label: "Programa", className: "bg-blue-500/15 text-blue-700 dark:text-blue-300" },
  investidor: { label: "Investidor", className: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  ict: { label: "ICT", className: "bg-purple-500/15 text-purple-700 dark:text-purple-300" },
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
    key: "title",
    header: "Título",
    render: (v, row) => {
      const title = String(v);
      // Defesa: se o título parece cabeçalho de seção (ex: "2. Áreas do
      // conhecimento"), usa o ID como fallback visual.
      const display = /^\d+[\.\)]\s/.test(title)
        ? `[${row.id.split(":").pop()}]`
        : truncate(title, 45);
      const disambiguator = (row as unknown as Record<string, unknown>)._disambiguator as string | undefined;
      return (
        <span className="font-medium">
          {display}
          {disambiguator && (
            <span className="ml-1.5 text-[11px] text-content-secondary font-normal">
              {disambiguator}
            </span>
          )}
        </span>
      );
    },
  },
  {
    key: "type",
    header: "Tipo",
    render: (v) => <TypeBadge type={String(v)} />,
  },
  {
    key: "status",
    header: "Status",
    render: (_v, row) => {
      if (row.type !== "edital" && row.type !== "programa") {
        return <span className="text-xs text-content-secondary">—</span>;
      }
      const badge = temporalBadge(row);
      if (badge.tone === "review") {
        return (
          <span className="inline-flex items-center rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium font-sans text-amber-700 dark:text-amber-300">
            {badge.label}
          </span>
        );
      }
      const status = (badge.tone === "active" ? "ABERTA" : "ENCERRADA") as EditalStatus;
      return <StatusBadge status={status} />;
    },
  },
  {
    key: "deadline",
    header: "Prazo",
    numeric: true,
    render: (_v, row) => {
      if (row.type !== "edital" && row.type !== "programa") {
        return <span className="text-xs text-content-secondary">—</span>;
      }
      const temporal = temporalDeadlineText(row);
      if (!temporal) return <span className="text-xs text-content-secondary">—</span>;
      const parsed = parseDeadline(temporal);
      return (
        <span className={`text-xs ${parsed ? "font-data" : ""}`}>
          {parsed ?? temporal}
        </span>
      );
    },
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

  const filtered = useMemo(() => {
    let items = opportunities ?? [];

    // Apply type/theme filters
    if (typeFilter !== "all") {
      items = items.filter((o) => o.type === typeFilter);
    }
    if (themeQuery.trim()) {
      const q = themeQuery.trim().toLowerCase();
      items = items.filter((o) =>
        o.themes.some((t) => t.toLowerCase().includes(q)),
      );
    }

    // Disambiguate duplicate titles: detect collisions, annotate entry
    // with _disambiguator from macro_temas[0] → themes[0] → fonte_recurso[0]
    // → source prefix → id suffix
    const titleCounts = new Map<string, number>();
    for (const o of items) {
      titleCounts.set(o.title, (titleCounts.get(o.title) ?? 0) + 1);
    }
    return items.map((o) => {
      if ((titleCounts.get(o.title) ?? 0) <= 1) return o;
      const mt = (o as unknown as Record<string, unknown>).macro_temas as string[] | undefined;
      const idParts = o.id.split(":");
      const pick = mt?.[0] ?? o.themes[0] ?? o.fonte_recurso?.[0] ?? idParts[0] ?? idParts.pop() ?? "";
      return { ...o, _disambiguator: pick };
    });
  }, [opportunities, typeFilter, themeQuery]);

  // D1/PR8: toda oportunidade (edital, programa, investidor) abre a ficha
  // unificada. ICTs não têm ficha detalhada (são Ator, não Oportunidade).
  const handleRowClick = (row: OpportunityEntry) => {
    if (row.type === "ict") return;
    router.push(`/oportunidades/${encodeURIComponent(row.id)}`);
  };

  return (
    <DashboardLayout title="Ecossistema">
      <div className="space-y-5">
        {/* KPI row */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
          <MetricCard
            label="Itens no ecossistema"
            value={stats ? stats.total_oportunidades : "—"}
            subtext="editais + programas + investidores + ICTs"
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
          <MetricCard
            label="ICTs"
            value={stats ? stats.n_icts : "—"}
            subtext="instituições parceiras"
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
              itens encontrados
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
    </DashboardLayout>
  );
}
