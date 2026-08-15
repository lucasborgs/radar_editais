"use client";

import type { InnovationPath, PathExplanation } from "@/lib/api";
import { pathTypeLabel } from "@/lib/radar-utils";

// Caminho de inovação (spec product-pathways-domain-matching.md): badge do tipo,
// próximo passo sempre visível e explicação (confirmados/inferidos/pendentes/
// lacunas) colapsável. A fonte autoritativa do label é `explicacao.dominio`
// (TIPO_LABEL do backend); o fallback é o mapa local por `tipo`.
function ListRow({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <p className="text-[11px] font-medium text-content-secondary">{label}</p>
      <ul className="mt-0.5 space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-[11px] text-content-primary leading-snug">• {item}</li>
        ))}
      </ul>
    </div>
  );
}

const STATUS_LABELS: Record<string, string> = {
  candidatura_viável: "Caminho com candidatura viável",
  lacunas: "Caminho com lacunas",
  possibilidade: "Possibilidade — não é promessa de aprovação",
};

function isUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

export function PathBlock({
  path,
  explanation,
}: {
  path?: InnovationPath | null;
  explanation?: PathExplanation | null;
}) {
  if (!path) return null;
  const label = explanation?.dominio ?? pathTypeLabel(path.tipo);
  const statusLabel = STATUS_LABELS[path.status];

  return (
    <div className="mt-2 pt-2 border-t border-border">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {label && (
          <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
            {label}
          </span>
        )}
        {statusLabel && (
          <span className="text-[11px] text-content-tertiary">{statusLabel}</span>
        )}
      </div>

      <p className="mt-1.5 text-xs text-content-primary leading-snug">
        <span className="font-medium text-content-primary">Próximo passo:</span>{" "}
        {path.proximo_passo}
      </p>

      <details className="group mt-1.5">
        <summary className="flex cursor-pointer list-none items-center gap-1 text-[11px] font-medium text-primary hover:underline [&::-webkit-details-marker]:hidden">
          <span className="transition-transform group-open:rotate-90">›</span>
          Como o Consultor avalia este caminho
        </summary>
        <div className="mt-2 space-y-2">
          {explanation?.criterios && (
            <p className="text-[11px] text-content-tertiary">
              Critérios considerados: {explanation.criterios}
            </p>
          )}
          <ListRow label="Requisitos" items={path.requisitos} />
          {path.canal_de_acesso && (
            <div>
              <p className="text-[11px] font-medium text-content-secondary">Canal de acesso</p>
              {isUrl(path.canal_de_acesso) ? (
                <a
                  href={path.canal_de_acesso}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-0.5 block break-all text-[11px] text-primary hover:underline"
                >
                  {path.canal_de_acesso}
                </a>
              ) : (
                <p className="mt-0.5 break-words text-[11px] text-content-primary leading-snug">
                  {path.canal_de_acesso}
                </p>
              )}
            </div>
          )}
          {explanation && (
            <>
              <ListRow label="Confirmado" items={explanation.confirmados} />
              <ListRow label="Inferido" items={explanation.inferidos} />
              <ListRow label="Pendente" items={explanation.pendentes} />
              <ListRow label="Lacunas" items={explanation.lacunas} />
            </>
          )}
        </div>
      </details>
    </div>
  );
}
