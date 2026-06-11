"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function AuthRedirectPage() {
  const router = useRouter();
  const { loading, user } = useAuth();

  useEffect(() => {
    if (loading) return;
    // Pós-login sempre cai na porta única "/" (D7): lá o front-door hidrata o
    // perfil da conta e faz o merge com a conversa anônima (F5/F6). Sem usuário,
    // volta ao login.
    router.replace(user ? "/" : "/login");
  }, [loading, user, router]);

  return (
    <div className="min-h-screen bg-app-bg flex items-center justify-center">
      <div className="flex items-center gap-3 text-content-secondary font-sans text-sm">
        <span className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        Carregando...
      </div>
    </div>
  );
}
