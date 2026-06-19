"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import { resolveWorkspaceSession } from "@/lib/workspace";
import { loadProfileFromStorage, EMPTY_PROFILE } from "@/types/profile";

/**
 * /chat — rota legada do fluxo de escrita, agora um RESOLVER (spec W7/§6).
 *
 * O workspace (`/workspace/{id}`) substituiu o chat linear. Esta página só:
 *  - sem ?edital (ou sem perfil): redireciona para o front-door "/";
 *  - com ?edital=X: resolve a sessão mais recente desse alvo (ou cria) e
 *    navega para /workspace/{id}. Erros → toast + volta pra "/".
 *
 * Deep-links existentes (sessions, editais/[id], PipelineCard) continuam
 * funcionando: chegam aqui e são reencaminhados para o workspace.
 */
function ChatResolverInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const editalId = searchParams.get("edital");
  const { getToken, loading: authLoading } = useAuth();
  const [message, setMessage] = useState("Abrindo workspace…");
  const started = useRef(false);

  useEffect(() => {
    if (authLoading || started.current) return;
    started.current = true;

    (async () => {
      const profile = loadProfileFromStorage();
      if (!editalId || !profile) {
        router.replace("/");
        return;
      }

      const token = await getToken();
      if (!token) {
        router.replace("/login");
        return;
      }

      try {
        const sessionId = await resolveWorkspaceSession(
          editalId,
          profile ?? EMPTY_PROFILE,
          token,
        );
        router.replace(`/workspace/${sessionId}`);
      } catch (e) {
        toast.error(
          e instanceof Error ? e.message : "Não foi possível abrir o workspace.",
        );
        setMessage("Falha ao abrir. Redirecionando…");
        router.replace("/");
      }
    })();
  }, [authLoading, editalId, getToken, router]);

  return (
    <div className="h-screen flex items-center justify-center bg-app-bg">
      <div className="flex items-center gap-3 text-sm text-content-secondary font-sans">
        <span className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        {message}
      </div>
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatResolverInner />
    </Suspense>
  );
}
