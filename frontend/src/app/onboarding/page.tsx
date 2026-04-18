"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  EMPTY_PROFILE,
  saveProfileToStorage,
  type CompanyProfile,
  type TipoEntidade,
  type FaturamentoFaixa,
  type TipoFinanciamento,
  type UsoFinanciamento,
} from "@/types/profile";
import { PORTE_LABELS } from "@/lib/constants";

// ── Shared UI primitives ─────────────────────────────────────────────────────

const INPUT_CLS = cn(
  "w-full rounded-lg border border-border px-3 py-2 text-sm font-sans",
  "text-content-primary placeholder:text-content-secondary",
  "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
  "transition-colors bg-white"
);

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="block text-sm font-medium text-content-primary font-sans">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {hint && <p className="text-xs text-content-secondary font-sans">{hint}</p>}
      {children}
    </div>
  );
}

function CheckGroup<T extends string>({
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
    <div className="flex flex-wrap gap-2">
      {options.map((opt) => {
        const active = value.includes(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => toggle(opt.value)}
            className={cn(
              "px-3 py-1.5 rounded-full text-sm font-sans border transition-colors",
              active
                ? "bg-primary text-white border-primary"
                : "bg-white text-content-secondary border-border hover:border-primary/50 hover:text-content-primary"
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function TagInput({
  value,
  onChange,
  placeholder,
  suggestions,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
}) {
  const [draft, setDraft] = useState("");

  function add(tag: string) {
    const t = tag.trim();
    if (t && !value.includes(t)) onChange([...value, t]);
    setDraft("");
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          className={INPUT_CLS}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(draft);
            }
          }}
          placeholder={placeholder ?? "Digite e pressione Enter"}
        />
        <button
          type="button"
          onClick={() => add(draft)}
          className="shrink-0 px-3 py-2 rounded-lg border border-border text-xs font-sans text-content-secondary hover:bg-gray-50 transition-colors"
        >
          Adicionar
        </button>
      </div>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {value.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 rounded-full bg-primary/10 text-primary px-2.5 py-0.5 text-xs font-sans"
            >
              {tag}
              <button
                type="button"
                onClick={() => onChange(value.filter((v) => v !== tag))}
                className="hover:text-red-500 transition-colors leading-none"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      {suggestions && (
        <div className="flex flex-wrap gap-1">
          {suggestions
            .filter((s) => !value.includes(s))
            .slice(0, 6)
            .map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => add(s)}
                className="text-xs font-sans text-content-secondary border border-border/60 rounded-full px-2 py-0.5 hover:border-primary/40 hover:text-primary transition-colors"
              >
                + {s}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

// ── Step definitions ─────────────────────────────────────────────────────────

const TIPO_ENTIDADE_OPTIONS: { value: TipoEntidade; label: string }[] = [
  { value: "empresa", label: "Empresa" },
  { value: "startup", label: "Startup" },
  { value: "universidade", label: "Universidade / ICT" },
  { value: "ICT", label: "Instituto de Pesquisa" },
];

const FATURAMENTO_OPTIONS: { value: FaturamentoFaixa; label: string }[] = [
  { value: "<500K", label: "Até R$ 500 mil" },
  { value: "500K-5M", label: "R$ 500 mil – R$ 5 mi" },
  { value: "5M-50M", label: "R$ 5 mi – R$ 50 mi" },
  { value: ">50M", label: "Acima de R$ 50 mi" },
];

const FINANCIAMENTO_OPTIONS: { value: TipoFinanciamento; label: string }[] = [
  { value: "subvencao_nao_reembolsavel", label: "Subvenção (não reembolsável)" },
  { value: "credito_reembolsavel", label: "Crédito reembolsável" },
  { value: "matching_embrapii", label: "Matching EMBRAPII" },
  { value: "pesquisa_colaborativa", label: "Pesquisa colaborativa" },
];

const USO_FINANCIAMENTO_OPTIONS: { value: UsoFinanciamento; label: string }[] = [
  { value: "P&D_interno", label: "P&D interno" },
  { value: "contratacao", label: "Contratação" },
  { value: "equipamento", label: "Equipamento" },
  { value: "prototipagem", label: "Prototipagem" },
  { value: "internacionalizacao", label: "Internacionalização" },
  { value: "marketing", label: "Marketing" },
];

const CERTIFICACOES_SUGERIDAS = [
  "ISO 9001", "ISO 14001", "ISO/IEC 27001", "CMMI", "Empresa B",
];

const TRL_LABELS: Record<number, string> = {
  1: "TRL 1 — Princípios básicos observados",
  2: "TRL 2 — Conceito tecnológico formulado",
  3: "TRL 3 — Prova de conceito experimental",
  4: "TRL 4 — Validado em laboratório",
  5: "TRL 5 — Validado em ambiente relevante",
  6: "TRL 6 — Demonstrado em ambiente relevante",
  7: "TRL 7 — Protótipo em ambiente operacional",
  8: "TRL 8 — Sistema completo e qualificado",
  9: "TRL 9 — Sistema em operação",
};

// ── Step components ──────────────────────────────────────────────────────────

function Step1({
  profile,
  set,
}: {
  profile: CompanyProfile;
  set: <K extends keyof CompanyProfile>(k: K, v: CompanyProfile[K]) => void;
}) {
  return (
    <div className="space-y-5">
      <Field label="Nome da empresa" required>
        <input
          className={INPUT_CLS}
          value={profile.nome}
          onChange={(e) => set("nome", e.target.value)}
          placeholder="Ex: TechSol Inovações"
          autoFocus
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
      <Field label="Tipo de entidade" required>
        <div className="flex flex-wrap gap-2 mt-1">
          {TIPO_ENTIDADE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => set("tipo_entidade", opt.value)}
              className={cn(
                "px-3 py-1.5 rounded-full text-sm font-sans border transition-colors",
                profile.tipo_entidade === opt.value
                  ? "bg-primary text-white border-primary"
                  : "bg-white text-content-secondary border-border hover:border-primary/50"
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </Field>
      <div className="grid grid-cols-2 gap-4">
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
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </Field>
        <Field label="Faturamento anual">
          <select
            className={INPUT_CLS}
            value={profile.faturamento_anual_faixa}
            onChange={(e) =>
              set("faturamento_anual_faixa", e.target.value as FaturamentoFaixa)
            }
          >
            <option value="">Selecionar...</option>
            {FATURAMENTO_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Localização">
          <input
            className={INPUT_CLS}
            value={profile.localizacao}
            onChange={(e) => set("localizacao", e.target.value)}
            placeholder="Ex: São Paulo/SP"
          />
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
      </div>
    </div>
  );
}

function Step2({
  profile,
  set,
}: {
  profile: CompanyProfile;
  set: <K extends keyof CompanyProfile>(k: K, v: CompanyProfile[K]) => void;
}) {
  return (
    <div className="space-y-5">
      <Field
        label="Proposta de valor"
        hint="Uma frase que resume o que vocês fazem e para quem."
        required
      >
        <input
          className={INPUT_CLS}
          value={profile.one_liner}
          onChange={(e) => set("one_liner", e.target.value)}
          placeholder="Ex: Desenvolvemos sensores IoT para otimizar o consumo de energia em indústrias"
          autoFocus
        />
      </Field>
      <Field
        label="Problema que a empresa resolve"
        hint="Qual dor ou ineficiência do mercado vocês atacam?"
      >
        <textarea
          rows={2}
          className={cn(INPUT_CLS, "resize-none")}
          value={profile.problem_statement}
          onChange={(e) => set("problem_statement", e.target.value)}
          placeholder="Ex: Indústrias gastam até 30% a mais em energia por falta de monitoramento em tempo real..."
        />
      </Field>
      <Field
        label="Solução / tecnologia"
        hint="Como vocês resolvem esse problema? Qual abordagem ou tecnologia usam?"
      >
        <textarea
          rows={2}
          className={cn(INPUT_CLS, "resize-none")}
          value={profile.solution_summary}
          onChange={(e) => set("solution_summary", e.target.value)}
          placeholder="Ex: Plataforma SaaS com sensores LPWAN e ML para previsão de consumo e alertas proativos..."
        />
      </Field>
      <Field
        label="Descrição completa das atividades"
        hint="Conte mais sobre o negócio, mercado de atuação e diferenciais."
        required
      >
        <textarea
          rows={3}
          className={cn(INPUT_CLS, "resize-none")}
          value={profile.descricao_atividades}
          onChange={(e) => set("descricao_atividades", e.target.value)}
          placeholder="Descreva o que a empresa faz, tecnologias utilizadas, mercado de atuação..."
        />
      </Field>
      <Field label="TRL atual do projeto principal" hint="Technology Readiness Level (maturidade tecnológica)">
        <select
          className={INPUT_CLS}
          value={profile.trl ?? ""}
          onChange={(e) => set("trl", e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">Não sei / Não se aplica</option>
          {Object.entries(TRL_LABELS).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
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
    </div>
  );
}

function Step3({
  profile,
  set,
}: {
  profile: CompanyProfile;
  set: <K extends keyof CompanyProfile>(k: K, v: CompanyProfile[K]) => void;
}) {
  return (
    <div className="space-y-5">
      <Field label="Tipos de financiamento de interesse" hint="Selecione todos que se aplicam." required>
        <CheckGroup<TipoFinanciamento>
          options={FINANCIAMENTO_OPTIONS}
          value={profile.tipos_financiamento_interesse}
          onChange={(v) => set("tipos_financiamento_interesse", v)}
        />
      </Field>
      <Field label="Para que usaria o recurso?" hint="Selecione todos que se aplicam.">
        <CheckGroup<UsoFinanciamento>
          options={USO_FINANCIAMENTO_OPTIONS}
          value={profile.uso_financiamento}
          onChange={(v) => set("uso_financiamento", v)}
        />
      </Field>
      <Field label="Valor buscado (R$)" hint="Quanto precisaria para o próximo projeto?">
        <input
          type="number"
          className={INPUT_CLS}
          value={profile.valor_buscado ?? ""}
          onChange={(e) =>
            set("valor_buscado", e.target.value ? Number(e.target.value) : null)
          }
          placeholder="Ex: 1000000"
        />
      </Field>
      <Field label="Portfólio de projetos">
        <textarea
          rows={3}
          className={cn(INPUT_CLS, "resize-none")}
          value={profile.portfolio_projetos}
          onChange={(e) => set("portfolio_projetos", e.target.value)}
          placeholder="Cite projetos de P&D, inovação ou financiados anteriormente..."
        />
      </Field>
      <Field label="Certificações">
        <TagInput
          value={profile.certificacoes}
          onChange={(v) => set("certificacoes", v)}
          placeholder="Ex: ISO 9001"
          suggestions={CERTIFICACOES_SUGERIDAS}
        />
      </Field>
    </div>
  );
}

// ── Wizard ───────────────────────────────────────────────────────────────────

const STEPS = [
  { title: "Quem é você", subtitle: "Identificação da empresa" },
  { title: "O que você faz", subtitle: "Tecnologia e atividades" },
  { title: "O que você busca", subtitle: "Intenção de financiamento" },
];

function canAdvance(profile: CompanyProfile, step: number): boolean {
  if (step === 0) return !!profile.nome && !!profile.tipo_entidade;
  if (step === 1) return !!profile.one_liner && !!profile.descricao_atividades;
  if (step === 2) return true;
  return false;
}

function OnboardingInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") ?? "/matching";

  const [step, setStep] = useState(0);
  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);

  function set<K extends keyof CompanyProfile>(key: K, value: CompanyProfile[K]) {
    setProfile((prev) => ({ ...prev, [key]: value }));
  }

  function handleFinish() {
    saveProfileToStorage(profile);
    router.push(next);
  }

  const isLast = step === STEPS.length - 1;
  const canNext = canAdvance(profile, step);

  return (
    <div className="min-h-screen bg-app-bg flex items-center justify-center p-4">
      <div className="w-full max-w-xl">
        {/* Header */}
        <div className="text-center mb-8">
          <p className="text-xs font-semibold uppercase tracking-widest text-primary font-sans mb-2">
            Radar de Editais
          </p>
          <h1 className="font-heading text-2xl font-bold text-content-primary">
            {STEPS[step].title}
          </h1>
          <p className="text-sm text-content-secondary font-sans mt-1">
            {STEPS[step].subtitle}
          </p>
        </div>

        {/* Step indicator */}
        <div className="flex items-center gap-2 mb-6">
          {STEPS.map((s, i) => (
            <div key={i} className="flex items-center gap-2 flex-1">
              <div
                className={cn(
                  "w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold font-data transition-colors shrink-0",
                  i < step
                    ? "bg-primary text-white"
                    : i === step
                    ? "bg-primary/15 text-primary border-2 border-primary"
                    : "bg-gray-100 text-content-secondary"
                )}
              >
                {i < step ? "✓" : i + 1}
              </div>
              <span
                className={cn(
                  "text-xs font-sans hidden sm:block truncate",
                  i === step ? "text-content-primary font-medium" : "text-content-secondary"
                )}
              >
                {s.title}
              </span>
              {i < STEPS.length - 1 && (
                <div
                  className={cn(
                    "flex-1 h-px",
                    i < step ? "bg-primary" : "bg-border"
                  )}
                />
              )}
            </div>
          ))}
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl border border-border p-6 shadow-card">
          {step === 0 && <Step1 profile={profile} set={set} />}
          {step === 1 && <Step2 profile={profile} set={set} />}
          {step === 2 && <Step3 profile={profile} set={set} />}

          {/* Navigation */}
          <div className="flex items-center justify-between mt-6 pt-4 border-t border-border">
            <button
              type="button"
              onClick={() => setStep((s) => s - 1)}
              className={cn(
                "px-4 py-2 text-sm font-sans text-content-secondary hover:text-content-primary transition-colors",
                step === 0 && "invisible"
              )}
            >
              ← Voltar
            </button>
            <button
              type="button"
              onClick={isLast ? handleFinish : () => setStep((s) => s + 1)}
              disabled={!canNext}
              className={cn(
                "px-6 py-2.5 rounded-xl text-sm font-semibold font-sans text-white transition-colors",
                "bg-primary hover:bg-primary-hover",
                "disabled:opacity-40 disabled:cursor-not-allowed",
                "focus:outline-none focus:ring-2 focus:ring-primary/50"
              )}
            >
              {isLast ? "Começar a explorar →" : "Próximo →"}
            </button>
          </div>
        </div>

        {/* Skip link */}
        <p className="text-center mt-4">
          <button
            type="button"
            onClick={() => router.push("/dashboard")}
            className="text-xs text-content-secondary hover:text-content-primary font-sans transition-colors"
          >
            Pular por agora
          </button>
        </p>
      </div>
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense fallback={null}>
      <OnboardingInner />
    </Suspense>
  );
}
