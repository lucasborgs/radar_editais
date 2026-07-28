"use client";

import { useState } from "react";
import { getChunkText } from "@/lib/api";

interface Citation {
  chunk_id: string;
  claim: string;
}

export function CitationTooltip({ citations }: { citations: Citation[] }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [chunkText, setChunkText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleMouseEnter = async (i: number, chunkId: string) => {
    setHoveredIndex(i);
    setChunkText(null);
    setLoading(true);
    try {
      const res = await getChunkText(chunkId);
      setChunkText(res.text);
    } catch {
      setChunkText(null);
    } finally {
      setLoading(false);
    }
  };

  const handleMouseLeave = () => {
    setHoveredIndex(null);
    setChunkText(null);
  };

  if (!citations || citations.length === 0) return null;

  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="text-xs font-semibold text-content-secondary font-sans mb-2">
        Fontes consultadas
      </p>
      <ol className="space-y-1.5">
        {citations.map((c, i) => (
          <li key={c.chunk_id} className="relative">
            <span
              className="text-xs font-sans text-content-secondary cursor-help"
              onMouseEnter={() => handleMouseEnter(i, c.chunk_id)}
              onMouseLeave={handleMouseLeave}
            >
              <span className="font-medium text-primary">[{i + 1}]</span>{" "}
              {c.claim}
            </span>
            {hoveredIndex === i && (
              <div className="absolute bottom-full left-0 mb-1 z-10 w-80 rounded-lg border border-border bg-surface px-3 py-2 shadow-lg">
                {loading ? (
                  <p className="text-[11px] text-content-secondary font-sans">carregando…</p>
                ) : (
                  <>
                    <p className="text-[11px] font-sans text-content-primary leading-relaxed line-clamp-4">
                      {chunkText}
                    </p>
                    <p className="mt-1 text-[10px] font-mono text-content-secondary">
                      {c.chunk_id}
                    </p>
                  </>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}