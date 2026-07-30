"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { MatchedEditalCard } from "@/components/frontdoor/MatchedEditalCard";
import { MatchedEntityCard } from "@/components/frontdoor/MatchedEntityCard";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { getRadarMatches, startWritingSession, type RadarMatchesResponse } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  availableSetores,
  DEFAULT_RADAR_FILTERS,
  filterEditais,
  filterEntities,
  sortEditais,
  urgencyLabel,
  type RadarFilters,
} from "@/lib/radar-utils";
import { temporalDeadlineText } from "@/lib/opportunity-temporal";
import { isCompleteForWriting, isRadarReady } from "@/types/frontdoor";
import { EMPTY_PROFILE, loadProfileFromStorage, type CompanyProfile } from "@/types/profile";

function Trail({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="flex flex-col gap-2"><div><h2 className="text-base font-semibold text-content-primary">{title}</h2><p className="text-sm text-content-secondary">{description}</p></div><div className="flex flex-col gap-2">{children}</div></section>;
}

function LoadingCards() {
  return <div className="flex flex-col gap-2" aria-label="Carregando radar">{[0, 1, 2].map((key) => <div key={key} className="h-32 animate-pulse rounded-xl border border-border bg-surface" />)}</div>;
}

function FilterButton({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={`rounded-full border px-2.5 py-1 text-xs font-medium ${active ? "border-primary bg-primary text-white" : "border-border text-content-secondary hover:bg-app-bg"}`}>{children}</button>;
}

function ComparisonPanel({ editais, onRemove, onStartWriting }: { editais: RadarMatchesResponse["matched_editais"]; onRemove: (id: string) => void; onStartWriting: (edital: RadarMatchesResponse["matched_editais"][number]) => void }) {
  if (editais.length === 0) return null;
  const rows = [
    {
      label: "Prazo",
      render: (e: RadarMatchesResponse["matched_editais"][number]) => {
        const prazo = temporalDeadlineText(e);
        if (!prazo) return urgencyLabel(e);
        const urgency = urgencyLabel(e);
        return prazo === urgency ? prazo : `${prazo} · ${urgency}`;
      },
    },
    { label: "Valor", render: (e: RadarMatchesResponse["matched_editais"][number]) => e.valor || "Não informado" },
    { label: "Setores", render: (e: RadarMatchesResponse["matched_editais"][number]) => e.setores.join(", ") || "Não informado" },
    { label: "Afinidade de escopo", render: (e: RadarMatchesResponse["matched_editais"][number]) => `${Math.round(e.affinity * 100)} · evidência, não chance de aprovação` },
    { label: "Elegibilidade", render: (e: RadarMatchesResponse["matched_editais"][number]) => e.elegibilidade?.status === "elegivel" ? "Confirmada pelos dados disponíveis" : e.elegibilidade?.unknown?.join("; ") || "Não informado" },
    { label: "Por que apareceu", render: (e: RadarMatchesResponse["matched_editais"][number]) => e.matched_excerpts[0]?.edital_text || "Não informado" },
  ];
  return <section className="mt-6 rounded-xl border border-primary/30 bg-surface p-4" aria-label="Comparação de editais"><div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-base font-semibold text-content-primary">Comparar editais</h2><p className="text-xs text-content-secondary">Compare fatos e evidências; a decisão continua sendo sua.</p></div></div><div className="mt-4 overflow-x-auto"><table className="w-full min-w-[680px] text-left text-xs"><thead><tr><th className="w-40 p-2 text-content-secondary">Critério</th>{editais.map((e) => <th key={e.entity_id} className="min-w-52 p-2 align-top text-content-primary"><p>{e.name}</p><div className="mt-2 flex gap-2"><button type="button" onClick={() => onRemove(e.entity_id)} className="text-content-secondary underline">Remover</button><button type="button" onClick={() => onStartWriting(e)} className="text-primary underline">Escrever proposta</button></div></th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row.label} className="border-t border-border"><th className="p-2 align-top font-medium text-content-secondary">{row.label}</th>{editais.map((e) => <td key={e.entity_id} className="p-2 align-top leading-relaxed text-content-primary">{row.render(e)}</td>)}</tr>)}</tbody></table></div></section>;
}

export default function RadarPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [hydrated, setHydrated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<RadarMatchesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<RadarFilters>(DEFAULT_RADAR_FILTERS);
  const [comparedIds, setComparedIds] = useState<string[]>([]);

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
  const setores = useMemo(() => data ? availableSetores(data.matched_editais, data.matched_programas, data.matched_investidores) : [], [data]);
  const visibleEditais = useMemo(() => data ? sortEditais(filterEditais(data.matched_editais, filters), filters.order) : [], [data, filters]);
  const visibleProgramas = useMemo(() => data ? filterEntities(data.matched_programas, filters.setores) : [], [data, filters.setores]);
  const visibleInvestidores = useMemo(() => data ? filterEntities(data.matched_investidores, filters.setores) : [], [data, filters.setores]);
  const comparedEditais = useMemo(() => data ? data.matched_editais.filter((e) => comparedIds.includes(e.entity_id)) : [], [data, comparedIds]);
  const toggleCompare = useCallback((id: string) => {
    setComparedIds((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (current.length >= 3) { toast.message("Compare até três editais por vez."); return current; }
      return [...current, id];
    });
  }, []);
  const startWriting = useCallback(async (id: string, mode?: "proposal" | "pitch") => {
    if (!isCompleteForWriting(profile).ok) { toast.message("Complete o perfil para iniciar uma proposta."); router.push("/perfil"); return; }
    if (!user) { toast.message("Entre para iniciar uma proposta ou pitch."); router.push("/login"); return; }
    try { const session = await startWritingSession(id, profile, mode); if (session.session_id) router.push(`/workspace/${session.session_id}`); }
    catch (cause) { toast.error(cause instanceof Error ? cause.message : "Não consegui iniciar agora."); }
  }, [profile, router, user]);

  let content;
  if (!hydrated || authLoading) {
    content = <LoadingCards />;
  } else if (!ready) {
    content = (
      <div className="mx-auto max-w-2xl">
        <div className="mt-24 rounded-xl border border-border bg-surface p-6">
          <p className="text-sm font-semibold text-primary">Seu Radar</p>
          <h1 className="mt-2 text-2xl font-semibold text-content-primary">Conte o que sua empresa faz.</h1>
          <p className="mt-2 text-content-secondary">Com o nome e uma descrição das atividades, encontramos oportunidades por afinidade de escopo — não por promessa de aprovação.</p>
          <Link href="/" className="mt-5 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-white hover:opacity-90">Explorar e completar perfil →</Link>
        </div>
      </div>
    );
  } else {
    content = (
      <div className="mx-auto max-w-4xl">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-primary">Radar</p>
            <h1 className="text-2xl font-semibold text-content-primary">Oportunidades para {profile.nome}</h1>
            <p className="mt-1 max-w-2xl text-sm text-content-secondary">A ordem reflete afinidade entre trechos do seu perfil e do escopo de cada oportunidade. Confira as evidências e a elegibilidade antes de decidir.</p>
          </div>
          <button type="button" onClick={() => void loadRadar()} disabled={loading} className="rounded-lg border border-border px-3 py-2 text-sm font-medium text-content-primary hover:bg-app-bg disabled:opacity-50">{loading ? "Atualizando…" : "Atualizar"}</button>
        </header>
        {loading && !data && <LoadingCards />}
        {error && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"><p>{error}</p><button type="button" onClick={() => void loadRadar()} className="mt-2 font-medium underline">Tentar novamente</button></div>}
        {data && !hasResults && !loading && <div className="rounded-xl border border-border bg-surface p-5 text-sm"><p className="font-medium text-content-primary">Ainda não encontramos oportunidades com afinidade suficiente.</p><p className="mt-1 text-content-secondary">Detalhe atividades, tecnologia, mercado ou projetos no seu perfil para ampliar o sinal.</p><Link href="/" className="mt-3 inline-block text-primary hover:underline">Refinar perfil em Explorar →</Link></div>}
        {data && hasResults && <><section className="mb-7 rounded-xl border border-border bg-surface p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold text-content-primary">Filtrar resultados</h2><p className="text-xs text-content-secondary">{visibleEditais.length} editais, {visibleProgramas.length} programas e {visibleInvestidores.length} investidores exibidos.</p></div><button type="button" onClick={() => setFilters(DEFAULT_RADAR_FILTERS)} className="text-xs font-medium text-primary hover:underline">Limpar filtros</button></div><div className="mt-3 flex flex-col gap-3"><div className="flex flex-wrap gap-2"><span className="self-center text-xs text-content-secondary">Trilhas</span>{([{ id: "edital", label: "Editais" }, { id: "programa", label: "Programas" }, { id: "investidor", label: "Capital privado" }] as const).map((trail) => <FilterButton key={trail.id} active={filters.trails.includes(trail.id)} onClick={() => setFilters((current) => ({ ...current, trails: current.trails.includes(trail.id) ? current.trails.filter((item) => item !== trail.id) : [...current.trails, trail.id] }))}>{trail.label}</FilterButton>)}</div><div className="flex flex-wrap gap-2"><span className="self-center text-xs text-content-secondary">Setores</span>{setores.map((setor) => <FilterButton key={setor} active={filters.setores.includes(setor)} onClick={() => setFilters((current) => ({ ...current, setores: current.setores.includes(setor) ? current.setores.filter((item) => item !== setor) : [...current.setores, setor] }))}>{setor}</FilterButton>)}</div><div className="flex flex-wrap gap-2"><span className="self-center text-xs text-content-secondary">Elegibilidade</span>{(["all", "elegivel", "nao_verificada"] as const).map((value) => <FilterButton key={value} active={filters.eligibility === value} onClick={() => setFilters((current) => ({ ...current, eligibility: value }))}>{value === "all" ? "Todos" : value === "elegivel" ? "Elegível" : "Não verificada"}</FilterButton>)}</div><div className="flex flex-wrap gap-2"><span className="self-center text-xs text-content-secondary">Prazo</span>{(["all", "closing", "soon", "continuous"] as const).map((value) => <FilterButton key={value} active={filters.deadline === value} onClick={() => setFilters((current) => ({ ...current, deadline: value }))}>{value === "all" ? "Todos" : value === "closing" ? "Até 7 dias" : value === "soon" ? "Até 30 dias" : "Fluxo contínuo"}</FilterButton>)}</div><div className="flex flex-wrap gap-2"><span className="self-center text-xs text-content-secondary">Ordenar editais</span>{(["affinity", "deadline"] as const).map((value) => <FilterButton key={value} active={filters.order === value} onClick={() => setFilters((current) => ({ ...current, order: value }))}>{value === "affinity" ? "Afinidade" : "Prazo mais próximo"}</FilterButton>)}</div></div></section>
          <div className="flex flex-col gap-9">
            {filters.trails.includes("edital") && visibleEditais.length > 0 && <Trail title="Editais" description="Chamadas com prazo e escopo compatíveis.">{visibleEditais.slice(0, 20).map((edital) => <MatchedEditalCard key={edital.entity_id} edital={edital} onCompare={() => toggleCompare(edital.entity_id)} isCompared={comparedIds.includes(edital.entity_id)} onStartWriting={(source, id) => void startWriting(`${source}:${id}`)} />)}</Trail>}
            {filters.trails.includes("programa") && visibleProgramas.length > 0 && <Trail title="Programas" description="Linhas de fomento contínuas ou recorrentes">{visibleProgramas.slice(0, 10).map((programa) => <MatchedEntityCard key={programa.entity_id} entity={programa} onStartWriting={(id, mode) => void startWriting(id, mode)} />)}</Trail>}
            {filters.trails.includes("investidor") && visibleInvestidores.length > 0 && <Trail title="Capital privado" description="Investidores com tese alinhada ao seu perfil">{visibleInvestidores.slice(0, 10).map((investidor) => <MatchedEntityCard key={investidor.entity_id} entity={investidor} onStartWriting={(id, mode) => void startWriting(id, mode)} />)}</Trail>}
            {(!filters.trails.includes("edital") || visibleEditais.length === 0) && (!filters.trails.includes("programa") || visibleProgramas.length === 0) && (!filters.trails.includes("investidor") || visibleInvestidores.length === 0) && <div className="rounded-xl border border-border bg-surface p-5 text-sm"><p className="font-medium text-content-primary">Nenhum resultado corresponde aos filtros atuais.</p><button type="button" onClick={() => setFilters(DEFAULT_RADAR_FILTERS)} className="mt-2 text-primary hover:underline">Limpar filtros</button></div>}
          </div>
          <ComparisonPanel editais={comparedEditais} onRemove={toggleCompare} onStartWriting={(edital) => void startWriting(`${edital.source}:${edital.edital_id}`)} />
        </>}
      </div>
    );
  }

  return <DashboardLayout title="Radar">{content}</DashboardLayout>;
}
