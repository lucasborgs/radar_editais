import type { Config } from "tailwindcss";

// Cores semânticas são dirigidas por CSS vars (canais RGB) definidas em
// globals.css — `:root` (claro) e `.dark` (escuro). A forma `rgb(var(--x) /
// <alpha-value>)` preserva os modificadores de opacidade do Tailwind
// (bg-primary/10, border-primary/30, etc.). Trocar de tema = trocar a classe
// `dark` no <html>; nenhuma classe de cor precisa mudar.
const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: "rgb(var(--color-primary) / <alpha-value>)",
          hover: "rgb(var(--color-primary-hover) / <alpha-value>)",
        },
        app: {
          bg: "rgb(var(--color-app-bg) / <alpha-value>)",
          surface: "rgb(var(--color-surface) / <alpha-value>)",
        },
        surface: "rgb(var(--color-surface) / <alpha-value>)",
        content: {
          primary: "rgb(var(--color-content-primary) / <alpha-value>)",
          secondary: "rgb(var(--color-content-secondary) / <alpha-value>)",
        },
        border: {
          DEFAULT: "rgb(var(--color-border) / <alpha-value>)",
        },
      },
      fontFamily: {
        heading: ["var(--font-cabinet)", "sans-serif"],
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-fragment-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: {
        "2xl": "1rem",
        xl: "0.75rem",
        lg: "0.5rem",
        full: "9999px",
      },
      boxShadow: {
        sm: "0 1px 2px 0 rgb(0 0 0 / 0.05)",
        card: "0 1px 3px 0 rgb(0 0 0 / 0.08), 0 1px 2px -1px rgb(0 0 0 / 0.06)",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};

export default config;
