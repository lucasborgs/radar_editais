"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Generic scroll container for a chat message list. Owns the scroll ref and
 * auto-scrolls to the bottom whenever its contents change. Callers keep their
 * own message-to-bubble mapping and pass the rendered bubbles as `children`.
 *
 * `deps` lets callers trigger a re-scroll on values that aren't part of the
 * children identity (e.g. a "sending" flag that toggles a typing indicator).
 */
export function ChatMessageList({
  children,
  className,
  deps = [],
}: {
  children: ReactNode;
  className?: string;
  deps?: unknown[];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [children, ...deps]);

  return (
    <div
      ref={scrollRef}
      className={cn("flex-1 overflow-y-auto p-4 space-y-3", className)}
    >
      {children}
    </div>
  );
}
