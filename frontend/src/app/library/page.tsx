"use client";

// Página "Arquivos" — gerenciador mínimo de biblioteca (spec_frontend_chat_first
// Fase 4). Substitui a antiga /library rica (996 linhas) pelo padrão de apps de
// chat: o upload "de verdade" acontece no composer de escrita (📎,
// AttachToLibrary); aqui é só um gerente enxuto — listar, visualizar, upload,
// excluir, arquivar e restaurar.
//
// O QUE SAIU vs. a página antiga: filtros por tipo (tabs), busca, edição de
// conteúdo, enriquecimento de perfil a partir do item, badges de tema/score.
// O QUE FICOU: lista (nome/tipo/data/status de enrich), visualização (modal com
// summary/key_facts/conteúdo), upload, excluir, arquivar — e restaurar (mantido
// porque arquivar sem restaurar seria irrecuperável pela UI).

import { useState, useEffect, useRef } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import {
  getLibraryItems,
  getLibraryItem,
  uploadLibraryPdf,
  deleteLibraryItem,
  archiveLibraryItem,
  unarchiveLibraryItem,
} from "@/lib/api";
import type {
  ContentItemSummary,
  ContentItemType,
  ContentItemFull,
  EnrichmentStatus,
} from "@/types/api";
import { Modal, Skeleton } from "@/components/ui";
import { DashboardLayout } from "@/components/layout/DashboardLayout";

// ── Constantes de rótulo ────────────────────────────────────────────────────

const TYPE_LABELS: Record<ContentItemType, string> = {
  proposal: "Proposta",
  project_description: "Projeto",
  team_bio: "Equipe",
  technical_doc: "Doc técnico",
  other: "Outro",
};

const ENRICHMENT_LABEL: Record<EnrichmentStatus, string> = {
  pending: "enriquecendo…",
  processing: "enriquecendo…",
  done: "pronto",
  failed: "falhou",
};

const ENRICHMENT_COLOR: Record<EnrichmentStatus, string> = {
  pending: "bg-amber-50 text-amber-700 border border-amber-200",
  processing: "bg-amber-50 text-amber-700 border border-amber-200",
  done: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  failed: "bg-red-50 text-red-700 border border-red-200",
};

const INPUT_CLS = cn(
  "w-full rounded-lg border border-border px-3 py-2 text-sm font-sans",
  "text-content-primary placeholder:text-content-secondary",
  "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
  "transition-colors bg-white"
);

const ACCEPT = ".pdf,.docx,.txt,.md";

// ── Modal de upload ──────────────────────────────────────────────────────────

function UploadModal({
  token,
  onClose,
  onSaved,
}: {
  token: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<ContentItemType>("technical_doc");
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleSave() {
    if (!file) {
      setError("Selecione um arquivo");
      return;
    }
    const finalTitle = title.trim() || file.name.replace(/\.[^.]+$/, "");
    setSaving(true);
    setError(null);
    try {
      await uploadLibraryPdf(file, { title: finalTitle, type, tags: "" }, token);
      toast.success("Arquivo enviado — enriquecendo em segundo plano.");
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Erro ao enviar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Enviar arquivo"
      footer={
        <>
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-sans text-content-secondary hover:text-content-primary transition-colors"
          >
            Cancelar
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className={cn(
              "px-5 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors",
              "disabled:opacity-40 disabled:cursor-not-allowed"
            )}
          >
            {saving ? "Enviando…" : "Enviar"}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="block text-xs font-medium text-content-secondary font-sans mb-1">
            Arquivo
          </label>
          <div
            onClick={() => fileRef.current?.click()}
            className={cn(
              "border-2 border-dashed border-border rounded-lg p-6 text-center cursor-pointer",
              "hover:border-primary/40 transition-colors",
              file && "border-primary/40 bg-primary/5"
            )}
          >
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setFile(f);
                if (f && !title.trim()) setTitle(f.name.replace(/\.[^.]+$/, ""));
              }}
            />
            {file ? (
              <p className="text-sm font-sans text-content-primary">{file.name}</p>
            ) : (
              <p className="text-sm font-sans text-content-secondary">
                Clique para selecionar — PDF, DOCX, TXT, MD (máx. 10MB)
              </p>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-content-secondary font-sans mb-1">
              Título
            </label>
            <input
              className={INPUT_CLS}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Derivado do nome do arquivo"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-content-secondary font-sans mb-1">
              Tipo
            </label>
            <select
              className={INPUT_CLS}
              value={type}
              onChange={(e) => setType(e.target.value as ContentItemType)}
            >
              {Object.entries(TYPE_LABELS).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </div>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
            {error}
          </div>
        )}
      </div>
    </Modal>
  );
}

// ── Modal de visualização ────────────────────────────────────────────────────

function ViewModal({
  itemId,
  token,
  onClose,
}: {
  itemId: string;
  token: string;
  onClose: () => void;
}) {
  const [item, setItem] = useState<ContentItemFull | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLibraryItem(itemId, token)
      .then((data) => {
        if (!cancelled) setItem(data);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Erro ao carregar");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [itemId, token]);

  return (
    <Modal open onClose={onClose} title={item?.title ?? "Arquivo"} size="lg">
      {loading ? (
        <div className="space-y-3">
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : error ? (
        <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
          {error}
        </div>
      ) : item ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full font-sans bg-gray-100 text-content-secondary">
              {TYPE_LABELS[item.type]}
            </span>
            {item.tags.map((t) => (
              <span
                key={t}
                className="text-[10px] bg-gray-100 text-content-secondary px-1.5 py-0.5 rounded-full font-sans"
              >
                {t}
              </span>
            ))}
          </div>

          {item.summary && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-content-secondary font-sans mb-1">
                Resumo
              </p>
              <p className="text-sm text-content-primary font-sans leading-relaxed">
                {item.summary}
              </p>
            </div>
          )}

          {item.key_facts.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wide text-content-secondary font-sans mb-1">
                Fatos-chave
              </p>
              <ul className="list-disc list-inside space-y-1">
                {item.key_facts.map((f, i) => (
                  <li key={i} className="text-sm text-content-primary font-sans">
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <p className="text-[10px] uppercase tracking-wide text-content-secondary font-sans mb-1">
              Conteúdo
            </p>
            <pre className="whitespace-pre-wrap font-sans text-xs text-content-primary bg-app-bg rounded-lg border border-border p-3 max-h-72 overflow-y-auto">
              {item.content}
            </pre>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

// ── Menu de ações da linha ───────────────────────────────────────────────────

function RowActions({
  archived,
  onArchive,
  onUnarchive,
  onDelete,
}: {
  archived: boolean;
  onArchive: () => void;
  onUnarchive: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    window.addEventListener("mousedown", onPointer);
    return () => window.removeEventListener("mousedown", onPointer);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label="Ações"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className={cn(
          "w-7 h-7 rounded-md flex items-center justify-center text-content-secondary",
          "hover:bg-gray-100 hover:text-content-primary transition-colors",
          open && "bg-gray-100 text-content-primary"
        )}
      >
        <svg viewBox="0 0 20 20" width="14" height="14" fill="currentColor" aria-hidden>
          <circle cx="4" cy="10" r="1.6" />
          <circle cx="10" cy="10" r="1.6" />
          <circle cx="16" cy="10" r="1.6" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-30 w-40 rounded-lg border border-border bg-white shadow-lg py-1">
          {archived ? (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onUnarchive();
              }}
              className="w-full text-left px-3 py-1.5 text-xs font-sans text-content-primary hover:bg-gray-50"
            >
              Restaurar
            </button>
          ) : (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onArchive();
              }}
              className="w-full text-left px-3 py-1.5 text-xs font-sans text-content-primary hover:bg-gray-50"
            >
              Arquivar
            </button>
          )}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              onDelete();
            }}
            className="w-full text-left px-3 py-1.5 text-xs font-sans text-red-600 hover:bg-red-50"
          >
            Excluir
          </button>
        </div>
      )}
    </div>
  );
}

// ── Página ───────────────────────────────────────────────────────────────────

export default function FilesPage() {
  const { getToken } = useAuth();
  const [token, setToken] = useState<string | null>(null);
  const [items, setItems] = useState<ContentItemSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [viewingId, setViewingId] = useState<string | null>(null);

  useEffect(() => {
    getToken().then((t) => {
      setToken(t);
      if (t) loadItems(t);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadItems(t?: string) {
    const tk = t ?? token;
    if (!tk) return;
    setLoading(true);
    try {
      const data = await getLibraryItems(tk, undefined, undefined, includeArchived);
      setItems(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (token) loadItems();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeArchived]);

  // Polling enquanto algum item estiver enriquecendo, para refletir pending → done.
  const hasPending = items.some(
    (i) => i.enrichment_status === "pending" || i.enrichment_status === "processing"
  );
  useEffect(() => {
    if (!hasPending || !token) return;
    const id = setInterval(() => loadItems(), 4000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPending, token]);

  async function handleDelete(id: string) {
    if (!token) return;
    setItems((prev) => prev.filter((i) => i.id !== id));
    try {
      await deleteLibraryItem(id, token);
      toast.success("Arquivo excluído");
    } catch {
      toast.error("Erro ao excluir — recarregando…");
      loadItems();
    }
  }

  async function handleArchive(id: string) {
    if (!token) return;
    if (!includeArchived) setItems((prev) => prev.filter((i) => i.id !== id));
    try {
      await archiveLibraryItem(id, token);
      toast.success("Arquivo arquivado");
      if (includeArchived) loadItems();
    } catch {
      toast.error("Erro ao arquivar — recarregando…");
      loadItems();
    }
  }

  async function handleUnarchive(id: string) {
    if (!token) return;
    try {
      await unarchiveLibraryItem(id, token);
      toast.success("Arquivo restaurado");
      loadItems();
    } catch {
      toast.error("Erro ao restaurar — recarregando…");
      loadItems();
    }
  }

  return (
    <DashboardLayout>
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="font-heading text-xl font-bold text-content-primary">Arquivos</h1>
            <p className="text-sm text-content-secondary font-sans mt-0.5">
              Documentos disponíveis para mencionar (@) na escrita das suas propostas
            </p>
          </div>
          <button
            onClick={() => setShowUpload(true)}
            className="shrink-0 px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors"
          >
            + Enviar
          </button>
        </div>

        {/* Toggle de arquivados */}
        <div className="flex items-center justify-end">
          <label className="text-xs font-sans text-content-secondary flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
              className="h-3.5 w-3.5 accent-primary"
            />
            Ver arquivados
          </label>
        </div>

        {/* Lista */}
        {loading ? (
          <div className="space-y-2">
            {[1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-14 w-full rounded-xl" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-16 space-y-3">
            <p className="text-4xl">📂</p>
            <p className="text-sm font-sans text-content-secondary">
              Nenhum arquivo ainda.
              <br />
              Envie propostas ou documentos para mencioná-los na escrita.
            </p>
            <button
              onClick={() => setShowUpload(true)}
              className="mt-2 px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors"
            >
              Enviar primeiro arquivo
            </button>
          </div>
        ) : (
          <div className="rounded-xl border border-border bg-white divide-y divide-border overflow-hidden">
            {items.map((item) => {
              const status = item.enrichment_status;
              const isArchived = !!item.archived_at;
              return (
                <div
                  key={item.id}
                  onClick={() => setViewingId(item.id)}
                  className={cn(
                    "group flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-app-bg transition-colors",
                    isArchived && "opacity-60"
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-content-primary font-sans truncate">
                      {item.title}
                    </p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-[11px] text-content-secondary font-sans">
                        {TYPE_LABELS[item.type]}
                      </span>
                      <span className="text-[11px] text-content-secondary font-sans">·</span>
                      <span className="text-[11px] text-content-secondary font-sans">
                        {new Date(item.created_at).toLocaleDateString("pt-BR")}
                      </span>
                      {isArchived && (
                        <>
                          <span className="text-[11px] text-content-secondary font-sans">·</span>
                          <span className="text-[11px] text-content-secondary font-sans italic">
                            arquivado
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  {status && (
                    <span
                      className={cn(
                        "text-[10px] font-medium px-2 py-0.5 rounded-full font-sans inline-flex items-center gap-1 shrink-0",
                        ENRICHMENT_COLOR[status]
                      )}
                      title={`enrichment_status: ${status}`}
                    >
                      {(status === "pending" || status === "processing") && (
                        <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
                      )}
                      {ENRICHMENT_LABEL[status]}
                    </span>
                  )}

                  <div
                    onClick={(e) => e.stopPropagation()}
                    className="shrink-0"
                  >
                    <RowActions
                      archived={isArchived}
                      onArchive={() => handleArchive(item.id)}
                      onUnarchive={() => handleUnarchive(item.id)}
                      onDelete={() => handleDelete(item.id)}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {showUpload && token && (
        <UploadModal
          token={token}
          onClose={() => setShowUpload(false)}
          onSaved={() => loadItems()}
        />
      )}

      {viewingId && token && (
        <ViewModal itemId={viewingId} token={token} onClose={() => setViewingId(null)} />
      )}
    </DashboardLayout>
  );
}
