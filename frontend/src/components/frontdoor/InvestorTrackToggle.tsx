"use client";

// Switch "Busca também capital de risco?" (spec mechanism-scope-decisions, D2).
// O radar SEMPRE chama o match de investidor (always-on, decisão travada): este
// switch NÃO gateia resultados — só revela/coleta os campos Q3/Q4 do perfil
// (estagio, mrr_arr, round_alvo_brl, cap_table_resumo, tracao_resumo) para
// melhorar a qualidade do match de investidor.

import { cn } from "@/lib/utils";

export function InvestorTrackToggle({
  on,
  onChange,
}: {
  on: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      className="flex w-full items-start gap-3 text-left"
    >
      <span
        className={cn(
          "mt-0.5 relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors",
          on ? "bg-primary" : "bg-content-secondary/30",
        )}
        aria-hidden
      >
        <span
          className={cn(
            "inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform",
            on ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-content-primary font-sans">
          Busca também capital de risco?
        </span>
        <span className="block text-xs text-content-secondary font-sans">
          Investidores já aparecem no seu radar. Ligue para preencher os dados de
          rodada e tração e melhorar a qualidade desses matches.
        </span>
      </span>
    </button>
  );
}
