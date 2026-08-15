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

  const nextField = MISSING_FIELD_LABELS[missingFields[0]];

  return (
    <div className="rounded-xl border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/30 px-4 py-3 text-sm font-sans">
      <p className="font-medium text-content-primary">
        Falta uma informação para continuar
      </p>
      <p className="mt-1 text-content-secondary">
        {nextField
          ? `No seu perfil, preencha: ${nextField}.`
          : "No seu perfil, preencha a próxima informação necessária."}
      </p>
      <Link
        href="/perfil"
        className="mt-3 inline-block rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white transition-opacity hover:opacity-90"
      >
        Completar no perfil
      </Link>
    </div>
  );
}
