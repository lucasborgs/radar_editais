"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";

/**
 * Header fino do front-door: nome do produto + ação de conta + menu "⋮".
 *
 * - `isAuthed` decide entre "Entrar" (→ /login) e "Minha conta" (→ /dashboard),
 *   seguindo o padrão de sessão já usado no app (useAuth().session).
 * - O menu "⋮" expõe "Começar de novo" (limpa o transcript local). A barra de
 *   status de perfil é M2 — não entra aqui ainda.
 */
export function FrontDoorHeader({
  isAuthed,
  onReset,
}: {
  isAuthed: boolean;
  onReset: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Fecha o menu ao clicar fora.
  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  return (
    <header className="shrink-0 border-b border-border bg-white">
      <div className="mx-auto flex max-w-2xl items-center justify-between px-4 py-3">
        <span className="text-sm font-semibold text-content-primary font-sans">
          Radar de Editais
        </span>

        <div className="flex items-center gap-1">
          <Link
            href={isAuthed ? "/dashboard" : "/login"}
            className="rounded-lg px-3 py-1.5 text-sm font-sans text-content-primary hover:bg-app-bg transition-colors"
          >
            {isAuthed ? "Minha conta" : "Entrar"}
          </Link>

          <div className="relative" ref={menuRef}>
            <button
              type="button"
              aria-label="Mais ações"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
              className={cn(
                "rounded-lg px-2 py-1.5 text-content-secondary hover:bg-app-bg transition-colors",
                menuOpen && "bg-app-bg"
              )}
            >
              {/* glifo de "três pontos verticais" */}
              <span aria-hidden className="text-lg leading-none">⋮</span>
            </button>

            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full z-10 mt-1 w-44 rounded-lg border border-border bg-white py-1 shadow-lg"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onReset();
                  }}
                  className="block w-full px-3 py-2 text-left text-sm font-sans text-content-primary hover:bg-app-bg"
                >
                  Começar de novo
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
