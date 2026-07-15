"use client";

// Aba Perfil (decisão 2026-06-13): consolida em uma tela as três fontes do
// perfil/“org memory” da empresa que antes viviam espalhadas —
//   1. Extração automática do site (POST /profile/extract crawleia a URL);
//   2. Os campos do antigo onboarding (CompanyProfile, salvos em PUT /me/profile);
//   3. Anexos de documentos da empresa (library items — a memória de organização
//      que a escrita de proposta consome via @ menção / RAG).
// Reusa os metadados de campo do front-door (fieldSpec/coerceFieldValue) — sem
// duplicar o conhecimento de tipos/opções por campo.

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
  getMe,
  saveProfile,
  extractProfileFromUrl,
  extractProfileFromDocument,
  getLibraryItems,
  uploadLibraryPdf,
  deleteLibraryItem,
} from "@/lib/api";
import type { ContentItemSummary } from "@/types/api";
import { CompanyProfile, EMPTY_PROFILE } from "@/types/profile";
import { fieldSpec, coerceFieldValue } from "@/components/frontdoor/profileFields";
import { InvestorTrackToggle } from "@/components/frontdoor/InvestorTrackToggle";

// Campos Q3/Q4 (trilha de capital de risco, spec D2). Revelados pelo switch
// "busca também capital de risco?"; alimentam o match de investidor (always-on
// no radar — o switch só revela/coleta, não gateia resultados).
const INVESTOR_FIELDS: ReadonlySet<keyof CompanyProfile> = new Set<keyof CompanyProfile>([
  "estagio",
  "mrr_arr",
  "round_alvo_brl",
  "cap_table_resumo",
  "tracao_resumo",
]);

// Trilha de investidor "ligada" por default se "Capital de risco" está selecionado
// OU se qualquer campo Q3/Q4 já tem valor (decisão travada D2: flag derivada).
function deriveInvestorTrack(profile: CompanyProfile): boolean {
  if (profile.tipos_financiamento_interesse.includes("capital_risco")) return true;
  return Array.from(INVESTOR_FIELDS).some((f) => {
    const v = profile[f];
    return v !== "" && v !== null && v !== undefined;
  });
}

// Agrupamento dos campos do CompanyProfile em seções legíveis + rótulos PT-BR.
// A ordem aqui define a ordem de render; o tipo de input vem de fieldSpec().
const FIELD_GROUPS: { title: string; fields: (keyof CompanyProfile)[] }[] = [
  { title: "Identidade", fields: ["nome", "cnpj", "url_site", "tipo_entidade", "uf", "ano_fundacao"] },
  { title: "Proposta", fields: ["one_liner", "solution_summary", "descricao_atividades"] },
  { title: "Maturidade", fields: ["tamanho_empresa", "trl", "estagio", "faturamento_anual", "capital_social"] },
  { title: "Investimento", fields: ["tipos_financiamento_interesse", "mrr_arr", "round_alvo_brl"] },
  { title: "Narrativas", fields: ["portfolio_projetos", "equipe_resumo", "tracao_resumo", "cap_table_resumo"] },
];

const FIELD_LABELS: Partial<Record<keyof CompanyProfile, string>> = {
  nome: "Nome da empresa",
  cnpj: "CNPJ",
  url_site: "Site",
  tipo_entidade: "Tipo de entidade",
  uf: "UF",
  ano_fundacao: "Ano de fundação",
  one_liner: "Proposta (one-liner)",
  solution_summary: "Resumo da solução",
  descricao_atividades: "Descrição das atividades",
  tamanho_empresa: "Porte",
  trl: "TRL",
  estagio: "Estágio",
  faturamento_anual: "Faturamento anual (R$)",
  capital_social: "Capital social (R$)",
  tipos_financiamento_interesse: "Interesse de financiamento",
  mrr_arr: "MRR/ARR (R$)",
  round_alvo_brl: "Round alvo (R$)",
  portfolio_projetos: "Portfólio de projetos",
  equipe_resumo: "Equipe",
  tracao_resumo: "Tração",
  cap_table_resumo: "Cap table",
};

// ── Input por campo (deriva o tipo de fieldSpec) ──────────────────────────────
function FieldInput({
  field,
  value,
  onChange,
}: {
  field: keyof CompanyProfile;
  value: unknown;
  onChange: (raw: unknown) => void;
}) {
  const spec = fieldSpec(field);
  const base =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans text-content-primary focus:outline-none focus:ring-2 focus:ring-primary/40";

  if (spec.kind === "textarea") {
    return (
      <textarea
        rows={3}
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className={cn(base, "resize-y")}
      />
    );
  }
  if (spec.kind === "number") {
    return (
      <input
        type="number"
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => onChange(e.target.value)}
        className={base}
      />
    );
  }
  if (spec.kind === "select") {
    return (
      <select value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} className={base}>
        <option value="">—</option>
        {(spec.options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (spec.kind === "multiselect") {
    const selected = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {(spec.options ?? []).map((o) => {
          const on = selected.includes(o.value);
          return (
            <button
              key={o.value}
              type="button"
              onClick={() =>
                onChange(on ? selected.filter((v) => v !== o.value) : [...selected, o.value])
              }
              className={cn(
                "px-2.5 py-1 rounded-full text-xs font-sans border transition-colors",
                on
                  ? "bg-primary/10 border-primary/40 text-primary"
                  : "border-border text-content-secondary hover:bg-content-secondary/10",
              )}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    );
  }
  return (
    <input
      type="text"
      value={(value as string) ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className={base}
    />
  );
}

// ── Página ────────────────────────────────────────────────────────────────────
export default function PerfilPage() {
  const { getToken } = useAuth();

  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Trilha de capital de risco (switch D2). Default-on derivado dos campos Q3/Q4
  // preenchidos no perfil carregado; toggle manual depois. Estado persiste via os
  // próprios campos Q3/Q4 salvos no perfil (re-derivado no próximo carregamento).
  const [investorTrack, setInvestorTrack] = useState(false);

  // Extração
  const [url, setUrl] = useState("");
  const [extracting, setExtracting] = useState(false);
  const docInputRef = useRef<HTMLInputElement>(null);

  // Arquivos da empresa (org memory)
  const [items, setItems] = useState<ContentItemSummary[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  // Carrega perfil + arquivos ao montar.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getToken();
        if (!token || cancelled) return;
        const [me, libs] = await Promise.all([getMe(token), getLibraryItems(token)]);
        if (cancelled) return;
        const loaded = { ...EMPTY_PROFILE, ...(me.profile ?? {}) } as CompanyProfile;
        setProfile(loaded);
        setInvestorTrack(deriveInvestorTrack(loaded));
        setItems(libs ?? []);
      } catch {
        if (!cancelled) toast.error("Não consegui carregar seu perfil.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  const setField = useCallback((field: keyof CompanyProfile, raw: unknown) => {
    setProfile((prev) => {
      const next = { ...prev, [field]: coerceFieldValue(field, raw) };
      // Preencheu campo de investidor → auto-adiciona "Capital de risco"
      if (INVESTOR_FIELDS.has(field)) {
        const v = next[field];
        const filled = v !== "" && v !== null && v !== undefined;
        if (filled && !next.tipos_financiamento_interesse.includes("capital_risco")) {
          next.tipos_financiamento_interesse = [...next.tipos_financiamento_interesse, "capital_risco"];
        }
      }
      return next;
    });
    setDirty(true);
  }, []);

  // Mescla campos extraídos (não-vazios) sobre o perfil atual; usuário revisa e salva.
  const mergeExtracted = useCallback((extracted: Partial<CompanyProfile>) => {
    let changed = 0;
    setProfile((prev) => {
      const next = { ...prev };
      (Object.keys(extracted) as (keyof CompanyProfile)[]).forEach((f) => {
        if (!(f in EMPTY_PROFILE)) return;
        const v = extracted[f];
        const empty = v === "" || v === null || v === undefined || (Array.isArray(v) && v.length === 0);
        if (!empty) {
          (next as Record<string, unknown>)[f] = v;
          changed += 1;
        }
      });
      return next;
    });
    setDirty(true);
    return changed;
  }, []);

  const handleExtractUrl = useCallback(async () => {
    const u = url.trim();
    if (!u) return;
    if (!/^https?:\/\//.test(u)) {
      toast.error("A URL deve começar com http:// ou https://");
      return;
    }
    setExtracting(true);
    const t = toast.loading("Lendo o site da empresa…");
    try {
      const res = await extractProfileFromUrl(u);
      if (res.error) {
        toast.dismiss(t);
        toast.error(res.error);
        return;
      }
      const n = mergeExtracted(res.profile ?? {});
      toast.dismiss(t);
      toast.success(n > 0 ? `${n} campo(s) preenchido(s) — revise e salve.` : "Nada novo encontrado no site.");
    } catch (e) {
      toast.dismiss(t);
      toast.error(e instanceof Error ? e.message : "Falha ao ler o site.");
    } finally {
      setExtracting(false);
    }
  }, [url, mergeExtracted]);

  const handleExtractDoc = useCallback(
    async (file: File) => {
      setExtracting(true);
      const t = toast.loading("Lendo o documento…");
      try {
        const res = await extractProfileFromDocument(file);
        if (res.error) {
          toast.dismiss(t);
          toast.error(res.error);
          return;
        }
        const n = mergeExtracted(res.profile ?? {});
        toast.dismiss(t);
        toast.success(n > 0 ? `${n} campo(s) preenchido(s) — revise e salve.` : "Nada de perfil no documento.");
      } catch (e) {
        toast.dismiss(t);
        toast.error(e instanceof Error ? e.message : "Falha ao ler o documento.");
      } finally {
        setExtracting(false);
      }
    },
    [mergeExtracted],
  );

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const token = await getToken();
      if (!token) return;
      await saveProfile(profile, token);
      setDirty(false);
      toast.success("Perfil salvo.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Não consegui salvar o perfil.");
    } finally {
      setSaving(false);
    }
  }, [profile, getToken]);

  // Anexos de empresa → library (mesmo caminho do composer e da página Arquivos).
  const handleUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      const t = toast.loading("Enviando arquivo…");
      try {
        const token = await getToken();
        if (!token) return;
        const item = await uploadLibraryPdf(
          file,
          { title: file.name, type: "technical_doc", tags: "" },
          token,
        );
        setItems((prev) => [item, ...prev]);
        toast.dismiss(t);
        toast.success("Arquivo enviado — disponível para a escrita via @ menção.");
      } catch (e) {
        toast.dismiss(t);
        toast.error(e instanceof Error ? e.message : "Falha no upload.");
      } finally {
        setUploading(false);
      }
    },
    [getToken],
  );

  const handleDeleteItem = useCallback(
    async (id: string) => {
      if (!confirm("Excluir este arquivo permanentemente?")) return;
      try {
        const token = await getToken();
        if (!token) return;
        await deleteLibraryItem(id, token);
        setItems((prev) => prev.filter((i) => i.id !== id));
      } catch {
        toast.error("Erro ao excluir o arquivo.");
      }
    },
    [getToken],
  );

  return (
    <DashboardLayout title="Perfil">
      <div className="max-w-3xl mx-auto space-y-8 pb-16">
        {/* Extração automática */}
        <section className="rounded-xl border border-border bg-surface p-5">
          <h2 className="font-heading text-sm font-bold text-content-primary mb-1">
            Preencher automaticamente
          </h2>
          <p className="text-xs text-content-secondary font-sans mb-4">
            Extraia os dados do site da empresa ou de um documento (proposta antiga,
            apresentação). Os campos preenchidos entram abaixo para você revisar antes de salvar.
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void handleExtractUrl()}
              placeholder="https://suaempresa.com.br"
              className="flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
            <button
              onClick={() => void handleExtractUrl()}
              disabled={extracting || !url.trim()}
              className="px-4 py-2 rounded-lg bg-primary text-white text-sm font-semibold font-sans disabled:opacity-50 hover:bg-primary/90 transition-colors whitespace-nowrap"
            >
              {extracting ? "Lendo…" : "Extrair do site"}
            </button>
            <button
              onClick={() => docInputRef.current?.click()}
              disabled={extracting}
              className="px-4 py-2 rounded-lg border border-border text-sm font-medium font-sans text-content-primary disabled:opacity-50 hover:bg-content-secondary/10 transition-colors whitespace-nowrap"
            >
              Enviar documento
            </button>
            <input
              ref={docInputRef}
              type="file"
              accept=".pdf,.doc,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleExtractDoc(f);
                e.target.value = "";
              }}
            />
          </div>
        </section>

        {/* Trilha de capital de risco (switch D2) */}
        {!loading && (
          <section className="rounded-xl border border-border bg-surface p-5">
            <InvestorTrackToggle on={investorTrack} onChange={setInvestorTrack} />
          </section>
        )}

        {/* Form de campos */}
        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-24 rounded-xl bg-content-secondary/10 animate-pulse" />
            ))}
          </div>
        ) : (
          FIELD_GROUPS.map((group) => {
            // Esconde os campos Q3/Q4 quando a trilha de investidor está desligada.
            const groupFields = investorTrack
              ? group.fields
              : group.fields.filter((f) => !INVESTOR_FIELDS.has(f));
            if (groupFields.length === 0) return null;
            return (
            <section key={group.title} className="rounded-xl border border-border bg-surface p-5">
              <h2 className="font-heading text-sm font-bold text-content-primary mb-4">
                {group.title}
              </h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {groupFields.map((field) => {
                  const spec = fieldSpec(field);
                  const wide = spec.kind === "textarea" || spec.kind === "multiselect";
                  return (
                    <div key={field} className={cn(wide && "sm:col-span-2")}>
                      <label className="block text-xs font-semibold text-content-secondary font-sans mb-1">
                        {FIELD_LABELS[field] ?? field}
                      </label>
                      <FieldInput
                        field={field}
                        value={profile[field]}
                        onChange={(raw) => setField(field, raw)}
                      />
                    </div>
                  );
                })}
              </div>
            </section>
            );
          })
        )}

        {/* Arquivos da empresa (org memory) */}
        <section className="rounded-xl border border-border bg-surface p-5">
          <div className="flex items-center justify-between mb-1">
            <h2 className="font-heading text-sm font-bold text-content-primary">
              Arquivos da empresa
            </h2>
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="px-3 py-1.5 rounded-lg border border-border text-xs font-medium font-sans text-content-primary disabled:opacity-50 hover:bg-content-secondary/10 transition-colors"
            >
              {uploading ? "Enviando…" : "+ Anexar arquivo"}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.doc,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleUpload(f);
                e.target.value = "";
              }}
            />
          </div>
          <p className="text-xs text-content-secondary font-sans mb-4">
            Propostas antigas, certificados e materiais que a IA usa como memória ao
            escrever (referenciáveis por @ menção na escrita).
          </p>
          {items.length === 0 ? (
            <p className="text-sm text-content-secondary font-sans py-4 text-center">
              Nenhum arquivo ainda.
            </p>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((it) => (
                <li key={it.id} className="flex items-center gap-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-sans text-content-primary truncate">{it.title}</p>
                    <p className="text-xs text-content-secondary font-sans">
                      {it.type}
                      {it.enrichment_status === "pending" && " · processando…"}
                    </p>
                  </div>
                  <button
                    onClick={() => void handleDeleteItem(it.id)}
                    className="text-xs font-sans text-red-600 dark:text-red-400 hover:underline shrink-0"
                  >
                    Excluir
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* Barra de salvar fixa quando há mudanças */}
      {dirty && !loading && (
        <div className="sticky bottom-0 left-0 right-0 border-t border-border bg-surface px-5 py-3 flex items-center justify-end gap-3 -mx-6 mt-6">
          <span className="text-xs text-content-secondary font-sans">Alterações não salvas</span>
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-5 py-2 rounded-lg bg-primary text-white text-sm font-semibold font-sans disabled:opacity-50 hover:bg-primary/90 transition-colors"
          >
            {saving ? "Salvando…" : "Salvar perfil"}
          </button>
        </div>
      )}
    </DashboardLayout>
  );
}
