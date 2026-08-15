"use client";

import { useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import type { CompanyProfile } from "@/types/profile";

export function ProfileGate({
  onReady,
}: {
  onReady: (profile: CompanyProfile) => void;
}) {
  const [nome, setNome] = useState("");
  const [descricao, setDescricao] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleSubmit() {
    const n = nome.trim();
    const d = descricao.trim();
    if (!n || !d) {
      setError("Preencha o nome e a descrição da sua empresa.");
      return;
    }
    const profile: CompanyProfile = {
      nome: n,
      descricao_atividades: d,
      url_site: url.trim(),
      cnpj: "",
      tipo_entidade: "",
      one_liner: "",
      solution_summary: "",
      portfolio_projetos: "",
      estilo_escrita: "",
      tamanho_empresa: "",
      capital_social: null,
      uf: "",
      faturamento_anual: null,
      ano_fundacao: null,
      trl: null,
      equipe_resumo: "",
      tipos_financiamento_interesse: [],
      estagio: "",
      mrr_arr: null,
      round_alvo_brl: null,
      cap_table_resumo: "",
      tracao_resumo: "",
    };
    onReady(profile);
  }

  return (
    <div className="mx-auto w-full max-w-xl">
      <div className="rounded-2xl border border-primary/30 bg-surface px-6 py-8 shadow-card">
        <h1 className="text-xl font-semibold text-content-primary">
          Radar de Editais
        </h1>
        <p className="mt-2 text-sm text-content-secondary leading-relaxed">
          Descubra editais, programas e ICTs que combinam com sua empresa.
          Para começar, conte um pouco sobre o negócio.
        </p>

        <div className="mt-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-content-primary mb-1">
              Nome da empresa <span className="text-destructive">*</span>
            </label>
            <input
              type="text"
              value={nome}
              onChange={(e) => setNome(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSubmit();
              }}
              placeholder="Ex.: TechSolutions Ltda"
              className={cn(
                "w-full rounded-lg border border-border bg-app-bg px-3 py-2.5 text-sm font-sans",
                "text-content-primary placeholder:text-content-secondary/60",
                "focus:outline-none focus:ring-2 focus:ring-primary/40",
              )}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-content-primary mb-1">
              O que sua empresa faz? <span className="text-destructive">*</span>
            </label>
            <textarea
              value={descricao}
              onChange={(e) => setDescricao(e.target.value)}
              rows={3}
              placeholder="Ex.: Desenvolvemos soluções de IA para diagnóstico médico por imagem, voltadas para hospitais públicos."
              className={cn(
                "w-full rounded-lg border border-border bg-app-bg px-3 py-2.5 text-sm font-sans resize-none",
                "text-content-primary placeholder:text-content-secondary/60",
                "focus:outline-none focus:ring-2 focus:ring-primary/40",
              )}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-content-primary mb-1">
              Site da empresa
            </label>
            <input
              type="url"
              inputMode="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSubmit();
              }}
              placeholder="suaempresa.com.br (opcional)"
              className={cn(
                "w-full rounded-lg border border-border bg-app-bg px-3 py-2.5 text-sm font-sans",
                "text-content-primary placeholder:text-content-secondary/60",
                "focus:outline-none focus:ring-2 focus:ring-primary/40",
              )}
            />
          </div>

          {error && (
            <p className="text-xs text-destructive">{error}</p>
          )}

          <button
            type="button"
            onClick={handleSubmit}
            disabled={!nome.trim() || !descricao.trim()}
            className={cn(
              "w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white transition-opacity",
              "hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            Continuar
          </button>
        </div>

        <div className="mt-6 pt-4 border-t border-border">
          <p className="text-xs text-content-secondary mb-2">
            Quer apenas explorar o que existe?
          </p>
          <Link
            href="/oportunidades"
            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            Ver oportunidades disponíveis →
          </Link>
        </div>
      </div>
    </div>
  );
}
