import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Presentational chat bubble shared between the KG chat (dashboard) and the
 * writing chat. It does NOT decide how the content is rendered (markdown vs
 * pre) — the caller passes already-rendered `children`.
 */
export function ChatBubble({
  role,
  children,
  timestamp,
  footer,
  className,
}: {
  role: "user" | "assistant";
  children: ReactNode;
  timestamp?: string;
  footer?: ReactNode;
  className?: string;
}) {
  const isUser = role === "user";
  return (
    <div className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[82%] rounded-2xl px-4 py-3 text-sm font-sans leading-relaxed",
          isUser
            ? "bg-primary text-white rounded-br-sm"
            : "bg-white border border-border text-content-primary rounded-bl-sm",
          className
        )}
      >
        {children}
        {timestamp && (
          <p
            className={cn(
              "text-[10px] mt-1.5",
              isUser ? "text-white/60" : "text-content-secondary"
            )}
          >
            {timestamp}
          </p>
        )}
      </div>
      {footer}
    </div>
  );
}
