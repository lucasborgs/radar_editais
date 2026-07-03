"use client";

import Link from "next/link";

const MISSING_FIELD_LABELS: Record<string, string> = {
  nome: "Nome da empresa",
  tipo_entidade: "Tipo de entidade",
  trl: "Nível de maturidade (TRL)",
  tamanho_empresa: "Porte da empresa",
  uf: "Estado (UF)",
  descricao_atividades: "Descrição das atividades",
};

export function ProfileIncompleteCard({
  missingFields,
}: {
  missingFields: string[];
}) {
  if (missingFields.length === 0) return null;

  return (
    <div className="rounded-xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 text-sm font-sans">
      <p className="font-medium text-content-primary mb-2">
        Para escrever uma proposta, complete estes campos no seu perfil:
      </p>
      <ul className="list-disc list-inside space-y-1 mb-3 text-content-secondary">
        {missingFields.map((f) => (
          <li key={f}>
            {MISSING_FIELD_LABELS[f] ?? f} — está em branco
          </li>
        ))}
      </ul>
      <Link
        href="/perfil"
        className="inline-block rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
      >
        Ir para perfil →
      </Link>
    </div>
  );
}
