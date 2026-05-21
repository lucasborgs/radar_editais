"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import dynamic from "next/dynamic";
import type { GraphData, GraphNode } from "@/lib/api";

// ForceGraph2D toca em window/canvas — desabilita SSR.
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
});

const NODE_STYLE: Record<string, { color: string; r: number }> = {
  "edital-ABERTA": { color: "#1DB954", r: 6 },
  "edital-ENCERRADA": { color: "#6B7280", r: 5 },
  "edital-Desconhecido": { color: "#3b82f6", r: 5 },
  tema: { color: "#f59e0b", r: 9 },
  publico: { color: "#8b5cf6", r: 8 },
  fonte: { color: "#0ea5e9", r: 9 },
  subprograma: { color: "#a855f7", r: 8 },
  home: { color: "#111827", r: 11 },
  outro: { color: "#9ca3af", r: 5 },
};

function styleFor(node: GraphNode) {
  if (node.type === "edital") {
    return (
      NODE_STYLE[`edital-${node.status}`] ?? NODE_STYLE["edital-Desconhecido"]
    );
  }
  return NODE_STYLE[node.type] ?? NODE_STYLE.outro;
}

interface Props {
  data: GraphData;
  onNodeClick: (node: GraphNode) => void;
}

export function KnowledgeGraph({ data, onNodeClick }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setDims({ w: el.clientWidth, h: el.clientHeight });
    });
    ro.observe(el);
    setDims({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const handleNodeClick = useCallback(
    (node: object) => {
      const n = node as GraphNode;
      const clickable = ["edital", "tema", "publico", "subprograma", "home"];
      if (clickable.includes(n.type)) onNodeClick(n);
    },
    [onNodeClick]
  );

  const paintNode = useCallback(
    (node: object, ctx: CanvasRenderingContext2D, scale: number) => {
      const n = node as GraphNode & { x: number; y: number };
      const { color, r } = styleFor(n);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      // Rótulo só aparece com zoom suficiente (evita poluição).
      if (scale > 1.6) {
        const label =
          n.label.length > 32 ? n.label.slice(0, 32) + "…" : n.label;
        ctx.font = `${10 / scale}px Inter, sans-serif`;
        ctx.fillStyle = "#1f2937";
        ctx.textAlign = "center";
        ctx.fillText(label, n.x, n.y + r + 8 / scale);
      }
    },
    []
  );

  return (
    <div ref={wrapRef} className="w-full h-full">
      {dims.w > 0 && (
        <ForceGraph2D
          width={dims.w}
          height={dims.h}
          graphData={data}
          nodeRelSize={6}
          nodeCanvasObject={paintNode}
          nodePointerAreaPaint={(node, color, ctx) => {
            const n = node as GraphNode & { x: number; y: number };
            const { r } = styleFor(n);
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 2, 0, 2 * Math.PI);
            ctx.fill();
          }}
          linkColor={() => "#e5e7eb"}
          linkWidth={1}
          onNodeClick={handleNodeClick}
          cooldownTicks={100}
          d3VelocityDecay={0.3}
        />
      )}
    </div>
  );
}
