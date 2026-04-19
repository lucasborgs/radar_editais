"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { getMe } from "@/lib/api";

export default function AuthRedirectPage() {
  const router = useRouter();
  const { loading, user, getToken } = useAuth();

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }

    getToken().then(async (token) => {
      if (!token) {
        router.replace("/login");
        return;
      }
      try {
        const me = await getMe(token);
        const profile = me.profile as Record<string, unknown>;
        const hasProfile = profile && profile.nome && profile.descricao_atividades;
        router.replace(hasProfile ? "/dashboard" : "/onboarding");
      } catch {
        router.replace("/onboarding");
      }
    });
  }, [loading, user]);

  return (
    <div className="min-h-screen bg-app-bg flex items-center justify-center">
      <div className="flex items-center gap-3 text-content-secondary font-sans text-sm">
        <span className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        Carregando...
      </div>
    </div>
  );
}
