import { cn } from "@/lib/utils";

/**
 * Simple shimmer/pulse placeholder for loading states.
 * Pass `width`/`height` (any CSS size) or compose with Tailwind via `className`.
 */
export function Skeleton({
  width,
  height,
  className,
}: {
  width?: number | string;
  height?: number | string;
  className?: string;
}) {
  return (
    <div
      className={cn("animate-pulse bg-border/60 rounded", className)}
      style={{ width, height }}
    />
  );
}
