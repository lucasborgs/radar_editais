"use client";

import {
  forwardRef,
  useEffect,
  useRef,
  useState,
  useCallback,
  type KeyboardEvent,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/utils";
import { getLibraryItems } from "@/lib/api";
import type { ContentItemSummary } from "@/types/api";

/**
 * Textarea with @-mention autocomplete against the user's library.
 * On selection inserts a plain token `@<item_id>` into the text — the
 * backend writing service resolves it server-side.
 */

interface MentionsTextareaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> {
  value: string;
  onChange: (v: string) => void;
  onSubmitEnter?: () => void;
  token: string | null;
}

interface QueryState {
  active: boolean;
  start: number; // position of the @ char in the textarea value
  term: string;
}

function findActiveQuery(value: string, caret: number): QueryState {
  // walk backwards from caret looking for an @ not preceded by a word char
  for (let i = caret - 1; i >= 0; i--) {
    const ch = value[i];
    if (ch === "@") {
      const before = i === 0 ? " " : value[i - 1];
      if (/[\s\n([{]/.test(before) || i === 0) {
        return { active: true, start: i, term: value.slice(i + 1, caret) };
      }
      break;
    }
    if (ch === " " || ch === "\n") break;
  }
  return { active: false, start: -1, term: "" };
}

export const MentionsTextarea = forwardRef<HTMLTextAreaElement, MentionsTextareaProps>(
  function MentionsTextarea(
    { value, onChange, onSubmitEnter, token, className, onKeyDown: onKeyDownProp, ...rest },
    forwardedRef
  ) {
    const innerRef = useRef<HTMLTextAreaElement | null>(null);
    const setRef = useCallback(
      (el: HTMLTextAreaElement | null) => {
        innerRef.current = el;
        if (typeof forwardedRef === "function") {
          forwardedRef(el);
        } else if (forwardedRef) {
          (forwardedRef as React.MutableRefObject<HTMLTextAreaElement | null>).current = el;
        }
      },
      [forwardedRef]
    );

    const [query, setQuery] = useState<QueryState>({ active: false, start: -1, term: "" });
    const [suggestions, setSuggestions] = useState<ContentItemSummary[]>([]);
    const [highlight, setHighlight] = useState(0);
    const [loading, setLoading] = useState(false);
    const containerRef = useRef<HTMLDivElement | null>(null);
    const dropdownRef = useRef<HTMLDivElement | null>(null);

    // Caret coordinates for positioning the popover
    const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);

    // Debounced fetch when term changes
    useEffect(() => {
      if (!query.active || !token) {
        setSuggestions([]);
        return;
      }
      let cancelled = false;
      setLoading(true);
      const handle = setTimeout(async () => {
        try {
          const items = await getLibraryItems(token, undefined, query.term || undefined);
          if (!cancelled) {
            setSuggestions(items.slice(0, 8));
            setHighlight(0);
          }
        } catch {
          if (!cancelled) setSuggestions([]);
        } finally {
          if (!cancelled) setLoading(false);
        }
      }, 150);
      return () => {
        cancelled = true;
        clearTimeout(handle);
      };
    }, [query.active, query.term, token]);

    // Outside click → dismiss
    useEffect(() => {
      if (!query.active) return;
      function onPointer(e: MouseEvent) {
        const t = e.target as Node | null;
        if (
          t &&
          !innerRef.current?.contains(t) &&
          !dropdownRef.current?.contains(t)
        ) {
          setQuery({ active: false, start: -1, term: "" });
        }
      }
      window.addEventListener("mousedown", onPointer);
      return () => window.removeEventListener("mousedown", onPointer);
    }, [query.active]);

    function updateCoords(textarea: HTMLTextAreaElement, atIndex: number) {
      // Use a hidden mirror div to measure the caret position
      const mirror = document.createElement("div");
      const style = window.getComputedStyle(textarea);
      const props = [
        "boxSizing",
        "width",
        "fontFamily",
        "fontSize",
        "fontWeight",
        "lineHeight",
        "letterSpacing",
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
        "borderTopWidth",
        "borderRightWidth",
        "borderBottomWidth",
        "borderLeftWidth",
        "whiteSpace",
        "wordWrap",
      ] as const;
      props.forEach((p) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (mirror.style as any)[p] = (style as any)[p];
      });
      mirror.style.position = "absolute";
      mirror.style.visibility = "hidden";
      mirror.style.whiteSpace = "pre-wrap";
      mirror.style.wordWrap = "break-word";
      mirror.style.top = "0";
      mirror.style.left = "-9999px";
      mirror.textContent = textarea.value.slice(0, atIndex);
      const span = document.createElement("span");
      span.textContent = textarea.value.slice(atIndex) || ".";
      mirror.appendChild(span);
      document.body.appendChild(mirror);
      const spanRect = span.getBoundingClientRect();
      const mirrorRect = mirror.getBoundingClientRect();
      const tRect = textarea.getBoundingClientRect();
      const top = spanRect.top - mirrorRect.top - textarea.scrollTop + tRect.top + 22;
      const left = spanRect.left - mirrorRect.left + tRect.left;
      document.body.removeChild(mirror);
      setCoords({ top, left });
    }

    function handleChange(next: string, caret: number) {
      onChange(next);
      const q = findActiveQuery(next, caret);
      setQuery(q);
      if (q.active && innerRef.current) {
        updateCoords(innerRef.current, q.start);
      } else {
        setCoords(null);
      }
    }

    function applyMention(item: ContentItemSummary) {
      const el = innerRef.current;
      if (!el || !query.active) return;
      const before = value.slice(0, query.start);
      const after = value.slice(el.selectionStart ?? value.length);
      const mentionToken = `@${item.id} `;
      const next = `${before}${mentionToken}${after}`;
      onChange(next);
      setQuery({ active: false, start: -1, term: "" });
      setCoords(null);
      // restore caret after token
      setTimeout(() => {
        if (innerRef.current) {
          const pos = before.length + mentionToken.length;
          innerRef.current.focus();
          innerRef.current.setSelectionRange(pos, pos);
        }
      }, 0);
    }

    function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
      if (query.active && suggestions.length > 0) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setHighlight((h) => (h + 1) % suggestions.length);
          return;
        }
        if (e.key === "ArrowUp") {
          e.preventDefault();
          setHighlight((h) => (h - 1 + suggestions.length) % suggestions.length);
          return;
        }
        if (e.key === "Enter" || e.key === "Tab") {
          e.preventDefault();
          applyMention(suggestions[highlight]);
          return;
        }
        if (e.key === "Escape") {
          e.preventDefault();
          setQuery({ active: false, start: -1, term: "" });
          setCoords(null);
          return;
        }
      }
      if (e.key === "Enter" && !e.shiftKey && onSubmitEnter && !query.active) {
        e.preventDefault();
        onSubmitEnter();
        return;
      }
      onKeyDownProp?.(e);
    }

    return (
      <div ref={containerRef} className="relative flex-1">
        <textarea
          {...rest}
          ref={setRef}
          value={value}
          onChange={(e) => handleChange(e.target.value, e.target.selectionStart ?? e.target.value.length)}
          onKeyDown={handleKeyDown}
          onClick={(e) => {
            const el = e.currentTarget;
            const q = findActiveQuery(el.value, el.selectionStart ?? el.value.length);
            setQuery(q);
            if (q.active) updateCoords(el, q.start);
            else setCoords(null);
          }}
          className={className}
        />
        {query.active && coords && (
          <div
            ref={dropdownRef}
            style={{ position: "fixed", top: coords.top, left: coords.left, zIndex: 60 }}
            className={cn(
              "w-72 max-h-64 overflow-y-auto bg-surface border border-border rounded-lg shadow-lg",
              "text-sm font-sans"
            )}
          >
            <div className="px-3 py-1.5 text-[10px] uppercase tracking-wide text-content-secondary border-b border-border">
              Biblioteca {loading ? "· buscando…" : ""}
            </div>
            {suggestions.length === 0 && !loading && (
              <div className="px-3 py-3 text-xs text-content-secondary">
                Nenhum item encontrado para “{query.term}”.
              </div>
            )}
            {suggestions.map((item, idx) => (
              <button
                key={item.id}
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  applyMention(item);
                }}
                onMouseEnter={() => setHighlight(idx)}
                className={cn(
                  "w-full text-left px-3 py-2 flex flex-col gap-0.5 transition-colors",
                  idx === highlight ? "bg-primary/10" : "hover:bg-content-secondary/5"
                )}
              >
                <span className="text-content-primary font-medium truncate">{item.title}</span>
                <span className="text-[10px] text-content-secondary truncate">
                  {item.type} {item.tags.length > 0 ? `· ${item.tags.slice(0, 3).join(", ")}` : ""}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }
);
