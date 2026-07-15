"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function AuthRedirectPage() {
  const router = useRouter();
  const DEMO_MODE = typeof process !== "undefined" && process.env.NEXT_PUBLIC_DEMO_MODE === "1";
  const { loading, user } = useAuth();

  useEffect(() => {
    if (DEMO_MODE) {
      router.replace("/");
      return;
    }
    if (loading) return;
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
