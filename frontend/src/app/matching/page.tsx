"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { StatusBadge } from "@/components/ui";
import { getMatches } from "@/lib/api";
import { PORTE_LABELS, scoreColor } from "@/lib/constants";
import type { TipoFinanciamento } from "@/types/profile";

const FINANCIAMENTO_OPTIONS: { value: TipoFinanciamento; label: string }[] = [
  { value: "subvencao_nao_reembolsavel", label: "Subvenção (não reembolsável)" },
  { value: "credito_reembolsavel", label: "Crédito reembolsável" },
  { value: "matching_embrapii", label: "Matching EMBRAPII" },
  { value: "pesquisa_colaborativa", label: "Pesquisa colaborativa" },
];

function CheckPills<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T[];
  onChange: (v: T[]) => void;
}) {
  function toggle(v: T) {
    onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => {
        const active = value.includes(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => toggle(opt.value)}
            className={cn(
              "px-2.5 py-1 rounded-full text-xs font-sans border transition-colors",
              active
                ? "bg-primary text-white border-primary"
                : "bg-white text-content-secondary border-border hover:border-primary/50"
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
import { cn } from "@/lib/utils";
import type { CompanyProfile } from "@/types/profile";
import type { KGMatchResult } from "@/types/edital";
import { EMPTY_PROFILE, PROFILE_STORAGE_KEY, saveProfileToStorage } from "@/types/profile";


// ── Sub-components ───────────────────────────────────────────────────────────

function FormSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-6">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-content-secondary font-sans mb-3 pb-1.5 border-b border-border">
        {title}
      </h3>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-content-primary font-sans mb-1">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
    </div>
  );
}

const INPUT_CLS = cn(
  "w-full rounded-lg border border-border px-3 py-2 text-sm font-sans",
  "text-content-primary placeholder:text-content-secondary",
  "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
  "transition-colors bg-white"
);

const DIM_LABELS: Record<string, string> = {
  elegibilidade: "Elegibilidade",
  tematico:      "Temático",
  trl:           "TRL",
  mecanismo:     "Mecanismo",
  contrapartida: "Contrapartida",
};

function ScoreDimBar({ label, score, max }: { label: string; score: number; max: number }) {
  const pct = max > 0 ? Math.round((score / max) * 100) : 0;
  const color = pct >= 80 ? "#1DB954" : pct >= 50 ? "#f59e0b" : "#ef4444";
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] font-sans text-content-secondary w-24 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-[10px] font-data text-content-secondary w-10 text-right shrink-0">
        {score}/{max}
      </span>
    </div>
  );
}

function MatchCard({ match }: { match: KGMatchResult }) {
  const router = useRouter();
  const color = scoreColor(match.score);
  const [expanded, setExpanded] = useState(false);
  const dims = match.match_dimensions ?? {};
  const hasDims = Object.keys(dims).length > 0;

  return (
    <div className="rounded-xl border border-border bg-white p-4 transition-colors hover:bg-gray-50">
      <div className="flex items-start justify-between gap-3 mb-2">
        <p
          onClick={() => router.push(`/editais/${match.id}`)}
          className="text-sm font-semibold text-content-primary font-sans leading-snug flex-1 cursor-pointer hover:text-primary transition-colors"
        >
          {match.title}
        </p>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className="text-lg font-bold font-data leading-none" style={{ color }}>
            {match.score.toFixed(1)}
          </span>
          <span className="text-[10px] font-sans text-content-secondary">/10</span>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap mb-2">
        <StatusBadge status={match.status} />
        {match.deadline && (
          <span className="font-data text-xs text-content-secondary">
            Prazo: {match.deadline}
          </span>
        )}
      </div>

      <p className="text-xs text-content-secondary font-sans mb-3 line-clamp-2">
        {match.justificativa}
      </p>

      {/* Score breakdown */}
      {hasDims && (
        <div className="mb-3">
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-[10px] font-sans text-primary hover:underline mb-1"
          >
            {expanded ? "▲ Ocultar detalhes" : "▼ Ver score por dimensão"}
          </button>
          {expanded && (
            <div className="space-y-1.5 pt-1">
              {Object.entries(dims).map(([key, dim]) => (
                <ScoreDimBar
                  key={key}
                  label={DIM_LABELS[key] ?? key}
                  score={dim.score}
                  max={dim.max}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {match.key_requirements && match.key_requirements.length > 0 && (
        <ul className="space-y-0.5 mb-3">
          {match.key_requirements.slice(0, 2).map((r, i) => (
            <li key={i} className="text-xs text-content-secondary font-sans flex items-start gap-1">
              <span className="text-primary mt-0.5">·</span>
              {r}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2 pt-2 border-t border-border">
        <button
          onClick={() => router.push(`/editais/${match.id}`)}
          className="text-xs font-sans text-content-secondary hover:text-content-primary transition-colors"
        >
          Ver detalhes →
        </button>
        <button
          onClick={() => router.push(`/chat?edital=${match.id}`)}
          className={cn(
            "ml-auto px-3 py-1.5 rounded-lg text-xs font-semibold font-sans text-white",
            "bg-primary hover:bg-primary-hover transition-colors"
          )}
        >
          Escrever proposta
        </button>
      </div>
    </div>
  );
}

function ProfileCompletionBar({ profile }: { profile: CompanyProfile }) {
  const checks = [
    !!profile.nome,
    !!profile.tipo_entidade,
    !!profile.one_liner,
    !!profile.descricao_atividades,
    !!profile.solution_summary,
    !!profile.tamanho_empresa,
    profile.trl !== null,
    profile.tipos_financiamento_interesse.length > 0,
    !!profile.portfolio_projetos,
    profile.capital_social !== null,
    !!profile.equipe_resumo,
  ];
  const pct = Math.round((checks.filter(Boolean).length / checks.length) * 100);

  return (
    <div className="mb-4">
      <div className="flex justify-between items-center mb-1">
        <span className="text-xs font-sans text-content-secondary">Perfil preenchido</span>
        <span className="text-xs font-data font-semibold text-primary">{pct}%</span>
      </div>
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-primary rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function MatchingPage() {
  const [profile, setProfile] = useState<CompanyProfile>(() => {
    if (typeof window === "undefined") return EMPTY_PROFILE;
    try {
      const saved = localStorage.getItem(PROFILE_STORAGE_KEY);
      return saved ? { ...EMPTY_PROFILE, ...JSON.parse(saved) } : EMPTY_PROFILE;
    } catch { return EMPTY_PROFILE; }
  });

  const [results, setResults] = useState<KGMatchResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [hasMounted, setHasMounted] = useState(false);

  useEffect(() => { setHasMounted(true); }, []);

  // Persist profile to localStorage somente após mount (evita sobrescrever no primeiro render)
  useEffect(() => {
    if (!hasMounted) return;
    saveProfileToStorage(profile);
    setSaved(true);
    const t = setTimeout(() => setSaved(false), 1500);
    return () => clearTimeout(t);
  }, [profile, hasMounted]);

  function set<K extends keyof CompanyProfile>(key: K, value: CompanyProfile[K]) {
    setProfile((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSearch() {
    if (!profile.nome || !profile.descricao_atividades) {
      setError("Preencha ao menos o nome da empresa e a descrição das atividades.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await getMatches(profile);
      setResults(res.matches);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erro ao conectar ao servidor.");
    } finally {
      setLoading(false);
    }
  }

  const topMatches = results?.filter((r) => r.score >= 5) ?? [];
  const lowMatches = results?.filter((r) => r.score < 5) ?? [];

  return (
    <DashboardLayout title="Matching">
      <div className="flex gap-6 items-start">
        {/* ── Left: Profile Form ─────────────────────────────────────────── */}
        <div className="w-[380px] shrink-0">
          <div className="bg-white rounded-xl border border-border p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-heading text-base font-bold text-content-primary">
                Perfil da Empresa
              </h2>
              {saved && (
                <span className="text-[10px] font-sans text-green-600 animate-pulse">
                  Salvo
                </span>
              )}
            </div>

            <ProfileCompletionBar profile={profile} />

            <FormSection title="Identificação">
              <Field label="Nome da empresa" required>
                <input
                  className={INPUT_CLS}
                  value={profile.nome}
                  onChange={(e) => set("nome", e.target.value)}
                  placeholder="Ex: TechSol Inovações"
                />
              </Field>
              <Field label="CNPJ">
                <input
                  className={INPUT_CLS}
                  value={profile.cnpj}
                  onChange={(e) => set("cnpj", e.target.value)}
                  placeholder="00.000.000/0001-00"
                />
              </Field>
              <Field label="Site da empresa">
                <input
                  className={INPUT_CLS}
                  value={profile.url_site}
                  onChange={(e) => set("url_site", e.target.value)}
                  placeholder="https://suaempresa.com.br"
                />
              </Field>
              <Field label="Porte">
                <select
                  className={INPUT_CLS}
                  value={profile.tamanho_empresa}
                  onChange={(e) =>
                    set("tamanho_empresa", e.target.value as CompanyProfile["tamanho_empresa"])
                  }
                >
                  <option value="">Selecionar...</option>
                  {Object.entries(PORTE_LABELS).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Capital social (R$)">
                <input
                  type="number"
                  className={INPUT_CLS}
                  value={profile.capital_social ?? ""}
                  onChange={(e) =>
                    set("capital_social", e.target.value ? Number(e.target.value) : null)
                  }
                  placeholder="Ex: 250000"
                />
              </Field>
            </FormSection>

            <FormSection title="Atividades e Portfólio">
              <Field label="Proposta de valor">
                <input
                  className={INPUT_CLS}
                  value={profile.one_liner}
                  onChange={(e) => set("one_liner", e.target.value)}
                  placeholder="Ex: Sensores IoT para eficiência energética industrial"
                />
              </Field>
              <Field label="Descrição das atividades" required>
                <textarea
                  rows={3}
                  className={cn(INPUT_CLS, "resize-none")}
                  value={profile.descricao_atividades}
                  onChange={(e) => set("descricao_atividades", e.target.value)}
                  placeholder="Descreva o que a empresa faz, tecnologias utilizadas, mercado de atuação..."
                />
              </Field>
              <Field label="Solução / tecnologia">
                <textarea
                  rows={2}
                  className={cn(INPUT_CLS, "resize-none")}
                  value={profile.solution_summary}
                  onChange={(e) => set("solution_summary", e.target.value)}
                  placeholder="Como vocês resolvem o problema? Qual tecnologia ou abordagem?"
                />
              </Field>
              <Field label="TRL atual">
                <select
                  className={INPUT_CLS}
                  value={profile.trl ?? ""}
                  onChange={(e) => set("trl", e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">Não informado</option>
                  {[1,2,3,4,5,6,7,8,9].map((n) => (
                    <option key={n} value={n}>TRL {n}</option>
                  ))}
                </select>
              </Field>
              <Field label="Portfólio de projetos">
                <textarea
                  rows={2}
                  className={cn(INPUT_CLS, "resize-none")}
                  value={profile.portfolio_projetos}
                  onChange={(e) => set("portfolio_projetos", e.target.value)}
                  placeholder="Cite projetos relevantes já executados..."
                />
              </Field>
              <Field label="Equipe técnica">
                <textarea
                  rows={2}
                  className={cn(INPUT_CLS, "resize-none")}
                  value={profile.equipe_resumo}
                  onChange={(e) => set("equipe_resumo", e.target.value)}
                  placeholder="Ex: 15 colaboradores, 3 engenheiros sênior, 1 PM PMP..."
                />
              </Field>
            </FormSection>

            <FormSection title="Intenção de Financiamento">
              <Field label="Tipos de interesse">
                <CheckPills<TipoFinanciamento>
                  options={FINANCIAMENTO_OPTIONS}
                  value={profile.tipos_financiamento_interesse}
                  onChange={(v) => set("tipos_financiamento_interesse", v)}
                />
              </Field>
            </FormSection>

            {/* Search button */}
            {error && (
              <div className="mb-3 rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
                {error}
              </div>
            )}
            <button
              onClick={handleSearch}
              disabled={loading}
              className={cn(
                "w-full py-2.5 rounded-xl text-sm font-semibold font-sans text-white",
                "bg-primary hover:bg-primary-hover transition-colors",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                "focus:outline-none focus:ring-2 focus:ring-primary/50"
              )}
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Buscando...
                </span>
              ) : (
                "Buscar Editais Compatíveis"
              )}
            </button>
          </div>
        </div>

        {/* ── Right: Results ─────────────────────────────────────────────── */}
        <div className="flex-1 min-w-0">
          {!results && !loading && (
            <div className="bg-white rounded-xl border border-border p-10 text-center">
              <div className="w-12 h-12 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto mb-4">
                <svg
                  className="w-6 h-6 text-primary"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={1.75}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z"
                  />
                </svg>
              </div>
              <p className="font-heading text-base font-bold text-content-primary mb-1">
                Configure o perfil e busque
              </p>
              <p className="text-sm text-content-secondary font-sans max-w-sm mx-auto">
                Preencha as informações da empresa à esquerda e clique em{" "}
                <strong>Buscar Editais Compatíveis</strong> para ver o ranking de aderência.
              </p>
            </div>
          )}

          {loading && (
            <div className="bg-white rounded-xl border border-border p-10 text-center">
              <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin mx-auto mb-4" />
              <p className="text-sm text-content-secondary font-sans">
                Calculando aderência com {results === null ? "os editais" : "novos parâmetros"}...
              </p>
            </div>
          )}

          {results && !loading && (
            <div>
              {/* Summary bar */}
              <div className="flex items-center gap-4 mb-4 flex-wrap">
                <p className="text-sm font-sans text-content-secondary">
                  <span className="font-data font-bold text-content-primary text-base">
                    {results.length}
                  </span>{" "}
                  editais analisados
                </p>
                {topMatches.length > 0 && (
                  <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium font-sans bg-[#1DB954]/15 text-[#169c46]">
                    {topMatches.length} relevantes
                  </span>
                )}
              </div>

              {/* Result cards */}
              <div className="space-y-3">
                {topMatches.map((r) => (
                  <MatchCard key={r.id} match={r} />
                ))}
                {lowMatches.length > 0 && (
                  <details className="group">
                    <summary className="text-xs text-content-secondary font-sans cursor-pointer py-2 px-1 hover:text-content-primary transition-colors select-none">
                      + {lowMatches.length} com baixa relevância (ocultos)
                    </summary>
                    <div className="mt-2 space-y-3">
                      {lowMatches.map((r) => (
                        <MatchCard key={r.id} match={r} />
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </DashboardLayout>
  );
}
