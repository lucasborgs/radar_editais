"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useIsAdmin } from "@/lib/hooks";
import { cn } from "@/lib/utils";

/**
 * Header fino do front-door: nome do produto + ação de conta + menu "⋮".
 *
 * - Anônimo: "Entrar" (→ /login) e o menu só tem "Começar de novo".
 * - Logado: o menu "⋮" reúne navegação de suporte e operação. As jornadas de
 *   produto permanecem visíveis fora dele. "Começar de novo" limpa o transcript
 *   local em ambos os casos.
 */
const AUTHED_LINKS: { href: string; label: string; adminOnly?: boolean }[] = [
  { href: "/library", label: "Biblioteca" },
  { href: "/pipeline", label: "Pipeline" },
  { href: "/oportunidades", label: "Ecossistema" },
  // Fila da Descoberta é ferramenta do operador (ADMIN_EMAILS), não do cliente.
  { href: "/discovered", label: "Descobertas", adminOnly: true },
];

export function FrontDoorHeader({
  isAuthed,
  onReset,
  onSignOut,
}: {
  isAuthed: boolean;
  onReset: () => void;
  onSignOut?: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const isAdmin = useIsAdmin();
  const links = AUTHED_LINKS.filter((l) => !l.adminOnly || isAdmin);

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

  const itemCls =
    "block w-full px-3 py-2 text-left text-sm font-sans text-content-primary hover:bg-app-bg";

  return (
    <header className="shrink-0 border-b border-border bg-surface">
      <div className="mx-auto flex max-w-2xl items-center justify-between px-4 py-3">
        <Link href="/" aria-label="Radar de Editais" className="text-sm font-semibold text-content-primary font-sans">
          <span className="hidden sm:inline">Radar de Editais</span>
          <span className="sm:hidden">RE</span>
        </Link>

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
              type="button"
              aria-label="Mais ações"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
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
              <div
                role="menu"
                className="absolute right-0 top-full z-10 mt-1 w-44 rounded-lg border border-border bg-surface py-1 shadow-lg"
              >
                <Link href="/" role="menuitem" onClick={() => setMenuOpen(false)} className={itemCls}>
                  Explorar
                </Link>
                <Link href="/radar" role="menuitem" onClick={() => setMenuOpen(false)} className={itemCls}>
                  Radar
                </Link>
                <Link href="/projects" role="menuitem" onClick={() => setMenuOpen(false)} className={itemCls}>
                  Projetos
                </Link>
                <div className="my-1 border-t border-border" />
                {isAuthed &&
                  links.map((l) => (
                    <Link
                      key={l.href}
                      href={l.href}
                      role="menuitem"
                      onClick={() => setMenuOpen(false)}
                      className={itemCls}
                    >
                      {l.label}
                    </Link>
                  ))}
                {isAuthed && <div className="my-1 border-t border-border" />}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onReset();
                  }}
                  className={itemCls}
                >
                  Começar de novo
                </button>
                {isAuthed && onSignOut && (
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false);
                      onSignOut();
                    }}
                    className={itemCls}
                  >
                    Sair
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
