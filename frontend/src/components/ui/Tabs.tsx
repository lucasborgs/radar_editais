"use client";

import { useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export interface TabItem {
  value: string;
  label: React.ReactNode;
  content: React.ReactNode;
}

interface TabsProps {
  items: TabItem[];
  /** Controlled active value. Omit for uncontrolled (uses defaultValue). */
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  className?: string;
}

/**
 * Compact tabs. Pass `items: { value, label, content }[]`.
 *
 * Uncontrolled:  <Tabs items={items} defaultValue="url" />
 * Controlled:    <Tabs items={items} value={v} onValueChange={setV} />
 *
 * Active tab underlined + colored with primary; inactive text-content-secondary.
 * Arrow keys move focus/selection between tabs (roving tabindex).
 */
export function Tabs({
  items,
  value,
  defaultValue,
  onValueChange,
  className,
}: TabsProps) {
  const baseId = useId();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [internal, setInternal] = useState<string>(
    defaultValue ?? items[0]?.value ?? ""
  );

  const active = value ?? internal;

  const select = (next: string) => {
    if (value === undefined) setInternal(next);
    onValueChange?.(next);
  };

  const onKeyDown = (e: React.KeyboardEvent, index: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const dir = e.key === "ArrowRight" ? 1 : -1;
    const next = (index + dir + items.length) % items.length;
    tabRefs.current[next]?.focus();
    select(items[next].value);
  };

  const activeItem = items.find((it) => it.value === active);

  return (
    <div className={cn("w-full", className)}>
      <div
        role="tablist"
        aria-orientation="horizontal"
        className="flex items-center gap-1 border-b border-border"
      >
        {items.map((item, i) => {
          const selected = item.value === active;
          return (
            <button
              key={item.value}
              ref={(el) => {
                tabRefs.current[i] = el;
              }}
              role="tab"
              type="button"
              id={`${baseId}-tab-${item.value}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${item.value}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => select(item.value)}
              onKeyDown={(e) => onKeyDown(e, i)}
              className={cn(
                "relative -mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:text-primary",
                selected
                  ? "border-primary text-primary"
                  : "border-transparent text-content-secondary hover:text-content-primary"
              )}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      {activeItem && (
        <div
          role="tabpanel"
          id={`${baseId}-panel-${activeItem.value}`}
          aria-labelledby={`${baseId}-tab-${activeItem.value}`}
          tabIndex={0}
          className="pt-4 focus:outline-none"
        >
          {activeItem.content}
        </div>
      )}
    </div>
  );
}
