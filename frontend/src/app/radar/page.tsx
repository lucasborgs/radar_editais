"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { MatchedEditalCard } from "@/components/frontdoor/MatchedEditalCard";
import { MatchedEntityCard } from "@/components/frontdoor/MatchedEntityCard";
import { getRadarMatches, startWritingSession, type RadarMatchesResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { isCompleteForWriting, isRadarReady } from "@/types/frontdoor";
import { EMPTY_PROFILE, loadProfileFromStorage, type CompanyProfile } from "@/types/profile";

function Trail({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="flex flex-col gap-2"><div><h2 className="text-base font-semibold text-content-primary">{title}</h2><p className="text-sm text-content-secondary">{description}</p></div><div className="flex flex-col gap-2">{children}</div></section>;
}

function LoadingCards() {
  return <div className="flex flex-col gap-2" aria-label="Carregando radar">{[0, 1, 2].map((key) => <div key={key} className="h-32 animate-pulse rounded-xl border border-border bg-surface" />)}</div>;
}

export default function RadarPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [hydrated, setHydrated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<RadarMatchesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setProfile(loadProfileFromStorage() ?? EMPTY_PROFILE); setHydrated(true); }, []);
  const ready = isRadarReady(profile);
  const loadRadar = useCallback(async () => {
    if (!ready) return;
    setLoading(true); setError(null);
    try { setData(await getRadarMatches(profile)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Não foi possível atualizar o Radar."); }
    finally { setLoading(false); }
  }, [profile, ready]);
  useEffect(() => { if (hydrated && ready) void loadRadar(); }, [hydrated, ready, loadRadar]);
  const hasResults = useMemo(() => Boolean(data && (data.matched_editais.length || data.matched_programas.length || data.matched_investidores.length)), [data]);
  const startWriting = useCallback(async (id: string, mode?: "proposal" | "pitch") => {
    if (!isCompleteForWriting(profile).ok) { toast.message("Complete o perfil para iniciar uma proposta."); router.push("/perfil"); return; }
    if (!user) { toast.message("Entre para iniciar uma proposta ou pitch."); router.push("/login"); return; }
    try { const session = await startWritingSession(id, profile, mode); if (session.session_id) router.push(`/workspace/${session.session_id}`); }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : "Não consegui iniciar agora."); }
  }, [profile, router, user]);

  if (!hydrated || authLoading) return <main className="mx-auto min-h-screen max-w-4xl px-4 py-10"><LoadingCards /></main>;
  if (!ready) return <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-4 py-10"><div className="rounded-xl border border-border bg-surface p-6"><p className="text-sm font-semibold text-primary">Seu Radar</p><h1 className="mt-2 text-2xl font-semibold text-content-primary">Conte o que sua empresa faz.</h1><p className="mt-2 text-content-secondary">Com o nome e uma descrição das atividades, encontramos oportunidades por afinidade de escopo — não por promessa de aprovação.</p><Link href="/" className="mt-5 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:opacity-90">Explorar e completar perfil →</Link></div></main>;

  return <main className="mx-auto min-h-screen max-w-4xl px-4 py-8 sm:py-10"><header className="mb-8 flex flex-wrap items-end justify-between gap-3"><div><p className="text-sm font-semibold text-primary">Radar</p><h1 className="text-2xl font-semibold text-content-primary">Oportunidades para {profile.nome}</h1><p className="mt-1 max-w-2xl text-sm text-content-secondary">A ordem reflete afinidade entre trechos do seu perfil e do escopo de cada oportunidade. Confira as evidências e a elegibilidade antes de decidir.</p></div><button type="button" onClick={() => void loadRadar()} disabled={loading} className="rounded-lg border border-border px-3 py-2 text-sm font-medium text-content-primary hover:bg-app-bg disabled:opacity-50">{loading ? "Atualizando…" : "Atualizar"}</button></header>
    {loading && !data && <LoadingCards />}
    {error && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"><p>{error}</p><button type="button" onClick={() => void loadRadar()} className="mt-2 font-medium underline">Tentar novamente</button></div>}
    {data && !hasResults && !loading && <div className="rounded-xl border border-border bg-surface p-5 text-sm"><p className="font-medium text-content-primary">Ainda não encontramos oportunidades com afinidade suficiente.</p><p className="mt-1 text-content-secondary">Detalhe atividades, tecnologia, mercado ou projetos no seu perfil para ampliar o sinal.</p><Link href="/" className="mt-3 inline-block text-primary hover:underline">Refinar perfil no Explorer →</Link></div>}
    {data && hasResults && <div className="flex flex-col gap-9">{data.matched_editais.length > 0 && <Trail title="Editais" description="Chamadas com prazo e escopo compatíveis.">{data.matched_editais.map((edital) => <MatchedEditalCard key={edital.entity_id} edital={edital} onStartWriting={(source, id) => void startWriting(`${source}:${id}`)} />)}</Trail>}{data.matched_programas.length > 0 && <Trail title="Programas" description="Iniciativas recorrentes que podem apoiar a jornada.">{data.matched_programas.map((entity) => <MatchedEntityCard key={entity.entity_id} entity={entity} onStartWriting={(id, mode) => void startWriting(id, mode)} />)}</Trail>}{data.matched_investidores.length > 0 && <Trail title="Capital privado" description="Investidores cuja tese se aproxima do seu perfil.">{data.matched_investidores.map((entity) => <MatchedEntityCard key={entity.entity_id} entity={entity} onStartWriting={(id, mode) => void startWriting(id, mode)} />)}</Trail>}</div>}
  </main>;
}
