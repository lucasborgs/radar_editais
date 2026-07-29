"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { DataTable, Modal, Skeleton } from "@/components/ui";
import type { Column } from "@/components/ui";
import { cn } from "@/lib/utils";
import {
  ApiError,
  getDataQualityException,
  getDataQualityExceptions,
  reviewDataQualityException,
  type DataQualityExceptionOut,
  type DataQualityEvidenceRef,
  type DataQualityExceptionState,
  type DataQualityIssueCode,
  type DataQualityReviewDecision,
} from "@/lib/api";

type StatusFilter = "all" | "open" | "resolved";

type ReviewFormState = {
  reviewId: string;
  decision: DataQualityReviewDecision;
  justification: string;
  correctedValue: string;
  selectedEvidenceKeys: string[];
};

const PAGE_LIMIT = 25;

const ISSUE_CODE_LABELS: Record<DataQualityIssueCode, string> = {
  fact_conflict: "Conflito de fato",
  critical_fact_missing: "Fato crítico ausente",
  validation_failed: "Validação reprovada",
  evidence_unresolved: "Evidência sem resolução",
  temporal_status_without_basis: "Status temporal sem base",
  temporal_status_conflict: "Conflito temporal",
};

const SUBJECT_KIND_LABELS: Record<DataQualityExceptionOut["subject_kind"], string> = {
  opportunity: "Oportunidade",
  investor: "Investidor",
  ict: "ICT",
  program: "Programa",
  agency: "Agência",
};

const STATE_LABELS: Record<DataQualityExceptionState, string> = {
  open: "Aberta",
  resolved: "Resolvida",
  superseded: "Substituída",
};

const STATE_CLASSES: Record<DataQualityExceptionState, string> = {
  open: "bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300",
  resolved: "bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300",
  superseded: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400",
};

const DECISION_LABELS: Record<DataQualityReviewDecision, string> = {
  confirm: "Confirmar",
  correct: "Corrigir",
  mark_unknown: "Marcar como desconhecido",
  confirm_continuous: "Confirmar fluxo contínuo",
};

const DECISION_HELP: Record<DataQualityReviewDecision, string> = {
  confirm: "Aceita o valor produzido e envia a evidência selecionada.",
  correct: "Registra um novo valor com base em evidência já vinculada.",
  mark_unknown: "Reconhece que a base atual não basta para afirmar o fato.",
  confirm_continuous: "Só vale quando a evidência vinculada contém um quote não vazio.",
};

function formatDateTime(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("pt-BR");
}

function evidenceKey(ref: DataQualityEvidenceRef) {
  return JSON.stringify(ref);
}

function evidenceLabel(ref: DataQualityEvidenceRef) {
  const parts: string[] = [];
  if (ref.source) parts.push(ref.source);
  if (ref.document) parts.push(ref.document);
  if (ref.page) parts.push(`p. ${ref.page}`);
  if (ref.section_path.length > 0) parts.push(ref.section_path.join(" / "));
  if (ref.block_idx !== null && ref.block_idx !== undefined) parts.push(`bloco ${ref.block_idx}`);
  return parts.length ? parts.join(" · ") : "Evidência vinculada";
}

function evidenceHashes(ref: DataQualityEvidenceRef) {
  const parts: string[] = [];
  if (ref.bundle_hash) parts.push(`bundle ${ref.bundle_hash}`);
  if (ref.content_hash) parts.push(`content ${ref.content_hash}`);
  if (ref.canonical_content_hash) parts.push(`canonical ${ref.canonical_content_hash}`);
  if (ref.silver_source_hash) parts.push(`silver ${ref.silver_source_hash}`);
  return parts;
}

function stateBadge(state: DataQualityExceptionState) {
  return cn(
    "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium font-sans",
    STATE_CLASSES[state],
  );
}

const LIST_COLUMNS: Column<DataQualityExceptionOut>[] = [
  {
    key: "id",
    header: "ID",
    render: (value) => (
      <span className="font-data text-xs text-content-secondary">{String(value)}</span>
    ),
  },
  {
    key: "subject_id",
    header: "Sujeito",
    render: (_value, row) => (
      <div className="space-y-0.5">
        <div className="text-xs font-medium text-content-primary">{SUBJECT_KIND_LABELS[row.subject_kind]}</div>
        <div className="text-[11px] text-content-secondary font-data break-all">{row.subject_id}</div>
      </div>
    ),
  },
  {
    key: "source",
    header: "Fonte / campo",
    render: (_value, row) => (
      <div className="space-y-0.5">
        <div className="text-xs text-content-primary">{row.source ?? "—"}</div>
        <div className="text-[11px] text-content-secondary">{row.field_path}</div>
      </div>
    ),
  },
  {
    key: "issue_code",
    header: "Motivo",
    render: (value, row) => (
      <div className="space-y-0.5">
        <div className="text-xs font-medium text-content-primary">{ISSUE_CODE_LABELS[value as DataQualityIssueCode]}</div>
        <div className="text-[11px] text-content-secondary">{row.impact}</div>
      </div>
    ),
  },
  {
    key: "safe_value",
    header: "Valor seguro",
    render: (value) => (
      <span className="text-xs text-content-primary break-all">{value ? String(value) : "—"}</span>
    ),
  },
  {
    key: "state",
    header: "Estado",
    render: (value) => (
      <span className={stateBadge(value as DataQualityExceptionState)}>
        {STATE_LABELS[value as DataQualityExceptionState]}
      </span>
    ),
  },
  {
    key: "current_review",
    header: "Revisão",
    render: (_value, row) => {
      const review = row.current_review;
      if (!review) {
        return <span className="text-xs text-content-secondary">Sem revisão</span>;
      }
      return (
        <div className="space-y-0.5">
          <div className="text-xs font-medium text-content-primary">{DECISION_LABELS[review.decision]}</div>
          <div className="text-[11px] text-content-secondary">{formatDateTime(review.reviewed_at)}</div>
        </div>
      );
    },
  },
];

export function AdminDataQualityExceptions({ token }: { token: string | null }) {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [codeFilter, setCodeFilter] = useState<DataQualityIssueCode | "">("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [fieldFilter, setFieldFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<DataQualityExceptionOut[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [selectedException, setSelectedException] = useState<DataQualityExceptionOut | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [form, setForm] = useState<ReviewFormState | null>(null);
  const [formExceptionId, setFormExceptionId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const listRequestId = useRef(0);
  const detailRequestId = useRef(0);
  const selectedExceptionId = selectedException?.id ?? null;

  useEffect(() => {
    if (!token) return;
    const requestId = ++listRequestId.current;
    setLoading(true);
    setLoadError(null);
    setForbidden(false);
    getDataQualityExceptions(token, {
      status: statusFilter === "all" ? undefined : statusFilter,
      code: codeFilter || undefined,
      source: sourceFilter.trim() || undefined,
      field: fieldFilter.trim() || undefined,
      limit: PAGE_LIMIT,
      offset,
    })
      .then((response) => {
        if (requestId !== listRequestId.current) return;
        setRows(response.items);
        setHasMore(response.has_more);
        setNextOffset(response.next_offset ?? null);
      })
      .catch((err: unknown) => {
        if (requestId !== listRequestId.current) return;
        if (err instanceof ApiError && err.status === 403) {
          setForbidden(true);
          setRows([]);
          setHasMore(false);
          setNextOffset(null);
          return;
        }
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Falha ao carregar a fila de exceções.";
        setLoadError(message);
        setRows([]);
        setHasMore(false);
        setNextOffset(null);
      })
      .finally(() => {
        if (requestId === listRequestId.current) {
          setLoading(false);
        }
      });
    return () => {
      listRequestId.current += 1;
    };
  }, [token, statusFilter, codeFilter, sourceFilter, fieldFilter, offset, refreshTick]);

  useEffect(() => {
    if (!detailOpen || !selectedExceptionId || !token) {
      return;
    }
    const requestId = ++detailRequestId.current;
    setDetailLoading(true);
    setDetailError(null);
    getDataQualityException(selectedExceptionId, token)
      .then((response) => {
        if (requestId !== detailRequestId.current) return;
        setSelectedException(response);
      })
      .catch((err: unknown) => {
        if (requestId !== detailRequestId.current) return;
        const message =
          err instanceof ApiError
            ? err.status === 404
              ? "Exceção de dados não encontrada."
              : err.message
            : err instanceof Error
              ? err.message
              : "Falha ao carregar os detalhes.";
        setDetailError(message);
      })
      .finally(() => {
        if (requestId === detailRequestId.current) {
          setDetailLoading(false);
        }
      });
    return () => {
      detailRequestId.current += 1;
    };
  }, [detailOpen, selectedExceptionId, token]);

  function openException(exception: DataQualityExceptionOut) {
    setSelectedException(exception);
    setDetailOpen(true);
    setDetailError(null);
    setSubmitError(null);
    setFormExceptionId(exception.id);
    if (exception.current_review) {
      setForm(null);
      return;
    }
    setForm({
      reviewId: crypto.randomUUID(),
      decision: "confirm",
      justification: "",
      correctedValue: "",
      selectedEvidenceKeys: [],
    });
  }

  function closeDetail() {
    setDetailOpen(false);
    setSelectedException(null);
    setDetailError(null);
    setSubmitError(null);
    setForm(null);
    setFormExceptionId(null);
    detailRequestId.current += 1;
  }

  function refreshList() {
    setRefreshTick((value) => value + 1);
  }

  const availableEvidence = useMemo(
    () => selectedException?.evidence_refs ?? [],
    [selectedException],
  );
  const selectedEvidence = useMemo(() => {
    if (!form) return [];
    const wanted = new Set(form.selectedEvidenceKeys);
    return availableEvidence.filter((ref) => wanted.has(evidenceKey(ref)));
  }, [availableEvidence, form]);

  const formValidationError = useMemo(() => {
    if (!form || !selectedException) return null;
    if (!form.justification.trim()) {
      return "A justificativa é obrigatória.";
    }
    if (form.decision !== "mark_unknown" && selectedEvidence.length === 0) {
      return "Selecione ao menos uma evidência já vinculada à exceção.";
    }
    if (form.decision === "correct" && !/^\d{4}-\d{2}-\d{2}$/.test(form.correctedValue.trim())) {
      return "Para corrigir, informe uma data válida no formato YYYY-MM-DD.";
    }
    if (form.decision === "confirm_continuous") {
      const invalid = selectedEvidence.filter((ref) => !ref.quote || !ref.quote.trim());
      if (invalid.length > 0) {
        return "confirm_continuous aceita apenas evidências vinculadas com quote não vazio.";
      }
    }
    return null;
  }, [form, selectedEvidence, selectedException]);

  async function submitReview() {
    if (!token || !selectedException || !form || formValidationError) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const response = await reviewDataQualityException(
        selectedException.id,
        {
          review_id: form.reviewId,
          decision: form.decision,
          justification: form.justification.trim(),
          corrected_value: form.decision === "correct" ? form.correctedValue.trim() : undefined,
          evidence_refs:
            form.decision === "mark_unknown"
              ? form.selectedEvidenceKeys.length === 0
                ? []
                : selectedEvidence
              : selectedEvidence,
        },
        token,
      );
      setSelectedException(response);
      refreshList();
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Falha ao registrar a revisão.";
      setSubmitError(message);
    } finally {
      setSubmitting(false);
    }
  }

  function updateSelectedEvidence(key: string, checked: boolean) {
    setForm((current) => {
      if (!current) return current;
      const next = new Set(current.selectedEvidenceKeys);
      if (checked) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return { ...current, selectedEvidenceKeys: Array.from(next) };
    });
  }

  const detailTitle = selectedException
    ? `${SUBJECT_KIND_LABELS[selectedException.subject_kind]} · ${selectedException.subject_id}`
    : "Exceção de dados";

  return (
    <section className="space-y-4">
      <div className="rounded-xl border border-border bg-surface p-5 shadow-card">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-1">
            <h2 className="font-heading text-lg font-bold text-content-primary">
              Exceções de dados
            </h2>
            <p className="text-sm text-content-secondary font-sans">
              Fila administrativa de revisão factual. As decisões vêm da API da T05;
              o frontend só organiza a navegação e a experiência do operador.
            </p>
          </div>
          <div className="text-xs text-content-secondary font-sans">
            {loading ? "Carregando…" : `${rows.length} item(ns) nesta página`}
          </div>
        </div>

        <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_1fr_1fr_1fr_auto]">
          <label className="space-y-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
              Estado
            </span>
            <div className="flex flex-wrap gap-2">
              {[
                { value: "open" as const, label: "Abertas" },
                { value: "resolved" as const, label: "Resolvidas" },
                { value: "all" as const, label: "Todas" },
              ].map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => {
                    setStatusFilter(option.value);
                    setOffset(0);
                  }}
                  className={cn(
                    "rounded-full px-3 py-1.5 text-xs font-medium font-sans transition-colors",
                    statusFilter === option.value
                      ? "bg-primary text-white"
                      : "bg-content-secondary/10 text-content-secondary hover:bg-content-secondary/20",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </label>

          <label className="space-y-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
              Código
            </span>
            <select
              value={codeFilter}
              onChange={(e) => {
                setCodeFilter(e.target.value as DataQualityIssueCode | "");
                setOffset(0);
              }}
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans text-content-primary outline-none transition-colors focus:border-primary"
            >
              <option value="">Todos</option>
              {Object.entries(ISSUE_CODE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <label className="space-y-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
              Fonte
            </span>
            <input
              value={sourceFilter}
              onChange={(e) => {
                setSourceFilter(e.target.value);
                setOffset(0);
              }}
              placeholder="finep, fapesp, ..."
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans text-content-primary placeholder:text-content-secondary/50 outline-none transition-colors focus:border-primary"
            />
          </label>

          <label className="space-y-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
              Campo
            </span>
            <input
              value={fieldFilter}
              onChange={(e) => {
                setFieldFilter(e.target.value);
                setOffset(0);
              }}
              placeholder="deadline, status, ..."
              className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans text-content-primary placeholder:text-content-secondary/50 outline-none transition-colors focus:border-primary"
            />
          </label>

          <div className="flex items-end gap-2">
            <button
              type="button"
              onClick={() => {
                setStatusFilter("open");
                setCodeFilter("");
                setSourceFilter("");
                setFieldFilter("");
                setOffset(0);
              }}
              className="rounded-lg border border-border px-3 py-2 text-xs font-semibold font-sans text-content-secondary transition-colors hover:bg-surface/80"
            >
              Limpar
            </button>
            <button
              type="button"
              onClick={() => refreshList()}
              className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold font-sans text-white transition-colors hover:bg-primary-hover"
            >
              Atualizar
            </button>
          </div>
        </div>
      </div>

      {forbidden ? (
        <div className="rounded-xl border border-border bg-surface px-4 py-8 text-center">
          <h3 className="font-heading text-base font-bold text-content-primary">
            Acesso restrito
          </h3>
          <p className="mt-2 text-sm text-content-secondary font-sans">
            Esta fila administrativa está disponível apenas para operadores autorizados.
          </p>
        </div>
      ) : loadError ? (
        <div className="rounded-xl border border-border bg-surface px-4 py-8 text-center">
          <h3 className="font-heading text-base font-bold text-content-primary">
            Não foi possível carregar as exceções
          </h3>
          <p className="mt-2 text-sm text-content-secondary font-sans">
            {loadError}
          </p>
          <button
            type="button"
            onClick={() => refreshList()}
            className="mt-4 rounded-lg bg-primary px-4 py-2 text-sm font-semibold font-sans text-white transition-colors hover:bg-primary-hover"
          >
            Tentar novamente
          </button>
        </div>
      ) : (
        <>
          <div className="rounded-xl border border-border bg-surface overflow-hidden">
            <DataTable
              data={rows}
              columns={LIST_COLUMNS}
              loading={loading}
              emptyMessage="Nenhuma exceção encontrada com os filtros atuais."
              onRowClick={openException}
            />
          </div>

          <div className="flex items-center justify-between gap-3 text-xs font-sans text-content-secondary">
            <div>
              {rows.length > 0 ? (
                <>
                  Mostrando {offset + 1}–{offset + rows.length}
                  {statusFilter !== "all"
                    ? ` · ${STATE_LABELS[statusFilter as DataQualityExceptionState].toLowerCase()}`
                    : ""}
                </>
              ) : (
                "Sem itens nesta página"
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={offset === 0 || loading}
                onClick={() => setOffset(Math.max(0, offset - PAGE_LIMIT))}
                className="rounded-lg border border-border px-3 py-1.5 font-semibold text-content-secondary transition-colors hover:bg-surface/80 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Anterior
              </button>
              <button
                type="button"
                disabled={!hasMore || loading}
                onClick={() => {
                  if (nextOffset !== null) {
                    setOffset(nextOffset);
                  }
                }}
                className="rounded-lg bg-primary px-3 py-1.5 font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                Próxima
              </button>
            </div>
          </div>
        </>
      )}

      <Modal
        open={detailOpen}
        onClose={closeDetail}
        title={detailTitle}
        size="lg"
        footer={
          form ? (
            <>
              <button
                type="button"
                onClick={closeDetail}
                className="rounded-lg border border-border px-4 py-2 text-sm font-semibold text-content-secondary transition-colors hover:bg-surface/80"
              >
                Fechar
              </button>
              <button
                type="submit"
                form="dq-review-form"
                disabled={submitting || !!formValidationError}
                className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40"
              >
                {submitting ? "Registrando…" : "Registrar revisão"}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={closeDetail}
              className="rounded-lg border border-border px-4 py-2 text-sm font-semibold text-content-secondary transition-colors hover:bg-surface/80"
            >
              Fechar
            </button>
          )
        }
      >
        {!selectedException ? (
          detailLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : detailError ? (
            <div className="space-y-2">
              <p className="text-sm text-content-secondary">{detailError}</p>
            </div>
          ) : null
        ) : (
          <div className="space-y-5">
            {detailError && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                {detailError}
              </div>
            )}

            <div className="grid gap-3 lg:grid-cols-2">
              <div className="rounded-xl border border-border bg-app-bg p-4">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
                  Sujeito
                </p>
                <p className="mt-1 text-sm font-medium text-content-primary">
                  {SUBJECT_KIND_LABELS[selectedException.subject_kind]}
                </p>
                <p className="mt-1 text-xs text-content-secondary font-data break-all">
                  {selectedException.subject_id}
                </p>
              </div>
              <div className="rounded-xl border border-border bg-app-bg p-4">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
                  Estado
                </p>
                <div className="mt-1 flex items-center gap-2">
                  <span className={stateBadge(selectedException.state)}>
                    {STATE_LABELS[selectedException.state]}
                  </span>
                  <span className="text-sm text-content-secondary">{selectedException.impact}</span>
                </div>
                <p className="mt-2 text-xs text-content-secondary">
                  Código: <span className="font-medium text-content-primary">{ISSUE_CODE_LABELS[selectedException.issue_code]}</span>
                </p>
              </div>
              <div className="rounded-xl border border-border bg-app-bg p-4">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
                  Fonte
                </p>
                <p className="mt-1 text-sm font-medium text-content-primary">
                  {selectedException.source ?? "—"}
                </p>
                <p className="mt-2 text-xs text-content-secondary">
                  Campo: <span className="font-medium text-content-primary">{selectedException.field_path}</span>
                </p>
              </div>
              <div className="rounded-xl border border-border bg-app-bg p-4">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
                  Valor seguro
                </p>
                <p className="mt-1 text-sm text-content-primary break-all">
                  {selectedException.safe_value ?? "—"}
                </p>
                <p className="mt-2 text-xs text-content-secondary">
                  {selectedException.detected_at ? `Detectado em ${formatDateTime(selectedException.detected_at)}` : "Data de detecção indisponível"}
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <div>
                <h3 className="text-sm font-semibold text-content-primary">Evidências vinculadas</h3>
                <p className="mt-1 text-xs text-content-secondary">
                  Apenas evidências já recebidas pela exceção podem ser selecionadas. Não há URL livre nem texto avulso.
                </p>
              </div>
              <div className="space-y-3">
                {availableEvidence.length === 0 ? (
                  <p className="text-sm text-content-secondary">Nenhuma evidência vinculada.</p>
                ) : (
                  availableEvidence.map((ref) => {
                    const key = evidenceKey(ref);
                    const checked = form ? form.selectedEvidenceKeys.includes(key) : false;
                    const disabled = !form || (form.decision === "confirm_continuous" && (!ref.quote || !ref.quote.trim()));
                    const hashes = evidenceHashes(ref);
                    return (
                      <label
                        key={key}
                        className={cn(
                          "block rounded-xl border p-4 transition-colors",
                          checked ? "border-primary bg-primary/5" : "border-border bg-white",
                          disabled && "opacity-70",
                        )}
                      >
                        <div className="flex items-start gap-3">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={disabled}
                            onChange={(e) => updateSelectedEvidence(key, e.target.checked)}
                            className="mt-1 h-4 w-4 rounded border-border text-primary focus:ring-primary"
                          />
                          <div className="min-w-0 flex-1 space-y-2">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-medium text-content-primary">
                                {evidenceLabel(ref)}
                              </span>
                              <span className="rounded-full bg-content-secondary/10 px-2 py-0.5 text-[10px] font-medium text-content-secondary">
                                {ref.locator_quality}
                              </span>
                            </div>
                            {ref.quote && (
                              <p className="rounded-lg border border-border/70 bg-app-bg px-3 py-2 text-sm text-content-primary">
                                {ref.quote}
                              </p>
                            )}
                            {!ref.quote && (
                              <p className="text-xs text-content-secondary">
                                Sem quote disponível.
                              </p>
                            )}
                            <div className="grid gap-1 text-[11px] text-content-secondary lg:grid-cols-2">
                              {ref.document && <span>Documento: {ref.document}</span>}
                              {ref.page && <span>Página: {ref.page}</span>}
                              {ref.block_idx !== null && ref.block_idx !== undefined && <span>Bloco: {ref.block_idx}</span>}
                              {ref.section_path.length > 0 && <span>Seção: {ref.section_path.join(" / ")}</span>}
                              {ref.native_id && <span>Native ID: {ref.native_id}</span>}
                              {ref.edital_id && <span>Edital: {ref.edital_id}</span>}
                              {ref.collected_at && <span>Coletado em: {formatDateTime(ref.collected_at)}</span>}
                            </div>
                            {hashes.length > 0 && (
                              <p className="font-data text-[10px] text-content-secondary break-all">
                                {hashes.join(" · ")}
                              </p>
                            )}
                          </div>
                        </div>
                      </label>
                    );
                  })
                )}
              </div>
            </div>

            <div className="rounded-xl border border-border bg-app-bg p-4">
              <h3 className="text-sm font-semibold text-content-primary">Revisão corrente</h3>
              {selectedException.current_review ? (
                <div className="mt-3 space-y-2 text-sm">
                  <p className="text-content-primary">
                    Decisão: <span className="font-medium">{DECISION_LABELS[selectedException.current_review.decision]}</span>
                  </p>
                  <p className="text-content-secondary">
                    Registrada em {formatDateTime(selectedException.current_review.reviewed_at)}
                  </p>
                  <p className="text-content-secondary">
                    Evidências: {selectedException.current_review.evidence_refs.length}
                  </p>
                </div>
              ) : (
                <p className="mt-3 text-sm text-content-secondary">
                  Ainda não há revisão corrente para esta exceção.
                </p>
              )}
            </div>

            {form && formExceptionId === selectedException.id && (
              <form
                id="dq-review-form"
                className="space-y-4 rounded-xl border border-border bg-white p-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submitReview();
                }}
              >
                <div className="grid gap-4 lg:grid-cols-2">
                  <label className="space-y-1">
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
                      Decisão
                    </span>
                    <select
                      value={form.decision}
                      onChange={(e) => {
                        const decision = e.target.value as DataQualityReviewDecision;
                        setForm((current) => {
                          if (!current) return current;
                          const next = { ...current, decision };
                          if (decision !== "correct") {
                            next.correctedValue = "";
                          }
                          return next;
                        });
                      }}
                      className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans text-content-primary outline-none transition-colors focus:border-primary"
                    >
                      {Object.entries(DECISION_LABELS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {value} · {label}
                        </option>
                      ))}
                    </select>
                    <p className="text-[11px] text-content-secondary">
                      {DECISION_HELP[form.decision]}
                    </p>
                  </label>

                  <label className="space-y-1">
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
                      Justificativa
                    </span>
                    <textarea
                      value={form.justification}
                      onChange={(e) =>
                        setForm((current) =>
                          current ? { ...current, justification: e.target.value } : current,
                        )
                      }
                      rows={4}
                      maxLength={2000}
                      className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans text-content-primary placeholder:text-content-secondary/50 outline-none transition-colors focus:border-primary"
                      placeholder="Descreva a base da decisão"
                    />
                    <div className="text-[11px] text-content-secondary">
                      {form.justification.length}/2000
                    </div>
                  </label>
                </div>

                {form.decision === "correct" && (
                  <label className="space-y-1">
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-content-secondary">
                      Valor corrigido
                    </span>
                    <input
                      type="date"
                      value={form.correctedValue}
                      onChange={(e) =>
                        setForm((current) =>
                          current ? { ...current, correctedValue: e.target.value } : current,
                        )
                      }
                      className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm font-sans text-content-primary outline-none transition-colors focus:border-primary"
                    />
                  </label>
                )}

                {submitError && (
                  <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
                    {submitError}
                  </div>
                )}

                {formValidationError && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                    {formValidationError}
                  </div>
                )}

                <div className="rounded-lg border border-border/70 bg-app-bg px-3 py-2 text-[11px] text-content-secondary">
                  Submissão atual: <span className="font-data">{form.reviewId}</span>
                </div>
              </form>
            )}

            {!form && selectedException.current_review && (
              <div className="rounded-xl border border-border bg-white p-4 text-sm text-content-secondary">
                Esta exceção já possui uma revisão corrente. O formulário fica
                indisponível para evitar sobrescrita sem repetir a mesma
                submissão original.
              </div>
            )}
          </div>
        )}
      </Modal>
    </section>
  );
}
