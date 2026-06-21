"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { extractProfileFromUrl } from "@/lib/api";
import type { CompanyProfile } from "@/types/profile";

// Hero de URL da Etapa 1 do onboarding progressivo (spec onboarding-input-ux,
// Decisão 2). Porta de entrada de menor fricção: cole o site → extract preenche
// os campos estruturais → o caller monta um diff de revisão e roda o radar.
// ZERO pergunta nesta etapa. "Prefiro descrever" cai no chat (comportamento de
// hoje). Este componente é dono só do input + estados de loading/erro; o caller
// decide o que fazer com o perfil extraído (montar diff, persistir, rodar radar).

// Garante esquema http(s):// — o backend /profile/extract exige (422 sem ele).
function normalizeUrl(raw: string): string {
  const u = raw.trim();
  if (!u) return u;
  return /^https?:\/\//i.test(u) ? u : `https://${u}`;
}

export function UrlHero({
  onResult,
  onSkip,
}: {
  // Perfil extraído (campos não-vazios) + se a extração veio fraca.
  onResult: (profile: CompanyProfile, lowConfidence: boolean) => void;
  onSkip: () => void;
}) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    const normalized = normalizeUrl(url);
    if (!normalized) return;
    setLoading(true);
    setError(null);
    try {
      const res = await extractProfileFromUrl(normalized);
      if (res.error) {
        setError("Não consegui ler esse site. Tente outra URL ou descreva sua empresa.");
        return;
      }
      onResult(res.profile, res.low_confidence);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Não consegui acessar o site. Tente novamente.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-xl rounded-2xl border border-primary/30 bg-surface px-5 py-6 shadow-card">
      <h2 className="text-lg font-semibold text-content-primary">
        Cole o site da sua empresa
      </h2>
      <p className="mt-1 text-sm text-content-secondary">
        Eu leio o site, monto um rascunho do perfil e já te mostro o que existe de
        fomento. Você revisa antes de aplicar.
      </p>

      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <input
          type="url"
          inputMode="url"
          autoFocus
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !loading) void handleSubmit();
          }}
          disabled={loading}
          placeholder="suaempresa.com.br"
          className={cn(
            "flex-1 rounded-lg border border-border bg-app-bg px-3 py-2 text-sm font-sans",
            "text-content-primary placeholder:text-content-secondary/60",
            "focus:outline-none focus:ring-2 focus:ring-primary/40",
            "disabled:cursor-not-allowed disabled:opacity-60",
          )}
        />
        <button
          type="button"
          onClick={() => void handleSubmit()}
          disabled={loading || !url.trim()}
          className={cn(
            "rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white transition-opacity",
            "hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          {loading ? "Analisando…" : "Analisar"}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-amber-600">{error}</p>}

      <button
        type="button"
        onClick={onSkip}
        disabled={loading}
        className="mt-3 text-xs font-medium text-content-secondary underline-offset-2 hover:underline disabled:opacity-50"
      >
        Não tenho site / prefiro descrever
      </button>
    </div>
  );
}
