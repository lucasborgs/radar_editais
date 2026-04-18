"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { StatusBadge } from "@/components/ui";
import { getEditalById } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { EditalCard } from "@/types/edital";

// ── Sub-components ───────────────────────────────────────────────────────────

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="text-xs font-sans text-content-secondary w-36 shrink-0">{label}</span>
      <span className="text-xs font-sans text-content-primary">{value}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-border p-5 mb-4">
      <h2 className="font-heading text-sm font-bold text-content-primary mb-3 pb-2 border-b border-border">
        {title}
      </h2>
      {children}
    </div>
  );
}

function TagList({ tags }: { tags: string[] }) {
  if (!tags.length) return <span className="text-xs text-content-secondary font-sans">—</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map((t) => (
        <span
          key={t}
          className="text-[11px] font-sans bg-primary/10 text-primary rounded-full px-2.5 py-0.5"
        >
          {t}
        </span>
      ))}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function EditalDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params?.id as string;

  const [edital, setEdital] = useState<EditalCard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);
    getEditalById(id)
      .then(setEdital)
      .catch((e) => setError(e instanceof Error ? e.message : "Erro desconhecido"))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <DashboardLayout title="Edital">
        <div className="flex items-center gap-3 py-8 text-sm text-content-secondary font-sans">
          <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          Carregando edital...
        </div>
      </DashboardLayout>
    );
  }

  if (error || !edital) {
    return (
      <DashboardLayout title="Edital">
        <div className="rounded-xl bg-red-50 border border-red-200 p-6 text-sm text-red-700 font-sans">
          <strong>Edital não encontrado.</strong> {error}
          <br />
          <button
            onClick={() => router.push("/editais")}
            className="mt-3 underline hover:text-red-900"
          >
            Voltar para a lista
          </button>
        </div>
      </DashboardLayout>
    );
  }

  const mechanism_labels: Record<string, string> = {
    subvencao: "Subvenção (não reembolsável)",
    reembolsavel: "Crédito reembolsável",
    investimento: "Investimento direto",
    misto: "Misto",
  };

  return (
    <DashboardLayout title={edital.title}>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-xs font-sans text-content-secondary mb-4">
        <button onClick={() => router.push("/editais")} className="hover:text-primary transition-colors">
          Editais
        </button>
        <span>/</span>
        <span className="text-content-primary font-medium truncate max-w-[400px]">{edital.title}</span>
      </div>

      {/* Header card */}
      <div className="bg-white rounded-xl border border-border p-5 mb-4">
        <div className="flex items-start justify-between gap-4 mb-3">
          <h1 className="font-heading text-lg font-bold text-content-primary leading-snug flex-1">
            {edital.title}
          </h1>
          <StatusBadge status={edital.status} />
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-2 mb-4">
          {edital.deadline && <InfoRow label="Prazo" value={edital.deadline} />}
          {edital.pub_date && <InfoRow label="Publicação" value={edital.pub_date} />}
          {edital.mechanism && (
            <InfoRow
              label="Mecanismo"
              value={mechanism_labels[edital.mechanism] ?? edital.mechanism}
            />
          )}
          {edital.value_range?.max_brl && (
            <InfoRow
              label="Valor máximo"
              value={`R$ ${edital.value_range.max_brl.toLocaleString("pt-BR")}`}
            />
          )}
          {edital.value_range?.min_brl && (
            <InfoRow
              label="Valor mínimo"
              value={`R$ ${edital.value_range.min_brl.toLocaleString("pt-BR")}`}
            />
          )}
          {(edital.trl_range?.min || edital.trl_range?.max) && (
            <InfoRow
              label="TRL aceito"
              value={`${edital.trl_range.min ?? "1"} – ${edital.trl_range.max ?? "9"}`}
            />
          )}
          {edital.counterpart_required && (
            <InfoRow label="Contrapartida" value="Obrigatória" />
          )}
        </div>

        {edital.link && (
          <a
            href={edital.link}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs font-sans text-primary hover:underline"
          >
            Ver edital original ↗
          </a>
        )}
      </div>

      {/* Objective */}
      {edital.objective && (
        <Section title="Objetivo">
          <p className="text-sm font-sans text-content-primary leading-relaxed">
            {edital.objective}
          </p>
        </Section>
      )}

      {/* Themes & audiences */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 mb-4">
        <div className="bg-white rounded-xl border border-border p-5">
          <h2 className="font-heading text-sm font-bold text-content-primary mb-3 pb-2 border-b border-border">
            Temas
          </h2>
          <TagList tags={edital.themes} />
        </div>
        <div className="bg-white rounded-xl border border-border p-5">
          <h2 className="font-heading text-sm font-bold text-content-primary mb-3 pb-2 border-b border-border">
            Público-alvo
          </h2>
          <TagList tags={edital.eligible_entities.length ? edital.eligible_entities : edital.publico_alvo} />
        </div>
      </div>

      {/* Key requirements */}
      {edital.key_requirements.length > 0 && (
        <Section title="Requisitos principais">
          <ul className="space-y-1.5">
            {edital.key_requirements.map((r, i) => (
              <li key={i} className="text-sm font-sans text-content-primary flex gap-2">
                <span className="text-primary shrink-0 mt-0.5">·</span>
                {r}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* Key facts */}
      {edital.key_facts.length > 0 && (
        <Section title="Fatos-chave do edital">
          <ul className="space-y-1.5">
            {edital.key_facts.map((f, i) => (
              <li key={i} className="text-sm font-sans text-content-secondary flex gap-2">
                <span className="text-content-secondary shrink-0 mt-0.5">{i + 1}.</span>
                {f}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* CTA — Iniciar sessão de escrita */}
      <div className="bg-white rounded-xl border border-border p-5 flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-content-primary font-sans">
            Pronto para escrever a proposta?
          </p>
          <p className="text-xs text-content-secondary font-sans mt-0.5">
            Inicie uma sessão de escrita assistida por IA para este edital.
          </p>
        </div>
        <button
          onClick={() => router.push(`/chat?edital=${edital.id}`)}
          className={cn(
            "px-5 py-2.5 rounded-xl text-sm font-semibold font-sans text-white shrink-0",
            "bg-primary hover:bg-primary-hover transition-colors",
            "focus:outline-none focus:ring-2 focus:ring-primary/50"
          )}
        >
          Iniciar Escrita
        </button>
      </div>
    </DashboardLayout>
  );
}
