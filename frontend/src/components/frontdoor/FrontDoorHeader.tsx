"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useIsAdmin } from "@/lib/hooks";
import {
  PRIMARY_NAV_DESTINATIONS,
  UTILITY_NAV_DESTINATIONS,
  visibleNavigationDestinations,
} from "@/lib/navigation";
import { cn } from "@/lib/utils";

/**
 * Header fino do front-door: nome do produto + ação de conta + menu "⋮".
 *
 * - Anônimo: "Entrar" (→ /login) e as jornadas ficam no menu "⋮".
 * - Logado: o menu "⋮" reúne navegação de suporte e operação. As jornadas de
 *   produto permanecem visíveis fora dele.
 * - A ação opcional "Começar de novo" limpa o transcript local no front door;
 *   shells secundários reutilizam a mesma navegação sem essa ação.
 */
export function FrontDoorHeader({
  isAuthed,
  onReset,
  onSignOut,
  label,
}: {
  isAuthed: boolean;
  onReset?: () => void;
  onSignOut?: () => void;
  label?: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const isAdmin = useIsAdmin();
  const utilityLinks = visibleNavigationDestinations(
    UTILITY_NAV_DESTINATIONS,
    isAdmin,
  );

  // Fecha o menu ao clicar fora.
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  const itemCls =
    "block w-full px-3 py-2 text-left text-sm font-sans text-content-primary hover:bg-app-bg";

  return (
    <header className="shrink-0 border-b border-border bg-surface">
      <div className="mx-auto flex max-w-2xl items-center justify-between px-4 py-3">
        {label ? (
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <Link
              href="/"
              aria-label="Radar de Editais"
              className="shrink-0 text-sm font-semibold text-content-primary font-sans"
            >
              RE
            </Link>
            <h1 className="truncate text-sm font-semibold text-content-primary font-sans">
              {label}
            </h1>
          </div>
        ) : (
          <Link
            href="/"
            aria-label="Radar de Editais"
            className="min-w-0 flex-1 truncate text-sm font-semibold text-content-primary font-sans"
          >
            <span className="hidden sm:inline">Radar de Editais</span>
            <span className="sm:hidden">RE</span>
          </Link>
        )}

        <div className="flex items-center gap-1">
          {!isAuthed && (
            <Link
              href="/login"
              className="rounded-lg px-3 py-1.5 text-sm font-sans text-content-primary hover:bg-app-bg transition-colors"
            >
              Entrar
            </Link>
          )}

          <div className="relative" ref={menuRef}>
            <button
              ref={menuButtonRef}
              type="button"
              aria-label={menuOpen ? "Fechar navegação" : "Abrir navegação"}
              aria-expanded={menuOpen}
              aria-controls={menuId}
              onClick={() => setMenuOpen((v) => !v)}
              className={cn(
                "rounded-lg px-2 py-1.5 text-content-secondary hover:bg-app-bg transition-colors",
                menuOpen && "bg-app-bg",
              )}
            >
              {/* glifo de "três pontos verticais" */}
              <span aria-hidden className="text-lg leading-none">
                ⋮
              </span>
            </button>

            {menuOpen && (
              <nav
                id={menuId}
                aria-label="Navegação"
                className="absolute right-0 top-full z-10 mt-1 w-44 rounded-lg border border-border bg-surface py-1 shadow-lg"
              >
                {PRIMARY_NAV_DESTINATIONS.map((link) => (
                  <Link
                    key={link.href}
                    href={link.href}
                    onClick={() => setMenuOpen(false)}
                    className={itemCls}
                  >
                    {link.label}
                  </Link>
                ))}
                <div className="my-1 border-t border-border" />
                {isAuthed &&
                  utilityLinks.map((link) => (
                    <Link
                      key={link.href}
                      href={link.href}
                      onClick={() => setMenuOpen(false)}
                      className={itemCls}
                    >
                      {link.label}
                    </Link>
                  ))}
                {isAuthed && <div className="my-1 border-t border-border" />}
                {onReset && (
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      onReset();
                    }}
                    className={itemCls}
                  >
                    Começar de novo
                  </button>
                )}
                {isAuthed && onSignOut && (
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      onSignOut();
                    }}
                    className={itemCls}
                  >
                    Sair
                  </button>
                )}
              </nav>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
