"use client";

import { Suspense, useState, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  EMPTY_PROFILE,
  saveProfileToStorage,
  type CompanyProfile,
  type TipoEntidade,
  type TipoFinanciamento,
} from "@/types/profile";
import { PORTE_LABELS } from "@/lib/constants";
import { extractProfileFromUrl, extractProfileFromDocument, saveProfile } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Tabs } from "@/components/ui";
import type { FieldConfidence } from "@/types/api";

const MAX_PDF_BYTES = 10 * 1024 * 1024; // backend caps at 10MB

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
  confidence,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  confidence?: FieldConfidence;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="block text-sm font-medium text-content-primary font-sans">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
        {confidence === "high" && (
          <span className="ml-1.5 text-[10px] font-sans text-green-600 font-normal">✓ extraído</span>
        )}
        {confidence === "missing" && required && (
          <span className="ml-1.5 text-[10px] font-sans text-amber-600 font-normal">⚠ preencha</span>
        )}
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

// ── Field option sets ────────────────────────────────────────────────────────

const TIPO_ENTIDADE_OPTIONS: { value: TipoEntidade; label: string }[] = [
  { value: "empresa", label: "Empresa" },
  { value: "startup", label: "Startup" },
  { value: "universidade", label: "Universidade / ICT" },
  { value: "ICT", label: "Instituto de Pesquisa" },
];

const FINANCIAMENTO_OPTIONS: { value: TipoFinanciamento; label: string }[] = [
  { value: "subvencao_nao_reembolsavel", label: "Subvenção (não reembolsável)" },
  { value: "credito_reembolsavel", label: "Crédito reembolsável" },
  { value: "matching_embrapii", label: "Matching EMBRAPII" },
  { value: "pesquisa_colaborativa", label: "Pesquisa colaborativa" },
];

const UF_OPTIONS = [
  "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG",
  "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
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

// ── Profile form (single screen) ─────────────────────────────────────────────

function isComplete(p: CompanyProfile): boolean {
  return (
    !!p.nome &&
    !!p.tipo_entidade &&
    !!p.tamanho_empresa &&
    !!p.one_liner &&
    !!p.solution_summary &&
    !!p.descricao_atividades &&
    p.trl !== null &&
    p.tipos_financiamento_interesse.length > 0
  );
}

function ProfileForm({
  profile,
  set,
  confidence,
}: {
  profile: CompanyProfile;
  set: <K extends keyof CompanyProfile>(k: K, v: CompanyProfile[K]) => void;
  confidence: Record<string, FieldConfidence>;
}) {
  return (
    <div className="space-y-5">
      <Field label="Nome da empresa" required confidence={confidence.nome}>
        <input
          className={INPUT_CLS}
          value={profile.nome}
          onChange={(e) => set("nome", e.target.value)}
          placeholder="Ex: TechSol Inovações"
          autoFocus
        />
      </Field>

      <Field label="Tipo de entidade" required confidence={confidence.tipo_entidade}>
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

      <Field label="Porte" required confidence={confidence.tamanho_empresa}>
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

      <Field
        label="UF (sede)"
        hint="Estado da empresa — usado na elegibilidade geográfica de editais."
        confidence={confidence.uf}
      >
        <select
          className={INPUT_CLS}
          value={profile.uf}
          onChange={(e) => set("uf", e.target.value)}
        >
          <option value="">Selecionar...</option>
          {UF_OPTIONS.map((uf) => (
            <option key={uf} value={uf}>{uf}</option>
          ))}
        </select>
      </Field>

      <Field
        label="Ano de fundação"
        hint="Idade da empresa — alguns editais exigem tempo mínimo de constituição."
        confidence={confidence.ano_fundacao}
      >
        <input
          type="number"
          className={INPUT_CLS}
          value={profile.ano_fundacao ?? ""}
          onChange={(e) => set("ano_fundacao", e.target.value ? Number(e.target.value) : null)}
          placeholder="Ex: 2019"
        />
      </Field>

      <Field
        label="Faturamento anual (R$)"
        hint="Receita bruta anual — usada em tetos/pisos de faturamento de editais."
        confidence={confidence.faturamento_anual}
      >
        <input
          type="number"
          className={INPUT_CLS}
          value={profile.faturamento_anual ?? ""}
          onChange={(e) => set("faturamento_anual", e.target.value ? Number(e.target.value) : null)}
          placeholder="Ex: 2000000"
        />
      </Field>

      <Field
        label="Proposta de valor"
        hint="Uma frase que resume o que vocês fazem e para quem."
        required
        confidence={confidence.one_liner}
      >
        <input
          className={INPUT_CLS}
          value={profile.one_liner}
          onChange={(e) => set("one_liner", e.target.value)}
          placeholder="Ex: Desenvolvemos sensores IoT para otimizar o consumo de energia em indústrias"
        />
      </Field>

      <Field
        label="Solução / tecnologia"
        hint="Como vocês resolvem o problema? Qual abordagem ou tecnologia usam?"
        required
        confidence={confidence.solution_summary}
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
        label="Descrição das atividades"
        hint="O que a empresa faz, mercado de atuação e diferenciais."
        required
        confidence={confidence.descricao_atividades}
      >
        <textarea
          rows={3}
          className={cn(INPUT_CLS, "resize-none")}
          value={profile.descricao_atividades}
          onChange={(e) => set("descricao_atividades", e.target.value)}
          placeholder="Descreva o que a empresa faz, tecnologias utilizadas, mercado de atuação..."
        />
      </Field>

      <Field
        label="TRL atual do projeto principal"
        required
        hint="Technology Readiness Level (maturidade tecnológica) — impacta diretamente o matching"
      >
        <select
          className={INPUT_CLS}
          value={profile.trl ?? ""}
          onChange={(e) => set("trl", e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">Selecionar...</option>
          {Object.entries(TRL_LABELS).map(([v, l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
      </Field>

      <Field
        label="Tipos de financiamento de interesse"
        hint="Define quais mecanismos de editais são compatíveis."
        required
      >
        <CheckGroup<TipoFinanciamento>
          options={FINANCIAMENTO_OPTIONS}
          value={profile.tipos_financiamento_interesse}
          onChange={(v) => set("tipos_financiamento_interesse", v)}
        />
      </Field>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

type OnboardingMode = "url-input" | "form";

function OnboardingInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") ?? "/matching";
  const { getToken } = useAuth();

  const [mode, setMode] = useState<OnboardingMode>("url-input");
  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [confidence, setConfidence] = useState<Record<string, FieldConfidence>>({});
  const [saving, setSaving] = useState(false);

  // URL extraction state
  const [urlInput, setUrlInput] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const urlRef = useRef<HTMLInputElement>(null);

  // Document (PDF) extraction state
  const [docFile, setDocFile] = useState<File | null>(null);
  const [docExtracting, setDocExtracting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  function set<K extends keyof CompanyProfile>(key: K, value: CompanyProfile[K]) {
    setProfile((prev) => ({ ...prev, [key]: value }));
  }

  async function handleFinish() {
    setSaving(true);
    saveProfileToStorage(profile);
    try {
      const token = await getToken();
      if (token) await saveProfile(profile, token);
    } catch {
      // localStorage fallback já salvo — continua sem bloquear
    } finally {
      setSaving(false);
    }
    router.push(next);
  }

  async function handleExtract() {
    const url = urlInput.trim();
    if (!url) return;
    if (!url.startsWith("http://") && !url.startsWith("https://")) {
      setExtractError("Digite uma URL completa começando com https://");
      return;
    }
    setExtracting(true);
    setExtractError(null);
    try {
      const res = await extractProfileFromUrl(url);
      if (res.error && res.low_confidence) {
        setExtractError(res.error === "llm_unavailable"
          ? "Serviço de IA indisponível. Preencha manualmente."
          : `Não conseguimos ler o site: ${res.error}`
        );
        return;
      }
      setProfile({ ...EMPTY_PROFILE, ...res.profile, url_site: url });
      setConfidence(res.confidence);
      setMode("form");
    } catch {
      setExtractError("Não foi possível conectar ao servidor.");
    } finally {
      setExtracting(false);
    }
  }

  function pickFile(f: File | null) {
    setExtractError(null);
    if (!f) {
      setDocFile(null);
      return;
    }
    const isPdf = f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      setExtractError("Envie um arquivo PDF.");
      setDocFile(null);
      return;
    }
    if (f.size > MAX_PDF_BYTES) {
      setExtractError("Arquivo muito grande (máx. 10MB).");
      setDocFile(null);
      return;
    }
    setDocFile(f);
  }

  async function handleExtractDoc() {
    if (!docFile) return;
    setDocExtracting(true);
    setExtractError(null);
    try {
      const res = await extractProfileFromDocument(docFile);
      if (res.error && res.low_confidence) {
        setExtractError(res.error === "llm_unavailable"
          ? "Serviço de IA indisponível. Preencha manualmente."
          : `Não conseguimos ler o documento: ${res.error}`
        );
        return;
      }
      setProfile({ ...EMPTY_PROFILE, ...res.profile });
      setConfidence(res.confidence);
      setMode("form");
    } catch {
      setExtractError("Não foi possível conectar ao servidor.");
    } finally {
      setDocExtracting(false);
    }
  }

  const canFinish = isComplete(profile);

  // ── URL-input screen ──────────────────────────────────────────────────────
  if (mode === "url-input") {
    return (
      <div className="min-h-screen bg-app-bg flex items-center justify-center p-4">
        <div className="w-full max-w-lg">
          <div className="text-center mb-8">
            <p className="text-xs font-semibold uppercase tracking-widest text-primary font-sans mb-2">
              Radar de Editais
            </p>
            <h1 className="font-heading text-2xl font-bold text-content-primary">
              Conta-nos sobre sua empresa
            </h1>
            <p className="text-sm text-content-secondary font-sans mt-1">
              Cole o site da empresa e extraímos o perfil automaticamente.
            </p>
          </div>

          <div className="bg-white rounded-2xl border border-border p-6 shadow-card">
            <Tabs
              items={[
                {
                  value: "url",
                  label: "URL do site",
                  content: (
                    <div className="space-y-4">
                      <div>
                        <label className="block text-sm font-medium text-content-primary font-sans mb-1.5">
                          URL do site da empresa
                        </label>
                        <div className="flex gap-2">
                          <input
                            ref={urlRef}
                            type="url"
                            className={cn(INPUT_CLS, "flex-1")}
                            value={urlInput}
                            onChange={(e) => setUrlInput(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && handleExtract()}
                            placeholder="https://suaempresa.com.br"
                            disabled={extracting}
                          />
                          <button
                            type="button"
                            onClick={handleExtract}
                            disabled={extracting || !urlInput.trim()}
                            className={cn(
                              "shrink-0 px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white",
                              "bg-primary hover:bg-primary-hover transition-colors",
                              "disabled:opacity-40 disabled:cursor-not-allowed"
                            )}
                          >
                            {extracting ? (
                              <span className="flex items-center gap-2">
                                <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                                Lendo...
                              </span>
                            ) : "Extrair"}
                          </button>
                        </div>
                      </div>

                      {extractError && (
                        <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
                          {extractError}
                        </div>
                      )}

                      {extracting && (
                        <div className="space-y-2">
                          {[80, 60, 72].map((w, i) => (
                            <div key={i} className="h-3 bg-gray-100 rounded animate-pulse" style={{ width: `${w}%` }} />
                          ))}
                        </div>
                      )}
                    </div>
                  ),
                },
                {
                  value: "doc",
                  label: "Enviar proposta antiga",
                  content: (
                    <div className="space-y-4">
                      <div>
                        <label className="block text-sm font-medium text-content-primary font-sans mb-1.5">
                          Proposta ou documento (PDF)
                        </label>
                        <p className="text-xs text-content-secondary font-sans mb-2">
                          Extraímos o perfil de uma proposta antiga ou apresentação da empresa.
                        </p>
                        <div
                          onClick={() => !docExtracting && fileRef.current?.click()}
                          className={cn(
                            "border-2 border-dashed border-border rounded-lg p-6 text-center cursor-pointer",
                            "hover:border-primary/40 transition-colors",
                            docFile && "border-primary/40 bg-primary/5",
                            docExtracting && "opacity-60 cursor-not-allowed"
                          )}
                        >
                          <input
                            ref={fileRef}
                            type="file"
                            accept=".pdf,application/pdf"
                            className="hidden"
                            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
                          />
                          {docFile ? (
                            <p className="text-sm font-sans text-content-primary">{docFile.name}</p>
                          ) : (
                            <p className="text-sm font-sans text-content-secondary">
                              Clique para selecionar um PDF (máx. 10MB)
                            </p>
                          )}
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={handleExtractDoc}
                        disabled={docExtracting || !docFile}
                        className={cn(
                          "w-full px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white",
                          "bg-primary hover:bg-primary-hover transition-colors",
                          "disabled:opacity-40 disabled:cursor-not-allowed"
                        )}
                      >
                        {docExtracting ? (
                          <span className="flex items-center justify-center gap-2">
                            <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                            Lendo...
                          </span>
                        ) : "Extrair perfil"}
                      </button>

                      {extractError && (
                        <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
                          {extractError}
                        </div>
                      )}
                    </div>
                  ),
                },
              ]}
              defaultValue="url"
              onValueChange={() => setExtractError(null)}
            />
          </div>

          <div className="mt-4 flex items-center gap-2 text-xs text-content-secondary">
            <div className="flex-1 h-px bg-border" />
            <span className="font-sans">ou</span>
            <div className="flex-1 h-px bg-border" />
          </div>

          <p className="text-center mt-4">
            <button
              type="button"
              onClick={() => setMode("form")}
              className="text-xs text-content-secondary hover:text-content-primary font-sans transition-colors underline-offset-2 hover:underline"
            >
              Ainda não tenho um site — preencher manualmente →
            </button>
          </p>
        </div>
      </div>
    );
  }

  // ── Form screen (single page) ─────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-app-bg flex items-center justify-center p-4">
      <div className="w-full max-w-xl">
        <div className="text-center mb-8">
          <p className="text-xs font-semibold uppercase tracking-widest text-primary font-sans mb-2">
            Radar de Editais
          </p>
          <h1 className="font-heading text-2xl font-bold text-content-primary">
            Perfil da empresa
          </h1>
          <p className="text-sm text-content-secondary font-sans mt-1">
            Só o essencial para o matching. Você complementa o resto ao escrever cada proposta.
          </p>
        </div>

        <div className="bg-white rounded-2xl border border-border p-6 shadow-card">
          <ProfileForm profile={profile} set={set} confidence={confidence} />

          <div className="flex items-center justify-end mt-6 pt-4 border-t border-border">
            <button
              type="button"
              onClick={handleFinish}
              disabled={!canFinish || saving}
              className={cn(
                "px-6 py-2.5 rounded-xl text-sm font-semibold font-sans text-white transition-colors",
                "bg-primary hover:bg-primary-hover",
                "disabled:opacity-40 disabled:cursor-not-allowed",
                "focus:outline-none focus:ring-2 focus:ring-primary/50"
              )}
            >
              {saving ? (
                <span className="flex items-center gap-2">
                  <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Salvando...
                </span>
              ) : "Começar a explorar →"}
            </button>
          </div>
        </div>

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
