"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/DashboardLayout";
import { StatusBadge } from "@/components/ui";
import { VerdictBlock } from "@/components/frontdoor/VerdictBlock";
import { getOportunidadeById, fetchMatchVerdicts } from "@/lib/api";
import { cn, parseDeadline } from "@/lib/utils";
import {
  isContinuousConfirmed,
  temporalBadge,
  temporalDeadlineText,
  temporalVerificationNote,
} from "@/lib/opportunity-temporal";
import type { MatchVerdict } from "@/lib/api";
import type {
  OportunidadeDetail,
  EligibilityConstraint,
  TicketRange,
  EditalStatus,
  FieldProvenance,
} from "@/types/edital";
import { ProvenanceHint } from "@/components/ProvenanceHint";

// ── Rótulos v2 ───────────────────────────────────────────────────────────────

const KIND_LABEL: Record<string, string> = {
  edital: "Edital",
  programa: "Programa",
  investimento: "Investimento",
  desafio: "Desafio",
};

const PORTE_LABEL: Record<string, string> = {
  mei: "MEI", me: "ME", epp: "EPP", media: "Média", grande: "Grande",
};

const brl = (n: number) => `R$ ${n.toLocaleString("pt-BR")}`;

function constraintLabel(c: EligibilityConstraint): string {
  const v = c.valor;
  const asList = (x: unknown): string[] => (Array.isArray(x) ? x.map(String) : [String(x)]);
  switch (c.tipo) {
    case "porte":
      return `Porte: ${asList(v).map((x) => PORTE_LABEL[x] ?? x).join(", ")}`;
    case "sede_uf":
      return `Sede: ${asList(v).map((x) => x.toUpperCase()).join(", ")}`;
    case "faturamento": {
      const opTxt = c.op === "lte" ? "≤" : c.op === "gte" ? "≥" : c.op;
      return `Faturamento ${opTxt} ${typeof v === "number" ? brl(v) : String(v)}`;
    }
    case "trl": {
      const opTxt = c.op === "lte" ? "≤" : c.op === "gte" ? "≥" : c.op;
      return `TRL ${opTxt} ${String(v)}`;
    }
    case "forma_juridica":
      return `Forma jurídica: ${asList(v).join(", ")}`;
    case "parceria":
      return `Exige parceria com ${String(v).toUpperCase()}`;
    default:
      return `${c.tipo} ${c.op} ${asList(v).join(", ")}`;
  }
}

function valueLabel(val: string | TicketRange | null | undefined): string | null {
  if (!val) return null;
  if (typeof val === "string") return val.trim() || null;
  const { min_brl, max_brl } = val;
  if (min_brl && max_brl) return `${brl(min_brl)} – ${brl(max_brl)}`;
  if (max_brl) return `até ${brl(max_brl)}`;
  if (min_brl) return `a partir de ${brl(min_brl)}`;
  return null;
}

// A chave do cache de veredito é o file_key do hipergrado (`source__native`) p/
// editais; curados (Parte B) usam o node-id. Editais são o caso com veredito hoje.
function verdictKeyFor(detail: OportunidadeDetail): string {
  return detail.kind === "edital" ? detail.id.replace(":", "__") : detail.id;
}

// ── Sub-components ───────────────────────────────────────────────────────────

function InfoRow({ label, value, provenance }: { label: string; value: React.ReactNode; provenance?: FieldProvenance }) {
  return (
    <div className="flex gap-3">
      <span className="text-xs font-sans text-content-secondary w-36 shrink-0">{label}</span>
      <span className="text-xs font-sans text-content-primary">
        {value}
        {provenance && <ProvenanceHint provenance={provenance} />}
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface rounded-xl border border-border p-5 mb-4">
      <h2 className="font-heading text-sm font-bold text-content-primary mb-3 pb-2 border-b border-border">
        {title}
      </h2>
      {children}
    </div>
  );
}

function TagCard({ title, tags, provenance, curationLabel }: { title: string; tags: string[]; provenance?: FieldProvenance; curationLabel?: "embrapii" | "catalogo" }) {
  if (!tags.length) return null;
  return (
    <div className="min-w-[250px] flex-1 bg-surface rounded-xl border border-border p-5">
      <h2 className="font-heading text-sm font-bold text-content-primary mb-3 pb-2 border-b border-border flex items-center gap-1.5">
        {title}
        {provenance && <ProvenanceHint provenance={provenance} curationLabel={curationLabel} />}
      </h2>
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
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function OportunidadeDetailPage() {
  const params = useParams();
  const router = useRouter();
  // Algumas versões do Next App Router devolvem o segmento encoded; outras,
  // decodificado. decodeURIComponent é idempotente (no-op se já limpo) —
  // previne double-encoding no fetch seguinte (Fix 3).
  const id = decodeURIComponent((params?.id as string) || "");

  const [detail, setDetail] = useState<OportunidadeDetail | null>(null);
  const [verdict, setVerdict] = useState<MatchVerdict | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);
    getOportunidadeById(id)
      .then((d) => {
        setDetail(d);
        // Veredito (Estágio 2) quando existir p/ o workspace — cache-only, auth.
        // Anônimo/sem veredito → silencioso (o card renderiza sem ele).
        fetchMatchVerdicts([verdictKeyFor(d)])
          .then((r) => setVerdict(r.verdicts[verdictKeyFor(d)] ?? null))
          .catch(() => setVerdict(null));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Erro desconhecido"))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <DashboardLayout title="Oportunidade">
        <div className="flex items-center gap-3 py-8 text-sm text-content-secondary font-sans">
          <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          Carregando oportunidade...
        </div>
      </DashboardLayout>
    );
  }

  if (error || !detail) {
    return (
      <DashboardLayout title="Oportunidade">
        <div className="rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 p-6 text-sm text-red-700 dark:text-red-300 font-sans">
          <strong>Oportunidade não encontrada.</strong> {error}
          <br />
          <button
            onClick={() => router.push("/oportunidades")}
            className="mt-3 underline hover:text-red-900"
          >
            Voltar para a lista
          </button>
        </div>
      </DashboardLayout>
    );
  }

  const kindLabel = KIND_LABEL[detail.kind] ?? detail.kind;
  const isEdital = detail.kind === "edital";
  const badge = temporalBadge(detail);
  const temporalDeadline = temporalDeadlineText(detail);
  const verificationNote = temporalVerificationNote(detail);
  const apertureLabel = isContinuousConfirmed(detail)
    ? "Fluxo contínuo"
    : detail.aperture === "recorrente"
      ? "Recorrente"
      : "";
  const showsTemporalStatus = detail.kind === "edital" || detail.kind === "programa" || !!detail.validity_state;
  const value = valueLabel(detail.value);
  const ticket = valueLabel(detail.ticket_range);

  return (
    <DashboardLayout title={detail.title}>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-xs font-sans text-content-secondary mb-4">
        <button onClick={() => router.push("/oportunidades")} className="hover:text-primary transition-colors">
          Oportunidades
        </button>
        <span>/</span>
        <span className="text-content-primary font-medium truncate max-w-[400px]">{detail.title}</span>
      </div>

      {/* Header card */}
      <div className="bg-surface rounded-xl border border-border p-5 mb-4">
        <div className="flex items-start justify-between gap-4 mb-3">
          <h1 className="font-heading text-lg font-bold text-content-primary leading-snug flex-1 flex items-center gap-1.5">
            {detail.title}
            {(detail.kind === "investimento" || detail.kind === "programa") && detail.provenance?.["name"] && (
              <ProvenanceHint provenance={detail.provenance["name"]} curationLabel="catalogo" />
            )}
          </h1>
          {showsTemporalStatus && (
            badge.tone === "review" ? (
              <span className="inline-flex items-center rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium font-sans text-amber-700 dark:text-amber-300">
                {badge.label}
              </span>
            ) : (
              <StatusBadge status={(badge.tone === "active" ? "ABERTA" : "ENCERRADA") as EditalStatus} />
            )
          )}
        </div>

        {/* Kind / aperture / fonte badges */}
        <div className="flex flex-wrap gap-1.5 mb-4">
          <span className="text-[11px] font-sans bg-content-primary/8 text-content-primary rounded-full px-2.5 py-0.5">
            {kindLabel}
          </span>
          {apertureLabel && (
            <span className="text-[11px] font-sans bg-content-primary/5 text-content-secondary rounded-full px-2.5 py-0.5">
              {apertureLabel}
            </span>
          )}
          {detail.fonte_recurso.map((f) => (
            <span key={f} className="text-[11px] font-sans bg-content-primary/5 text-content-secondary rounded-full px-2.5 py-0.5">
              {f}
            </span>
          ))}
        </div>

        {badge.tone === "review" && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200">
            Validade a confirmar. Esta oportunidade permanece consultável, mas não deve ser tratada como aberta até nova verificação.
          </div>
        )}

        <div className="grid grid-cols-2 gap-x-6 gap-y-2 mb-4">
          {temporalDeadline && (() => {
            const parsed = parseDeadline(temporalDeadline);
            return <InfoRow label="Prazo" value={parsed ?? temporalDeadline} />;
          })()}
          {verificationNote && <InfoRow label="Verificação" value={verificationNote} />}
          {detail.mechanism && <InfoRow label="Mecanismo" value={detail.mechanism} provenance={detail.provenance?.["mecanismo"]} />}
          {value && <InfoRow label="Valor" value={value} />}
          {ticket && !value && <InfoRow label="Ticket" value={ticket} />}
          {detail.estagio_alvo && detail.estagio_alvo.length > 0 && (
            <InfoRow label="Estágio-alvo" value={detail.estagio_alvo.join(", ")} />
          )}
          {detail.lead_follow && <InfoRow label="Lead/follow" value={detail.lead_follow} />}
        </div>

        {detail.official_url && (
          <a
            href={detail.official_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs font-sans text-primary hover:underline"
          >
            Ver página oficial ↗
          </a>
        )}
      </div>

      {/* Veredito (Estágio 2) quando existir para o workspace */}
      {verdict && (
        <div className="bg-surface rounded-xl border border-border p-5 mb-4">
          <h2 className="font-heading text-sm font-bold text-content-primary mb-1">
            Análise para o seu perfil
          </h2>
          <VerdictBlock verdict={verdict} />
        </div>
      )}

      {/* Objetivo */}
      {detail.objective && (
        <Section title="Objetivo">
          <p className="text-sm font-sans text-content-primary leading-relaxed">
            {detail.objective}
          </p>
        </Section>
      )}

      {/* Constraints de elegibilidade (chips) */}
      {detail.constraints.length > 0 && (
        <Section title="Elegibilidade">
          <div className="flex flex-wrap gap-1.5">
            {detail.constraints.map((c, i) => (
              <span
                key={i}
                className="text-[11px] font-sans bg-amber-500/10 text-amber-700 dark:text-amber-500 rounded-full px-2.5 py-0.5"
              >
                {constraintLabel(c)}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* Macro-temas */}
      {detail.macro_temas.length > 0 && (
        <Section title="Macro-temas">
          <div className="flex flex-wrap gap-1.5">
            {detail.macro_temas.map((t) => (
              <span
                key={t}
                className="text-[11px] font-sans bg-primary/10 text-primary rounded-full px-2.5 py-0.5 capitalize"
              >
                {t}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* Conceitos cobertos + atores relacionados */}
      <div className="flex flex-wrap gap-4 mb-4">
        <TagCard title="Temas" tags={detail.themes} provenance={detail.provenance?.["setores"]} />
        <TagCard title="Tecnologias" tags={detail.technologies} provenance={detail.provenance?.["tecnologias_tags"]} />
        <TagCard title="Programas" tags={detail.programs} />
        <TagCard title="Público-alvo" tags={detail.eligible_entities.length ? detail.eligible_entities : detail.publico_alvo} />
        <TagCard title="ICTs relacionadas" tags={detail.icts} />
      </div>

      {/* Requisitos */}
      {detail.key_requirements.length > 0 && (
        <Section title="Requisitos">
          <ul className="space-y-1.5">
            {detail.key_requirements.map((r, i) => (
              <li key={i} className="text-sm font-sans text-content-primary flex gap-2">
                <span className="text-primary shrink-0 mt-0.5">·</span>
                {r}
                {detail.provenance?.[`requisitos_texto.${i}`] && (
                  <ProvenanceHint provenance={detail.provenance[`requisitos_texto.${i}`]} />
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* Exclusões */}
      {detail.exclusoes.length > 0 && (
        <Section title="Não elegível / exclusões">
          <ul className="space-y-1.5">
            {detail.exclusoes.map((r, i) => (
              <li key={i} className="text-sm font-sans text-content-secondary flex gap-2">
                <span className="text-red-500 shrink-0 mt-0.5">×</span>
                {r}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* Documentos oficiais */}
      {detail.document_urls.length > 0 && (
        <Section title="Documentos">
          <ul className="space-y-1.5">
            {detail.document_urls.map((u, i) => (
              <li key={i}>
                <a
                  href={u}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs font-sans text-primary hover:underline break-all"
                >
                  {u.split("/").pop() || u} ↗
                </a>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* CTA — só editais têm sessão de escrita */}
      {isEdital && (
        <div className="bg-surface rounded-xl border border-border p-5 flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-content-primary font-sans">
              Pronto para escrever a proposta?
            </p>
            <p className="text-xs text-content-secondary font-sans mt-0.5">
              Inicie uma sessão de escrita assistida por IA para este edital.
            </p>
          </div>
          <button
            onClick={() => router.push(`/chat?edital=${detail.id}`)}
            className={cn(
              "px-5 py-2.5 rounded-xl text-sm font-semibold font-sans text-white shrink-0",
              "bg-primary hover:bg-primary-hover transition-colors",
              "focus:outline-none focus:ring-2 focus:ring-primary/50",
            )}
          >
            Iniciar Escrita
          </button>
        </div>
      )}
    </DashboardLayout>
  );
}
