"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

export interface AgentTraceStep {
  name: string;
  input_summary?: string;
  output_summary?: string;
  duration_ms?: number;
}

/**
 * Collapsible panel showing the sequence of tool calls an agent made.
 * Shared between the front door and the writing workspace.
 * Each step shows tool name + expandable input/output.
 */
export function AgentTrace({
  steps,
  className,
}: {
  steps: AgentTraceStep[];
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  if (steps.length === 0) return null;

  return (
    <div className={cn("mt-2 text-xs font-sans", className)}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-content-secondary hover:text-content-primary transition-colors"
      >
        <span className="text-[10px]">{expanded ? "▼" : "▶"}</span>
        <span>
          {steps.length} {steps.length === 1 ? "passo" : "passos"}
        </span>
      </button>

      {expanded && (
        <div className="mt-1.5 space-y-1 border-l-2 border-border pl-3">
          {steps.map((step, i) => (
            <StepRow key={i} step={step} />
          ))}
        </div>
      )}
    </div>
  );
}

function StepRow({ step }: { step: AgentTraceStep }) {
  const [showDetails, setShowDetails] = useState(false);
  const icon = iconForTool(step.name);

  return (
    <div>
      <button
        type="button"
        onClick={() => setShowDetails(!showDetails)}
        className="flex items-center gap-1.5 text-content-secondary hover:text-content-primary transition-colors w-full text-left"
      >
        <span>{icon}</span>
        <span className="font-medium text-[11px]">{step.name}</span>
        {step.duration_ms != null && (
          <span className="text-[10px] text-content-tertiary ml-auto">
            {step.duration_ms < 1000
              ? `${step.duration_ms}ms`
              : `${(step.duration_ms / 1000).toFixed(1)}s`}
          </span>
        )}
      </button>

      {showDetails && (step.input_summary || step.output_summary) && (
        <div className="ml-5 mt-0.5 space-y-0.5 text-[10px] text-content-tertiary">
          {step.input_summary && (
            <p>
              <span className="font-medium">→ </span>
              {truncate(step.input_summary, 120)}
            </p>
          )}
          {step.output_summary && (
            <p>
              <span className="font-medium">← </span>
              {truncate(step.output_summary, 120)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function iconForTool(name: string): string {
  if (name.includes("search") || name.includes("find")) return "🔍";
  if (name.includes("save") || name.includes("write")) return "💾";
  if (name.includes("read") || name.includes("list")) return "📖";
  if (name.includes("note")) return "📝";
  if (name.includes("info") || name.includes("user")) return "❓";
  return "⚙️";
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}
