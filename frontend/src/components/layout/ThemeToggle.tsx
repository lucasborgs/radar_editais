"use client";

// Botão de tema claro/escuro para o rodapé da sidebar. Visual e comportamento
// alinhados às utilitárias do ConversationSidebar (sol/lua, stroke 1.75).

import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const SUN =
  "M12 3v1m0 16v1m9-9h-1M4 12H3m15.36 6.36l-.7-.7M6.34 6.34l-.7-.7m12.72 0l-.7.7M6.34 17.66l-.7.7M16 12a4 4 0 11-8 0 4 4 0 018 0z";
const MOON = "M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z";

export function ThemeToggle() {
  const { resolved, toggle } = useTheme();
  const isDark = resolved === "dark";

  return (
    <button
      type="button"
      onClick={toggle}
      title={isDark ? "Tema claro" : "Tema escuro"}
      aria-label={isDark ? "Mudar para tema claro" : "Mudar para tema escuro"}
      className={cn(
        "flex w-full items-center gap-2.5 px-2 py-1.5 rounded-lg text-sm font-sans transition-colors",
        "text-content-secondary hover:bg-content-secondary/10 hover:text-content-primary",
      )}
    >
      <svg
        className="w-4 h-4"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.75}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d={isDark ? SUN : MOON}
        />
      </svg>
      {isDark ? "Tema claro" : "Tema escuro"}
    </button>
  );
}
