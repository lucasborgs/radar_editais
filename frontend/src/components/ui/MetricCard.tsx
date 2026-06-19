"use client";

import { cn } from "@/lib/utils";

interface TrendProps {
  direction: "up" | "down" | "neutral";
  label: string;
}

interface MetricCardProps {
  label: string;
  value: string | number;
  subtext?: string;
  icon?: React.ReactNode;
  trend?: TrendProps;
  className?: string;
}

const trendStyles: Record<TrendProps["direction"], string> = {
  up: "bg-[#1DB954]/15 text-[#169c46] dark:text-[#3ddc84]",
  down: "bg-red-500/15 text-red-700 dark:text-red-300",
  neutral: "bg-content-secondary/15 text-content-secondary",
};

const trendArrows: Record<TrendProps["direction"], string> = {
  up: "↑",
  down: "↓",
  neutral: "→",
};

export function MetricCard({
  label,
  value,
  subtext,
  icon,
  trend,
  className,
}: MetricCardProps) {
  return (
    <div
      className={cn(
        "relative bg-surface rounded-xl shadow-card border border-border overflow-hidden",
        className
      )}
    >
      {/* Green accent top border */}
      <div className="absolute top-0 left-0 right-0 h-1 bg-primary rounded-t-xl" />

      <div className="p-5 pt-6">
        {/* Label row */}
        <div className="flex items-center justify-between mb-3">
          <p className="text-xs font-semibold uppercase tracking-wider text-content-secondary font-sans">
            {label}
          </p>
          {icon && (
            <span className="text-primary opacity-70 text-lg">{icon}</span>
          )}
        </div>

        {/* Main value — Fragment Mono */}
        <p className="font-data text-3xl font-normal text-content-primary leading-none">
          {value}
        </p>

        {/* Subtext */}
        {subtext && (
          <p className="mt-1 text-xs text-content-secondary font-sans">
            {subtext}
          </p>
        )}

        {/* Trend badge */}
        {trend && (
          <div className="mt-3">
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium font-sans",
                trendStyles[trend.direction]
              )}
            >
              <span>{trendArrows[trend.direction]}</span>
              {trend.label}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
