"use client";

import * as Checkbox from "@radix-ui/react-checkbox";
import { cn } from "@/lib/utils";
import { SOURCE_COLORS, STATUS_CONFIG } from "@/lib/constants";
import type { EditalStatus } from "@/types/edital";

const ALL_STATUSES: EditalStatus[] = [
  "ABERTA",
  "ENCERRADA",
  "Desconhecido",
];

export interface FilterState {
  statuses: EditalStatus[];
  sources: string[];
  themes: string[];
  deadlineBefore: string | null;
  deadlineAfter: string | null;
}

export const EMPTY_FILTER: FilterState = {
  statuses: [],
  sources: [],
  themes: [],
  deadlineBefore: null,
  deadlineAfter: null,
};

interface SidebarFilterProps {
  availableSources: string[];
  availableThemes: string[];
  value: FilterState;
  onChange: (updated: FilterState) => void;
  onReset: () => void;
  className?: string;
}

function FilterSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wider text-content-secondary mb-2 font-sans">
        {title}
      </p>
      {children}
    </div>
  );
}

function CheckItem({
  checked,
  onCheckedChange,
  label,
  dot,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  label: string;
  dot?: string;
}) {
  return (
    <label className="flex items-center gap-2 cursor-pointer py-0.5 group">
      <Checkbox.Root
        checked={checked}
        onCheckedChange={onCheckedChange}
        className={cn(
          "w-4 h-4 rounded border-2 flex items-center justify-center shrink-0 transition-colors",
          checked
            ? "bg-primary border-primary"
            : "border-border bg-surface group-hover:border-primary/50"
        )}
      >
        <Checkbox.Indicator>
          {/* White checkmark SVG */}
          <svg
            className="w-2.5 h-2.5 text-white"
            fill="none"
            viewBox="0 0 10 10"
            stroke="currentColor"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M1.5 5l2.5 2.5 4.5-4.5" />
          </svg>
        </Checkbox.Indicator>
      </Checkbox.Root>

      {dot && (
        <span
          className="w-2 h-2 rounded-full shrink-0"
          style={{ backgroundColor: dot }}
        />
      )}
      <span className="text-sm text-content-primary font-sans">{label}</span>
    </label>
  );
}

function toggle<T>(arr: T[], item: T): T[] {
  return arr.includes(item) ? arr.filter((x) => x !== item) : [...arr, item];
}

export function SidebarFilter({
  availableSources,
  availableThemes,
  value,
  onChange,
  onReset,
  className,
}: SidebarFilterProps) {
  const hasActiveFilters =
    value.statuses.length > 0 ||
    value.sources.length > 0 ||
    value.themes.length > 0 ||
    value.deadlineBefore ||
    value.deadlineAfter;

  return (
    <div
      className={cn(
        "bg-surface rounded-xl border border-border p-4 space-y-5",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-content-primary font-sans">
          Filtros
        </p>
        {hasActiveFilters && (
          <button
            onClick={onReset}
            className="text-xs text-primary hover:text-primary-hover font-medium font-sans transition-colors"
          >
            Limpar
          </button>
        )}
      </div>

      {/* Status */}
      <FilterSection title="Status">
        <div className="space-y-1">
          {ALL_STATUSES.map((s) => (
            <CheckItem
              key={s}
              checked={value.statuses.includes(s)}
              onCheckedChange={() =>
                onChange({ ...value, statuses: toggle(value.statuses, s) })
              }
              label={STATUS_CONFIG[s].label}
            />
          ))}
        </div>
      </FilterSection>

      {/* Source */}
      <FilterSection title="Fonte">
        <div className="space-y-1">
          {availableSources.map((src) => (
            <CheckItem
              key={src}
              checked={value.sources.includes(src)}
              onCheckedChange={() =>
                onChange({ ...value, sources: toggle(value.sources, src) })
              }
              label={src}
              dot={SOURCE_COLORS[src]}
            />
          ))}
        </div>
      </FilterSection>

      {/* Themes */}
      {availableThemes.length > 0 && (
        <FilterSection title="Temática">
          <div className="space-y-1 max-h-48 overflow-y-auto pr-1">
            {availableThemes.map((theme) => (
              <CheckItem
                key={theme}
                checked={value.themes.includes(theme)}
                onCheckedChange={() =>
                  onChange({ ...value, themes: toggle(value.themes, theme) })
                }
                label={theme}
              />
            ))}
          </div>
        </FilterSection>
      )}

      {/* Deadline range */}
      <FilterSection title="Prazo de Inscrição">
        <div className="space-y-2">
          <div>
            <label className="text-xs text-content-secondary font-sans block mb-1">
              A partir de
            </label>
            <input
              type="date"
              value={value.deadlineAfter ?? ""}
              onChange={(e) =>
                onChange({
                  ...value,
                  deadlineAfter: e.target.value || null,
                })
              }
              className="w-full rounded-lg border border-border px-3 py-1.5 text-sm font-sans text-content-primary bg-surface focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-colors"
            />
          </div>
          <div>
            <label className="text-xs text-content-secondary font-sans block mb-1">
              Até
            </label>
            <input
              type="date"
              value={value.deadlineBefore ?? ""}
              onChange={(e) =>
                onChange({
                  ...value,
                  deadlineBefore: e.target.value || null,
                })
              }
              className="w-full rounded-lg border border-border px-3 py-1.5 text-sm font-sans text-content-primary bg-surface focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary transition-colors"
            />
          </div>
        </div>
      </FilterSection>
    </div>
  );
}
