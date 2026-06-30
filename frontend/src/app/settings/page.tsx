"use client";

import { useEffect, useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { getMe, getMyPreferences, updateMyPreferences } from "@/lib/api";

function Toggle({
  checked,
  disabled,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description: string;
}) {
  return (
    <label
      className={cn(
        "flex items-start gap-4 p-4 rounded-xl border border-border bg-surface",
        disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer hover:border-primary/30 transition-colors"
      )}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-content-primary font-sans">{label}</p>
        <p className="text-xs text-content-secondary font-sans mt-1 leading-relaxed">
          {description}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors mt-0.5",
          checked ? "bg-primary" : "bg-gray-300 dark:bg-gray-600",
          "focus:outline-none focus:ring-2 focus:ring-primary/40",
          disabled && "cursor-not-allowed"
        )}
      >
        <span
          aria-hidden
          className={cn(
            "inline-block h-5 w-5 rounded-full bg-white shadow transition-transform mt-0.5",
            checked ? "translate-x-5" : "translate-x-0.5"
          )}
        />
      </button>
    </label>
  );
}

export default function SettingsPage() {
  const { getToken } = useAuth();
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [contribute, setContribute] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2500);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const t = await getToken();
      if (cancelled) return;
      setToken(t);
      if (!t) {
        setError("Faça login para gerenciar preferências.");
        setLoading(false);
        return;
      }
      try {
        // Prefer /me which is supposed to include the field
        const me = await getMe(t);
        if (cancelled) return;
        const fromMe =
          me.preferences?.contribute_to_global_weights ??
          me.contribute_to_global_weights;
        if (typeof fromMe === "boolean") {
          setContribute(fromMe);
        } else {
          // Fallback: dedicated endpoint
          try {
            const prefs = await getMyPreferences(t);
            if (!cancelled) setContribute(!!prefs.contribute_to_global_weights);
          } catch {
            /* default to false */
          }
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Erro ao carregar preferências");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  async function handleToggle(next: boolean) {
    if (!token) return;
    const prev = contribute;
    setContribute(next); // optimistic
    setSaving(true);
    try {
      await updateMyPreferences({ contribute_to_global_weights: next }, token);
      showToast(next ? "Contribuição ativada" : "Contribuição desativada");
    } catch (e: unknown) {
      setContribute(prev); // revert
      const msg = e instanceof Error ? e.message : "Erro ao salvar";
      if (msg.includes("404")) {
        setError("Endpoint /me/preferences ainda não está disponível.");
      } else {
        setError(msg);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <DashboardLayout title="Configurações">
      <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        <div>
          <h1 className="font-heading text-xl font-bold text-content-primary">
            Configurações
          </h1>
          <p className="text-sm text-content-secondary font-sans mt-0.5">
            Preferências da conta e privacidade.
          </p>
        </div>

        {/* Privacy section */}
        <section className="space-y-3">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-content-secondary font-sans">
            Privacidade e dados
          </h2>

          {loading ? (
            <div className="bg-surface rounded-xl border border-border p-4 h-24 animate-pulse" />
          ) : (
            <Toggle
              checked={contribute}
              disabled={saving || !token}
              onChange={handleToggle}
              label="Contribuir com outcomes anônimos para melhorar matching global"
              description={
                "Quando ativo, seus resultados de aplicação (apenas: scores, dimensões, status — sem identificação) são usados em agregado para evoluir os pesos do matching para todos os usuários. Pode ser desligado a qualquer momento."
              }
            />
          )}

          {error && (
            <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-900 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 font-sans">
              {error}
            </div>
          )}
        </section>
      </div>

      {toast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 bg-content-primary text-white px-4 py-2 rounded-full shadow-lg text-xs font-sans">
          {toast}
        </div>
      )}
    </DashboardLayout>
  );
}
