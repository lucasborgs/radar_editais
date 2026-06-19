"use client";

import Link from "next/link";
import type { GateAction } from "@/types/frontdoor";

// Card de gate (D3/F4/F5): ação que exige conta. Explica o porquê e leva ao
// login preservando perfil + conversa (ambos vivem no localStorage, então
// "continuam aqui" é literal — nada se perde no ida-e-volta).
const REASON: Record<GateAction, string> = {
  anexo:
    "Para anexar arquivos você precisa de uma conta — seu perfil e conversa continuam aqui.",
  brief:
    "Para gerar o brief GO/NO-GO você precisa de uma conta — seu perfil e conversa continuam aqui.",
  proposta:
    "Para começar uma proposta você precisa de uma conta — seu perfil e conversa continuam aqui.",
};

export function GateCard({ action }: { action: GateAction }) {
  return (
    <div className="rounded-xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 text-sm font-sans">
      <p className="text-content-primary">{REASON[action]}</p>
      <Link
        href="/login"
        className="mt-2 inline-block rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
      >
        Entrar ou criar conta
      </Link>
    </div>
  );
}
