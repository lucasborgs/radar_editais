"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";

const INPUT_CLS = cn(
  "w-full rounded-lg border border-border px-3 py-2 text-sm font-sans",
  "text-content-primary placeholder:text-content-secondary",
  "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
  "transition-colors bg-surface"
);

export default function LoginPage() {
  const { signInWithEmail } = useAuth();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    setLoading(true);
    setError(null);

    const { error } = await signInWithEmail(email.trim());
    setLoading(false);

    if (error) {
      setError(error);
    } else {
      setSent(true);
    }
  }

  return (
    <div className="min-h-screen bg-app-bg flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <p className="text-xs font-semibold uppercase tracking-widest text-primary font-sans mb-2">
            Radar de Editais
          </p>
          <h1 className="font-heading text-2xl font-bold text-content-primary">
            Entrar na plataforma
          </h1>
          <p className="text-sm text-content-secondary font-sans mt-1">
            Enviaremos um link de acesso para o seu e-mail.
          </p>
        </div>

        <div className="bg-surface rounded-2xl border border-border p-6 shadow-card">
          {sent ? (
            <div className="text-center space-y-3">
              <div className="text-4xl">📬</div>
              <p className="text-sm font-medium text-content-primary font-sans">
                Link enviado para <span className="text-primary">{email}</span>
              </p>
              <p className="text-xs text-content-secondary font-sans">
                Clique no link do e-mail para acessar. Pode fechar esta aba.
              </p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-content-primary font-sans mb-1.5">
                  E-mail
                </label>
                <input
                  type="email"
                  className={INPUT_CLS}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="voce@empresa.com.br"
                  autoFocus
                  disabled={loading}
                  required
                />
              </div>

              {error && (
                <div className="rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 px-3 py-2 text-xs text-red-700 dark:text-red-300 font-sans">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={loading || !email.trim()}
                className={cn(
                  "w-full px-4 py-2.5 rounded-xl text-sm font-semibold font-sans text-white",
                  "bg-primary hover:bg-primary-hover transition-colors",
                  "disabled:opacity-40 disabled:cursor-not-allowed"
                )}
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Enviando...
                  </span>
                ) : (
                  "Enviar link de acesso"
                )}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
