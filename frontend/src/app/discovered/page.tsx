"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import {
  getDiscoveredOpportunities,
  promoteDiscoveredOpportunity,
  rejectDiscoveredOpportunity,
  retryDiscoveredPromotion,
  updateDiscoveredEditalLink,
  type DiscoveredOpportunity,
  type RelevanceStatus,
  type RelevanceVerdict,
  ApiError,
} from "@/lib/api";

export default function DiscoveredPage() {
  const { getToken } = useAuth();
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [opportunities, setOpportunities] = useState<DiscoveredOpportunity[]>([]);
  const [actingOn, setActingOn] = useState<string | null>(null);
  const [editalInputs, setEditalInputs] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [expandedRelevance, setExpandedRelevance] = useState<Record<string, boolean>>({});
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  useEffect(() => {
    getToken().then((t) => setToken(t ?? null));
  }, [getToken]);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const res = await getDiscoveredOpportunities(token, filter === "all");
      setOpportunities(res.opportunities);
    } catch (e: unknown) {
      // Fila da Descoberta é do operador (ADMIN_EMAILS): 403 vira estado
      // amigável em vez de toast de erro (acesso direto por URL).
      if (e instanceof ApiError && e.status === 403) {
        setForbidden(true);
        return;
      }
      const msg = e instanceof ApiError
        ? e.message
        : e instanceof Error
          ? e.message
          : "Erro ao carregar";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }, [token, filter]);

  useEffect(() => {
    reload();
  }, [reload]);

  function badge(quality: string | null) {
    if (quality === "low") {
      return (
        <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans bg-red-100 dark:bg-red-950/40 text-red-700 dark:text-red-300">
          extração fraca
        </span>
      );
    }
    if (quality === "high") {
      return (
        <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300">
          extração rica
        </span>
      );
    }
    return null;
  }

  function statusBadge(status: string) {
    const colors: Record<string, string> = {
      pending: "bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
      promoted: "bg-blue-100 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300",
      rejected: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400",
    };
    return (
      <span className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans",
        colors[status] || "bg-gray-100 text-gray-500",
      )}>
        {status === "pending" ? "pendente" : status}
      </span>
    );
  }

  function relevanceBadge(status: RelevanceStatus | undefined | null, verdict: RelevanceVerdict | undefined | null) {
    const label = (() => {
      if (!status || status === "unclassified") return "não classificado";
      if (status === "error") return "erro de classificação";
      if (!verdict) return "classificado";
      if (verdict.decision === "in_scope") return "no escopo";
      if (verdict.decision === "out_of_scope") return "fora do escopo";
      if (verdict.decision === "needs_review") return "revisar";
      return "classificado";
    })();
    const color = (() => {
      if (!status || status === "unclassified") return "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400";
      if (status === "error") return "bg-red-100 dark:bg-red-950/40 text-red-700 dark:text-red-300";
      if (!verdict) return "bg-blue-100 dark:bg-blue-950/40 text-blue-700 dark:text-blue-300";
      if (verdict.decision === "in_scope") return "bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300";
      if (verdict.decision === "out_of_scope") return "bg-orange-100 dark:bg-orange-950/40 text-orange-700 dark:text-orange-300";
      if (verdict.decision === "needs_review") return "bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300";
      return "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400";
    })();
    return (
      <span className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans",
        color,
      )}>
        {label}
      </span>
    );
  }

  function relevanceDetails(opp: DiscoveredOpportunity) {
    if (!opp.relevance_status || opp.relevance_status === "unclassified") {
      return (
        <p className="text-xs text-content-secondary font-sans">
          Registro legado ou ainda não processado pelo classificador de relevância.
        </p>
      );
    }
    if (opp.relevance_status === "error") {
      return (
        <p className="text-xs text-red-600 dark:text-red-400 font-sans">
          {opp.relevance_error || "Erro de classificação"}
        </p>
      );
    }
    if (!opp.relevance_verdict) {
      return (
        <p className="text-xs text-content-secondary font-sans">
          Classificado, mas sem detalhes disponíveis.
        </p>
      );
    }
    const v = opp.relevance_verdict;
    return (
      <div className="space-y-2 text-xs font-sans">
        {v.reason_codes.length > 0 && (
          <div>
            <span className="font-medium text-content-primary">Reason codes: </span>
            <span className="text-content-secondary">{v.reason_codes.join(", ")}</span>
          </div>
        )}
        {v.exclusion_codes.length > 0 && (
          <div>
            <span className="font-medium text-content-primary">Exclusion codes: </span>
            <span className="text-content-secondary">{v.exclusion_codes.join(", ")}</span>
          </div>
        )}
        {v.missing_information.length > 0 && (
          <div>
            <span className="font-medium text-content-primary">Informação faltante: </span>
            <span className="text-content-secondary">{v.missing_information.join("; ")}</span>
          </div>
        )}
        {v.evidence.length > 0 && (
          <div className="space-y-1">
            <span className="font-medium text-content-primary">Evidências:</span>
            {v.evidence.map((ev, i) => (
              <div key={i} className="pl-2 border-l-2 border-border text-content-secondary">
                <p><span className="font-medium">{ev.code}</span>{ev.quote ? `: "${ev.quote}"` : ""}</p>
                <p>{ev.source && `Fonte: ${ev.source}`}{ev.locator?.document ? ` · ${ev.locator.document}` : ""}{ev.locator?.page ? ` · p. ${ev.locator.page}` : ""}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  async function handlePromote(opp: DiscoveredOpportunity) {
    if (!token) return;
    setActingOn(opp.id);
    try {
      const editalLink = editalInputs[opp.id]?.trim() || undefined;
      const res = await promoteDiscoveredOpportunity(opp.id, editalLink, token);
      const detail = res.edital_processed
        ? `PDF processado (${res.edital_processed.n_chars} chars, chunk enfileirado)`
        : "URL adicionada ao web_sources";
      toast.success(`Promovida: ${detail}`);
      await reload();
    } catch (e: unknown) {
      const msg = e instanceof ApiError
        ? `${e.message}${e.requestId ? ` (${e.requestId})` : ""}`
        : e instanceof Error
          ? e.message
          : "Erro ao promover";
      toast.error(msg);
    } finally {
      setActingOn(null);
    }
  }

  async function handleReject(opp: DiscoveredOpportunity) {
    if (!token) return;
    setActingOn(opp.id);
    try {
      await rejectDiscoveredOpportunity(opp.id, undefined, token);
      toast.success("Rejeitada");
      await reload();
    } catch (e: unknown) {
      const msg = e instanceof ApiError ? e.message : "Erro ao rejeitar";
      toast.error(msg);
    } finally {
      setActingOn(null);
    }
  }

  async function handleSaveEditalLink(opp: DiscoveredOpportunity) {
    if (!token) return;
    const link = editalInputs[opp.id]?.trim();
    if (!link) return;
    setActingOn(opp.id);
    try {
      await updateDiscoveredEditalLink(opp.id, link, token);
      toast.success("Link do edital salvo");
      await reload();
    } catch (e: unknown) {
      toast.error(e instanceof ApiError ? e.message : "Erro ao salvar");
    } finally {
      setActingOn(null);
    }
  }

  async function handleRetry(opp: DiscoveredOpportunity, stage: "fetch" | "silver" | "radar" | "rag") {
    if (!token) return;
    setActingOn(opp.id);
    try {
      await retryDiscoveredPromotion(opp.id, stage, token);
      toast.success(`Retry de ${stage} enfileirado`);
      await reload();
    } catch (e: unknown) {
      toast.error(e instanceof ApiError ? e.message : "Erro ao repetir etapa");
    } finally {
      setActingOn(null);
    }
  }

  if (forbidden) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-16 text-center">
        <h1 className="font-heading text-lg font-bold text-content-primary">
          Acesso restrito
        </h1>
        <p className="mt-2 text-sm text-content-secondary font-sans">
          A fila de descoberta é uma ferramenta de gestão do sistema, disponível
          apenas para o operador.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
      <div className="mb-6">
        <h1 className="font-heading text-xl font-bold text-content-primary">
          Descoberta de oportunidades
        </h1>
        <p className="mt-1 text-sm text-content-secondary font-sans">
          Oportunidades encontradas pela torneira web. Revise, cole o link do
          edital (PDF) e promova para entrar no sistema.
        </p>
      </div>

      {/* Filtros */}
      <div className="mb-4 flex items-center gap-2">
        <button
          onClick={() => setFilter("pending")}
          className={cn(
            "rounded-lg px-3 py-1.5 text-xs font-semibold font-sans transition-colors",
            filter === "pending"
              ? "bg-primary text-white"
              : "bg-surface text-content-secondary border border-border hover:bg-surface/80",
          )}
        >
          Pendentes
        </button>
        <button
          onClick={() => setFilter("all")}
          className={cn(
            "rounded-lg px-3 py-1.5 text-xs font-semibold font-sans transition-colors",
            filter === "all"
              ? "bg-primary text-white"
              : "bg-surface text-content-secondary border border-border hover:bg-surface/80",
          )}
        >
          Todas
        </button>
      </div>

      {loading ? (
        <div className="text-sm text-content-secondary font-sans py-8 text-center">
          Carregando…
        </div>
      ) : opportunities.length === 0 ? (
        <div className="rounded-xl border border-border bg-surface px-4 py-8 text-center text-sm text-content-secondary font-sans">
          Nenhuma oportunidade encontrada.
        </div>
      ) : (
        <ul className="space-y-3">
          {opportunities.map((opp) => (
            <li
              key={opp.id}
              className="rounded-xl border border-border bg-surface p-4 space-y-3"
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    {badge(opp.extraction_quality)}
                    {statusBadge(opp.status)}
                    {relevanceBadge(opp.relevance_status, opp.relevance_verdict)}
                    {opp.opportunity_type && (
                      <span className="text-[10px] text-content-secondary font-sans uppercase">
                        {opp.opportunity_type}
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-content-primary font-sans leading-snug">
                    {opp.title || "Sem título"}
                  </p>
                  <p className="text-xs text-content-secondary font-sans truncate">
                    {opp.agency && `${opp.agency} · `}{opp.fonte}
                  </p>
                </div>
              </div>

              {/* Descrição / URL */}
              {opp.descricao && (
                <p className="text-xs text-content-secondary font-sans line-clamp-2">
                  {opp.descricao}
                </p>
              )}
              <a
                href={opp.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block text-xs text-blue-600 dark:text-blue-400 font-sans truncate hover:underline"
              >
                {opp.url}
              </a>

              {/* Relevance details (progressive disclosure) */}
              {opp.relevance_status && (
                <div className="border border-border/70 rounded-lg overflow-hidden">
                  <button
                    onClick={() =>
                      setExpandedRelevance((prev) => ({
                        ...prev,
                        [opp.id]: !prev[opp.id],
                      }))
                    }
                    className="flex items-center justify-between w-full px-3 py-2 text-xs font-sans text-content-secondary hover:bg-surface/80 transition-colors"
                  >
                    <span>Classificação</span>
                    <span className="text-[10px]">{expandedRelevance[opp.id] ? "▲" : "▼"}</span>
                  </button>
                  {expandedRelevance[opp.id] && (
                    <div className="px-3 pb-2">
                      {relevanceDetails(opp)}
                    </div>
                  )}
                </div>
              )}

              {/* Campos extras (pending) */}
              {opp.status === "pending" && (
                <>
                  {/* Edital link */}
                  <div className="flex items-center gap-2">
                    <input
                      ref={(el) => { inputRefs.current[opp.id] = el; }}
                      type="text"
                      placeholder="Link do PDF do edital (opcional)"
                      value={editalInputs[opp.id] ?? opp.edital_link ?? ""}
                      onChange={(e) =>
                        setEditalInputs((prev) => ({
                          ...prev,
                          [opp.id]: e.target.value,
                        }))
                      }
                      className="flex-1 rounded-lg border border-border bg-surface/60 px-3 py-1.5 text-xs font-sans text-content-primary placeholder:text-content-secondary/50 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                    {(editalInputs[opp.id]?.trim() || opp.edital_link) && (
                      <button
                        onClick={() => handleSaveEditalLink(opp)}
                        disabled={actingOn === opp.id}
                        className="shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-semibold font-sans text-white bg-primary/70 hover:bg-primary transition-colors disabled:opacity-40"
                      >
                        Salvar
                      </button>
                    )}
                  </div>

                  {/* Ações */}
                  <div className="flex items-center gap-2 pt-1">
                    <button
                      onClick={() => handlePromote(opp)}
                      disabled={actingOn === opp.id}
                      className={cn(
                        "rounded-lg px-3 py-1.5 text-xs font-semibold font-sans transition-colors",
                        "text-white bg-primary hover:bg-primary-hover",
                        "disabled:opacity-40 disabled:cursor-not-allowed",
                      )}
                    >
                      {actingOn === opp.id ? "Processando…" : "Promover"}
                    </button>
                    <button
                      onClick={() => handleReject(opp)}
                      disabled={actingOn === opp.id}
                      className={cn(
                        "rounded-lg px-3 py-1.5 text-xs font-semibold font-sans transition-colors",
                        "text-content-secondary border border-border hover:bg-surface/80",
                        "disabled:opacity-40 disabled:cursor-not-allowed",
                      )}
                    >
                      Rejeitar
                    </button>
                  </div>
                </>
              )}

              {/* Metadata (reviewed) */}
              {(opp.status === "promoted" || opp.status === "rejected") && (
                <div className="text-[10px] text-content-secondary font-sans">
                  {opp.reviewed_at && `Revisado em ${new Date(opp.reviewed_at).toLocaleString("pt-BR")}`}
                  {opp.edital_link && ` · Link: ${opp.edital_link}`}
                </div>
              )}

              {opp.promotion_run && (
                <div className="rounded-lg border border-border/70 bg-surface/50 px-3 py-2 text-xs font-sans space-y-1">
                  <p className="font-medium text-content-primary">
                    Ingestão: {({
                      awaiting_fetch: "aguardando coleta",
                      processing: "processando",
                      ready: "disponível no Radar e RAG",
                      partial_failure: "falha parcial",
                      failed: "falhou",
                      queued: "na fila",
                    } as Record<string, string>)[opp.promotion_run.status] || opp.promotion_run.status}
                  </p>
                  <p className="text-content-secondary">
                    Radar: {opp.promotion_run.stages.radar_ready?.status === "ready" ? "disponível" : "pendente"}
                    {" · "}RAG: {opp.promotion_run.stages.rag_ready?.status === "ready" ? "disponível" : "pendente"}
                  </p>
                  {opp.promotion_run.status !== "ready" && opp.status === "promoted" && (
                    <div className="flex gap-2 pt-1">
                      {opp.promotion_run.route === "web_source" && (
                        <button onClick={() => handleRetry(opp, "fetch")} disabled={actingOn === opp.id} className="text-primary hover:underline disabled:opacity-40">Repetir coleta</button>
                      )}
                      <button onClick={() => handleRetry(opp, "radar")} disabled={actingOn === opp.id} className="text-primary hover:underline disabled:opacity-40">Repetir Radar</button>
                      <button onClick={() => handleRetry(opp, "rag")} disabled={actingOn === opp.id} className="text-primary hover:underline disabled:opacity-40">Repetir RAG</button>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
