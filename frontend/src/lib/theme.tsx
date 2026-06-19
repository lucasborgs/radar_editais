"use client";

// Tema claro/escuro sem dependência externa (next-themes não é necessário).
// O estado de preferência é "light" | "dark" | "system"; o que de fato é
// aplicado ao <html> (classe `dark`) é o tema *resolvido*. A escolha persiste
// em localStorage e, no modo "system", segue o prefers-color-scheme em tempo
// real. O flash inicial é evitado pelo THEME_INIT_SCRIPT injetado no <head>
// (ver layout.tsx), que roda antes da hidratação.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type Theme = "light" | "dark" | "system";
type Resolved = "light" | "dark";

const STORAGE_KEY = "theme";

interface ThemeState {
  /** Preferência escolhida pelo usuário. */
  theme: Theme;
  /** Tema efetivamente aplicado (resolve "system"). */
  resolved: Resolved;
  setTheme: (t: Theme) => void;
  /** Alterna entre claro e escuro (ignora "system"). */
  toggle: () => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

function systemPrefersDark(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function resolve(theme: Theme): Resolved {
  if (theme === "system") return systemPrefersDark() ? "dark" : "light";
  return theme;
}

function apply(resolved: Resolved) {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
}

// Script inline para o <head>: aplica a classe `dark` antes do React montar,
// evitando o "flash" de tema claro no carregamento. Mantém a mesma lógica de
// resolve() acima (duplicada de propósito — precisa rodar sem o bundle JS).
export const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("${STORAGE_KEY}")||"system";var d=t==="dark"||(t==="system"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("system");
  const [resolved, setResolved] = useState<Resolved>("light");

  // Lê a preferência salva uma vez no mount (o script inline já pintou a tela).
  useEffect(() => {
    const stored = (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "system";
    setThemeState(stored);
    setResolved(resolve(stored));
  }, []);

  // No modo "system", reage a mudanças do SO em tempo real.
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const r = mq.matches ? "dark" : "light";
      setResolved(r);
      apply(r);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    const r = resolve(t);
    setResolved(r);
    apply(r);
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {
      /* modo privado/quota — segue sem persistir */
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(resolved === "dark" ? "light" : "dark");
  }, [resolved, setTheme]);

  return (
    <ThemeContext.Provider value={{ theme, resolved, setTheme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme deve ser usado dentro de <ThemeProvider>");
  return ctx;
}
