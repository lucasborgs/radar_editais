import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes safely, resolving conflicts */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Tenta parsear texto de prazo (dd/mm/aaaa, dd.mm.aaaa, aaaa-mm-dd, pt-BR escrito)
 *  e retorna data formatada dd/mm/aaaa, ou null se não-parseável.
 *  Espelha a normalização de prazo exposta pelo catálogo gold do backend. */
export function parseDeadline(raw: string): string | null {
  if (!raw) return null;
  const s = raw.trim();
  // Tenta formatos com dia/mês/ano
  const patterns: [RegExp, (m: RegExpMatchArray) => Date | null][] = [
    [/^(\d{1,2})[./](\d{1,2})[./](\d{4})$/, (m) => {
      const d = parseInt(m[1]), mo = parseInt(m[2]) - 1, y = parseInt(m[3]);
      return (d >= 1 && d <= 31 && mo >= 0 && mo <= 11) ? new Date(y, mo, d) : null;
    }],
    [/^(\d{4})-(\d{2})-(\d{2})$/, (m) => {
      const y = parseInt(m[1]), mo = parseInt(m[2]) - 1, d = parseInt(m[3]);
      return (d >= 1 && d <= 31 && mo >= 0 && mo <= 11) ? new Date(y, mo, d) : null;
    }],
    [/^(\d{1,2})[./](\d{1,2})[./](\d{2})$/, (m) => {
      const d = parseInt(m[1]), mo = parseInt(m[2]) - 1, y = 2000 + parseInt(m[3]);
      return (d >= 1 && d <= 31 && mo >= 0 && mo <= 11) ? new Date(y, mo, d) : null;
    }],
    // "16 de maio de 2024" ou "16 de Maio de 2024"
    [/^(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})$/i, (m) => {
      const meses: Record<string, number> = {
        janeiro:0, fevereiro:1, março:2, marco:2, abril:3, maio:4, junho:5,
        julho:6, agosto:7, setembro:8, outubro:9, novembro:10, dezembro:11,
      };
      const mo = meses[m[2].toLowerCase()];
      if (mo === undefined) return null;
      const d = parseInt(m[1]), y = parseInt(m[3]);
      return (d >= 1 && d <= 31) ? new Date(y, mo, d) : null;
    }],
  ];
  for (const [re, fn] of patterns) {
    const m = s.match(re);
    if (m) {
      const dt = fn(m);
      if (dt && !isNaN(dt.getTime())) {
        return dt.toLocaleDateString("pt-BR"); // dd/mm/aaaa
      }
    }
  }
  return null;
}

/** Truncate text to maxLength chars, appending ellipsis */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trimEnd() + "…";
}
