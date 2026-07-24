"use client";

import { useState } from "react";
import type { FieldProvenance } from "@/types/edital";

interface ProvenanceHintProps {
  provenance: FieldProvenance;
  curationLabel?: "embrapii" | "catalogo";
}

export function ProvenanceHint({ provenance, curationLabel }: ProvenanceHintProps) {
  const [show, setShow] = useState(false);

  if (!provenance || !provenance.state) return null;

  const { state, citations } = provenance;

  if (state === "unknown" || state === "legacy") return null;

  let tooltipText: string;

  if (curationLabel === "embrapii") {
    tooltipText = "Registro oficial EMBRAPII";
  } else if (curationLabel === "catalogo") {
    tooltipText = "Catálogo curado do Radar";
  } else if (state === "stated" && citations && citations.length > 0) {
    const c = citations[0];
    const parts = [`Fonte: ${c.document}`];
    if (c.page) parts.push(`p. ${c.page}`);
    tooltipText = parts.join(", ");
    if (c.collected_at) {
      try {
        const date = new Date(c.collected_at).toLocaleDateString("pt-BR");
        tooltipText += ` \u00B7 ${date}`;
      } catch {}
    }
  } else if (state === "inferred") {
    tooltipText = "Inferido automaticamente";
  } else {
    tooltipText = state;
  }

  return (
    <span className="relative inline-flex items-center">
      <span
        className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full bg-content-secondary/15 text-content-secondary cursor-help text-[10px] font-bold leading-none select-none hover:bg-primary/15 hover:text-primary transition-colors"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onFocus={() => setShow(true)}
        onBlur={() => setShow(false)}
        tabIndex={0}
        role="tooltip"
      >
        ?
      </span>
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 px-2 py-1 rounded-md bg-content-primary text-white text-[11px] font-sans whitespace-nowrap shadow-md z-20 pointer-events-none">
          {tooltipText}
          <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-content-primary" />
        </span>
      )}
    </span>
  );
}