"use client";

import type { IctPartner } from "@/lib/api";
import { PathBlock } from "./PathBlock";
import { SetorChips } from "./MatchedEditalCard";

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full bg-content-secondary/10 px-2 py-0.5 text-[11px] text-content-secondary">
      {children}
    </span>
  );
}

// ICT/laboratório como CAPACIDADE/PARCEIRO (spec product-pathways-domain-matching):
// não é oportunidade — não há botão de proposta nem ranking de afinidade. O card
// mostra as capacidades declaradas pela fonte e o próximo passo de parceria.
export function IctPartnerCard({ partner }: { partner: IctPartner }) {
  const cap = partner.capacidades;
  const source = partner.source ? (partner.source.toUpperCase() === "PNIPE" ? "PNIPE" : "EMBRAPII") : undefined;

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-3 text-sm font-sans">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
            <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
              ICT / laboratório
            </span>
            {source && <Chip>{source}</Chip>}
            {partner.uf && <Chip>{partner.uf}</Chip>}
          </div>
          <p className="text-sm font-medium text-content-primary leading-snug line-clamp-2">
            {partner.name}
          </p>
          <p className="text-[11px] text-content-secondary mt-0.5">
            {[cap?.institution, cap?.municipio].filter(Boolean).join(" · ") || "Capacidade de P&D"}
          </p>
          {partner.description && (
            <p className="text-xs text-content-secondary mt-1 line-clamp-2">
              {partner.description}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
            <SetorChips setores={partner.themes} />
          </div>

          {(cap?.competencias.length || cap?.equipamentos.length) && (
            <div className="mt-2 space-y-1.5">
              {cap.competencias.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {cap.competencias.slice(0, 5).map((c, i) => (
                    <Chip key={i}>{c}</Chip>
                  ))}
                </div>
              )}
              {cap.equipamentos.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {cap.equipamentos.slice(0, 4).map((eq, i) => (
                    <Chip key={i}>{eq}</Chip>
                  ))}
                </div>
              )}
            </div>
          )}

          {(cap?.condicoes_acesso || cap?.verificado_em) && (
            <div className="mt-2 space-y-0.5 text-[11px] text-content-tertiary">
              {cap.condicoes_acesso && <p>Condições de acesso: {cap.condicoes_acesso}</p>}
              {cap.verificado_em && <p>Capacidade verificada em {cap.verificado_em}</p>}
            </div>
          )}
        </div>
      </div>

      {partner.url && (
        <div className="mt-2 text-xs">
          <a
            href={partner.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            Ver a ICT / contato ↗
          </a>
        </div>
      )}

      <PathBlock path={partner.caminho} explanation={partner.explicacao} />
    </div>
  );
}
