"use client";

import { cn } from "@/lib/utils";

export interface Column<T> {
  key: keyof T | string;
  header: string;
  render?: (value: unknown, row: T) => React.ReactNode;
  className?: string;
  headerClassName?: string;
  /** Renders cell value in Fragment Mono (tabular-nums) */
  numeric?: boolean;
  sortable?: boolean;
}

interface DataTableProps<T extends { id: string }> {
  data: T[];
  columns: Column<T>[];
  onRowClick?: (row: T) => void;
  loading?: boolean;
  emptyMessage?: string;
  className?: string;
}

function SkeletonRow({ cols }: { cols: number }) {
  return (
    <tr className="animate-pulse">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3.5 border-b border-border/60">
          <div className="h-4 bg-gray-200 rounded w-3/4" />
        </td>
      ))}
    </tr>
  );
}

export function DataTable<T extends { id: string }>({
  data,
  columns,
  onRowClick,
  loading = false,
  emptyMessage = "Nenhum dado encontrado.",
  className,
}: DataTableProps<T>) {
  function getCellValue(row: T, key: keyof T | string): unknown {
    return row[key as keyof T];
  }

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-white",
        className
      )}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          {/* Header */}
          <thead>
            <tr className="bg-gray-50 border-b border-border">
              {columns.map((col) => (
                <th
                  key={String(col.key)}
                  className={cn(
                    "px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-content-secondary font-sans whitespace-nowrap",
                    col.headerClassName
                  )}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>

          {/* Body */}
          <tbody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <SkeletonRow key={i} cols={columns.length} />
              ))
            ) : data.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-4 py-12 text-center text-content-secondary font-sans text-sm"
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              data.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => onRowClick?.(row)}
                  className={cn(
                    "border-b border-border/60 last:border-0",
                    onRowClick &&
                      "hover:bg-gray-50 cursor-pointer transition-colors"
                  )}
                >
                  {columns.map((col) => {
                    const rawValue = getCellValue(row, col.key);
                    return (
                      <td
                        key={String(col.key)}
                        className={cn(
                          "px-4 py-3.5 text-content-primary align-middle",
                          col.numeric && "font-data",
                          !col.numeric && "font-sans",
                          col.className
                        )}
                      >
                        {col.render
                          ? col.render(rawValue, row)
                          : (rawValue as React.ReactNode) ?? "—"}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
