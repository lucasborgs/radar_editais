"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

// KG v2 / PR8: a ficha foi unificada em /oportunidades/[id] (D1 — a unidade é
// sempre Oportunidade). Esta rota vira um redirect p/ preservar bookmarks e o
// CTA `chat?edital=` que ainda aponta editais.
export default function EditalRedirectPage() {
  const params = useParams();
  const router = useRouter();
  const id = (params?.id as string) || "";

  useEffect(() => {
    router.replace(id ? `/oportunidades/${encodeURIComponent(id)}` : "/oportunidades");
  }, [id, router]);

  return (
    <div className="flex items-center gap-3 p-8 text-sm text-content-secondary font-sans">
      <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      Redirecionando...
    </div>
  );
}
