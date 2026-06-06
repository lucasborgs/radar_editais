"use client";

import { useState, useEffect, useRef } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import {
  getLibraryItems,
  createLibraryItem,
  uploadLibraryPdf,
  deleteLibraryItem,
  archiveLibraryItem,
  getLibraryItem,
  updateLibraryItem,
  extractProfileFromLibraryItem,
  getMe,
  saveProfile,
} from "@/lib/api";
import type { ContentItemSummary, ContentItemType, ContentItemFull, EnrichmentStatus } from "@/types/api";
import { EMPTY_PROFILE, type CompanyProfile } from "@/types/profile";
import { Modal } from "@/components/ui";
import { DashboardLayout } from "@/components/layout/DashboardLayout";

// Rótulos amigáveis dos campos do perfil (para o diff/merge de enriquecimento).
const PROFILE_FIELD_LABELS: Record<keyof CompanyProfile, string> = {
  nome: "Nome da empresa",
  cnpj: "CNPJ",
  url_site: "Site",
  tipo_entidade: "Tipo de entidade",
  one_liner: "Proposta de valor",
  solution_summary: "Solução / tecnologia",
  descricao_atividades: "Descrição das atividades",
  portfolio_projetos: "Portfólio de projetos",
  tamanho_empresa: "Porte",
  capital_social: "Capital social",
  uf: "UF",
  faturamento_anual: "Faturamento anual",
  ano_fundacao: "Ano de fundação",
  trl: "TRL",
  equipe_resumo: "Equipe",
  tipos_financiamento_interesse: "Financiamento de interesse",
};

// Renderiza um valor de campo do perfil em texto legível (lida com listas/null).
function formatProfileValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

function isEmptyValue(v: unknown): boolean {
  if (v === null || v === undefined || v === "") return true;
  if (Array.isArray(v)) return v.length === 0;
  return false;
}

// ── Constants ─────────────────────────────────────────────────────────────────

const TABS: { value: ContentItemType | "all"; label: string }[] = [
  { value: "all",                  label: "Todos" },
  { value: "proposal",             label: "Propostas" },
  { value: "project_description",  label: "Projetos" },
  { value: "team_bio",             label: "Equipe" },
  { value: "technical_doc",        label: "Docs técnicos" },
  { value: "other",                label: "Outros" },
];

const TYPE_LABELS: Record<ContentItemType, string> = {
  proposal:            "Proposta",
  project_description: "Projeto",
  team_bio:            "Equipe",
  technical_doc:       "Doc técnico",
  other:               "Outro",
};

const TYPE_COLORS: Record<ContentItemType, string> = {
  proposal:            "bg-blue-100 text-blue-700",
  project_description: "bg-green-100 text-green-700",
  team_bio:            "bg-purple-100 text-purple-700",
  technical_doc:       "bg-amber-100 text-amber-700",
  other:               "bg-gray-100 text-gray-600",
};

const ENRICHMENT_LABEL: Record<EnrichmentStatus, string> = {
  pending:    "enriquecendo…",
  processing: "enriquecendo…",
  done:       "pronto",
  failed:     "falhou",
};

const ENRICHMENT_COLOR: Record<EnrichmentStatus, string> = {
  pending:    "bg-amber-50 text-amber-700 border border-amber-200",
  processing: "bg-amber-50 text-amber-700 border border-amber-200",
  done:       "bg-emerald-50 text-emerald-700 border border-emerald-200",
  failed:     "bg-red-50 text-red-700 border border-red-200",
};

const INPUT_CLS = cn(
  "w-full rounded-lg border border-border px-3 py-2 text-sm font-sans",
  "text-content-primary placeholder:text-content-secondary",
  "focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary",
  "transition-colors bg-white"
);

// ── Add Item Modal ─────────────────────────────────────────────────────────────

function AddModal({
  onClose,
  onSaved,
  token,
}: {
  onClose: () => void;
  onSaved: () => void;
  token: string;
}) {
  const [mode, setMode] = useState<"text" | "pdf">("text");
  const [title, setTitle] = useState("");
  const [type, setType] = useState<ContentItemType>("proposal");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleSave() {
    if (!title.trim()) { setError("Título obrigatório"); return; }
    if (mode === "text" && !content.trim()) { setError("Conteúdo obrigatório"); return; }
    if (mode === "pdf" && !file) { setError("Selecione um arquivo"); return; }

    setSaving(true);
    setError(null);
    try {
      if (mode === "text") {
        await createLibraryItem(
          { title, type, content, tags: tags.split(",").map(t => t.trim()).filter(Boolean) },
          token
        );
      } else {
        await uploadLibraryPdf(file!, { title, type, tags }, token);
      }
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Erro ao salvar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div className="w-full max-w-lg bg-white rounded-2xl border border-border shadow-xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-base font-semibold font-sans text-content-primary">
            Adicionar à biblioteca
          </h2>
          <button onClick={onClose} className="text-content-secondary hover:text-content-primary text-lg leading-none">×</button>
        </div>

        <div className="p-6 space-y-4">
          {/* Mode toggle */}
          <div className="flex gap-1 bg-gray-100 p-1 rounded-lg">
            {(["text", "pdf"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={cn(
                  "flex-1 py-1.5 rounded-md text-sm font-sans font-medium transition-colors",
                  mode === m ? "bg-white text-content-primary shadow-sm" : "text-content-secondary"
                )}
              >
                {m === "text" ? "Colar texto" : "Upload arquivo"}
              </button>
            ))}
          </div>

          <div>
            <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Título</label>
            <input className={INPUT_CLS} value={title} onChange={e => setTitle(e.target.value)} placeholder="Ex: Proposta FINEP Bioeconomia 2024" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Tipo</label>
              <select className={INPUT_CLS} value={type} onChange={e => setType(e.target.value as ContentItemType)}>
                {Object.entries(TYPE_LABELS).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Tags (vírgula)</label>
              <input className={INPUT_CLS} value={tags} onChange={e => setTags(e.target.value)} placeholder="bioeconomia, P&D" />
            </div>
          </div>

          {mode === "text" ? (
            <div>
              <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Conteúdo</label>
              <textarea
                rows={6}
                className={cn(INPUT_CLS, "resize-none")}
                value={content}
                onChange={e => setContent(e.target.value)}
                placeholder="Cole aqui o texto da proposta, descrição do projeto ou documento..."
              />
            </div>
          ) : (
            <div>
              <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Arquivo</label>
              <div
                onClick={() => fileRef.current?.click()}
                className={cn(
                  "border-2 border-dashed border-border rounded-lg p-6 text-center cursor-pointer",
                  "hover:border-primary/40 transition-colors",
                  file && "border-primary/40 bg-primary/5"
                )}
              >
                <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" className="hidden" onChange={e => setFile(e.target.files?.[0] ?? null)} />
                {file ? (
                  <p className="text-sm font-sans text-content-primary">{file.name}</p>
                ) : (
                  <p className="text-sm font-sans text-content-secondary">Clique para selecionar — PDF, DOCX, TXT, MD (máx. 10MB)</p>
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">{error}</div>
          )}
        </div>

        <div className="flex justify-end gap-3 px-6 py-4 border-t border-border">
          <button onClick={onClose} className="px-4 py-2 text-sm font-sans text-content-secondary hover:text-content-primary transition-colors">
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
            {saving ? (
              <span className="flex items-center gap-2">
                <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Processando...
              </span>
            ) : "Salvar"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Item Card ─────────────────────────────────────────────────────────────────

function ItemActionMenu({
  onEdit,
  onArchive,
  onDelete,
}: {
  onEdit: () => void;
  onArchive: () => void;
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
          "opacity-0 group-hover:opacity-100",
          open && "opacity-100 bg-gray-100"
        )}
      >
        <svg viewBox="0 0 20 20" width="14" height="14" fill="currentColor">
          <circle cx="4" cy="10" r="1.6" />
          <circle cx="10" cy="10" r="1.6" />
          <circle cx="16" cy="10" r="1.6" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-8 z-30 w-40 rounded-lg border border-border bg-white shadow-lg py-1">
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onEdit();
            }}
            className="w-full text-left px-3 py-1.5 text-xs font-sans text-content-primary hover:bg-gray-50"
          >
            Editar
          </button>
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              onArchive();
            }}
            className="w-full text-left px-3 py-1.5 text-xs font-sans text-content-primary hover:bg-gray-50"
          >
            Arquivar
          </button>
          <button
            type="button"
            onClick={() => {
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

function ItemCard({
  item,
  onEdit,
  onDelete,
  onArchive,
  onEnrich,
}: {
  item: ContentItemSummary;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
  onArchive: (id: string) => void;
  onEnrich: (id: string) => void;
}) {
  const status = item.enrichment_status;
  return (
    <div className="bg-white rounded-xl border border-border p-4 space-y-2 hover:border-primary/30 transition-colors group">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={cn("text-[10px] font-semibold px-2 py-0.5 rounded-full font-sans", TYPE_COLORS[item.type])}>
            {TYPE_LABELS[item.type]}
          </span>
          {status && (
            <span
              className={cn(
                "text-[10px] font-medium px-2 py-0.5 rounded-full font-sans inline-flex items-center gap-1",
                ENRICHMENT_COLOR[status],
              )}
              title={`enrichment_status: ${status}`}
            >
              {(status === "pending" || status === "processing") && (
                <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
              )}
              {ENRICHMENT_LABEL[status]}
            </span>
          )}
          {item.tags.slice(0, 3).map(tag => (
            <span key={tag} className="text-[10px] bg-gray-100 text-content-secondary px-1.5 py-0.5 rounded-full font-sans">
              {tag}
            </span>
          ))}
        </div>
        <ItemActionMenu
          onEdit={() => onEdit(item.id)}
          onArchive={() => onArchive(item.id)}
          onDelete={() => onDelete(item.id)}
        />
      </div>

      <p className="text-sm font-semibold text-content-primary font-sans leading-snug">{item.title}</p>

      {item.summary && (
        <p className="text-xs text-content-secondary font-sans leading-relaxed line-clamp-2">{item.summary}</p>
      )}

      {item.themes.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {item.themes.slice(0, 4).map(t => (
            <span key={t} className="text-[10px] text-primary/70 font-sans">#{t}</span>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between gap-2 pt-1">
        <p className="text-[10px] text-content-secondary font-sans">
          {new Date(item.created_at).toLocaleDateString("pt-BR")}
        </p>
        {item.type === "proposal" && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onEnrich(item.id);
            }}
            className="text-[11px] font-medium font-sans text-primary hover:text-primary-hover transition-colors"
          >
            ↑ Enriquecer meu perfil
          </button>
        )}
      </div>
    </div>
  );
}

// ── Edit Item Modal ────────────────────────────────────────────────────────────

function EditModal({
  itemId,
  onClose,
  onSaved,
  token,
}: {
  itemId: string;
  onClose: () => void;
  onSaved: () => void;
  token: string;
}) {
  const [item, setItem] = useState<ContentItemFull | null>(null);
  const [title, setTitle] = useState("");
  const [type, setType] = useState<ContentItemType>("proposal");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLibraryItem(itemId, token)
      .then((data) => {
        if (cancelled) return;
        setItem(data);
        setTitle(data.title);
        setType(data.type);
        setContent(data.content);
        setOriginalContent(data.content);
        setTags(data.tags.join(", "));
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Erro ao carregar item");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [itemId, token]);

  const contentChanged = content !== originalContent;

  async function handleSave() {
    if (!title.trim()) { setError("Título obrigatório"); return; }
    if (!content.trim()) { setError("Conteúdo obrigatório"); return; }
    setSaving(true);
    setError(null);
    try {
      const updates: Partial<{ title: string; type: string; content: string; tags: string[] }> = {
        title,
        type,
        tags: tags.split(",").map(t => t.trim()).filter(Boolean),
      };
      if (contentChanged) updates.content = content;
      await updateLibraryItem(itemId, updates, token);
      onSaved();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Erro ao salvar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div className="w-full max-w-2xl bg-white rounded-2xl border border-border shadow-xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-base font-semibold font-sans text-content-primary">Editar item</h2>
          <button onClick={onClose} className="text-content-secondary hover:text-content-primary text-lg leading-none">×</button>
        </div>

        <div className="p-6 space-y-4 overflow-y-auto">
          {loading ? (
            <div className="space-y-3 animate-pulse">
              <div className="h-4 bg-gray-100 rounded w-1/3" />
              <div className="h-9 bg-gray-100 rounded" />
              <div className="h-32 bg-gray-100 rounded" />
            </div>
          ) : item ? (
            <>
              <div>
                <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Título</label>
                <input className={INPUT_CLS} value={title} onChange={e => setTitle(e.target.value)} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Tipo</label>
                  <select className={INPUT_CLS} value={type} onChange={e => setType(e.target.value as ContentItemType)}>
                    {Object.entries(TYPE_LABELS).map(([v, l]) => (
                      <option key={v} value={v}>{l}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-content-secondary font-sans mb-1">Tags (vírgula)</label>
                  <input className={INPUT_CLS} value={tags} onChange={e => setTags(e.target.value)} />
                </div>
              </div>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs font-medium text-content-secondary font-sans">Conteúdo</label>
                  {contentChanged && (
                    <span className="text-[10px] font-sans text-amber-700">
                      conteúdo alterado → vai reprocessar (status volta a `pending`)
                    </span>
                  )}
                </div>
                <textarea
                  rows={14}
                  className={cn(INPUT_CLS, "resize-none font-mono text-xs")}
                  value={content}
                  onChange={e => setContent(e.target.value)}
                />
              </div>
              {error && (
                <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">{error}</div>
              )}
            </>
          ) : (
            <div className="text-sm text-content-secondary font-sans">Falha ao carregar item.</div>
          )}
        </div>

        <div className="flex justify-end gap-3 px-6 py-4 border-t border-border">
          <button onClick={onClose} className="px-4 py-2 text-sm font-sans text-content-secondary hover:text-content-primary transition-colors">
            Cancelar
          </button>
          <button
            onClick={handleSave}
            disabled={saving || loading || !item}
            className={cn(
              "px-5 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors",
              "disabled:opacity-40 disabled:cursor-not-allowed"
            )}
          >
            {saving ? "Salvando..." : "Salvar"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Enrich Profile Modal ────────────────────────────────────────────────────────
//
// "AI drafts, human reviews": extrai sugestões do item, mostra diff campo a campo
// (atual vs. sugerido) e SÓ aplica o que o usuário marcar. Regra de default:
// campos vazios no perfil atual já vêm marcados; campos preenchidos vêm
// desmarcados (nunca sobrescrevemos algo silenciosamente).

interface SuggestionRow {
  field: keyof CompanyProfile;
  label: string;
  current: unknown;
  suggested: unknown;
}

function EnrichProfileModal({
  itemId,
  token,
  onClose,
}: {
  itemId: string;
  token: string;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [rows, setRows] = useState<SuggestionRow[]>([]);
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [me, extracted] = await Promise.all([
          getMe(token),
          extractProfileFromLibraryItem(itemId, token),
        ]);
        if (cancelled) return;

        if (extracted.error && extracted.low_confidence) {
          setError(
            extracted.error === "llm_unavailable"
              ? "Serviço de IA indisponível. Tente novamente mais tarde."
              : `Não conseguimos ler o documento: ${extracted.error}`,
          );
          return;
        }

        const cur: CompanyProfile = { ...EMPTY_PROFILE, ...me.profile };
        setCurrent(cur);

        // Só campos que a extração trouxe com confiança "high" (sugestões reais).
        const built: SuggestionRow[] = [];
        const defaults: Record<string, boolean> = {};
        (Object.keys(extracted.confidence) as (keyof CompanyProfile)[]).forEach((field) => {
          if (extracted.confidence[field] !== "high") return;
          if (!(field in PROFILE_FIELD_LABELS)) return;
          const suggested = extracted.profile[field];
          if (isEmptyValue(suggested)) return;
          built.push({
            field,
            label: PROFILE_FIELD_LABELS[field],
            current: cur[field],
            suggested,
          });
          // default: marcado só quando o atual está vazio.
          defaults[field] = isEmptyValue(cur[field]);
        });
        setRows(built);
        setAccepted(defaults);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Erro ao extrair perfil");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [itemId, token]);

  function toggle(field: keyof CompanyProfile) {
    setAccepted((prev) => ({ ...prev, [field]: !prev[field] }));
  }

  const acceptedCount = rows.filter((r) => accepted[r.field]).length;

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const merged: CompanyProfile = { ...current };
      rows.forEach((r) => {
        if (accepted[r.field]) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (merged as any)[r.field] = r.suggested;
        }
      });
      await saveProfile(merged, token);
      toast.success("Perfil atualizado");
      onClose();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Erro ao salvar perfil");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Enriquecer meu perfil"
      size="lg"
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
            disabled={saving || loading || !!error || acceptedCount === 0}
            className={cn(
              "px-5 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors",
              "disabled:opacity-40 disabled:cursor-not-allowed",
            )}
          >
            {saving ? "Salvando..." : `Salvar alterações${acceptedCount ? ` (${acceptedCount})` : ""}`}
          </button>
        </>
      }
    >
      {loading ? (
        <div className="space-y-3 animate-pulse">
          <div className="h-4 bg-gray-100 rounded w-1/2" />
          <div className="h-16 bg-gray-100 rounded" />
          <div className="h-16 bg-gray-100 rounded" />
        </div>
      ) : error ? (
        <div className="rounded-lg bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700 font-sans">
          {error}
        </div>
      ) : rows.length === 0 ? (
        <p className="text-sm text-content-secondary font-sans">
          Não encontramos novas informações para sugerir a partir deste documento.
        </p>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-content-secondary font-sans">
            Revise as sugestões extraídas. Campos já preenchidos não vêm marcados —
            marque para sobrescrever. Nada é salvo sem você confirmar.
          </p>
          {rows.map((r) => {
            const isChecked = !!accepted[r.field];
            const curText = formatProfileValue(r.current);
            const sugText = formatProfileValue(r.suggested);
            return (
              <label
                key={r.field}
                className={cn(
                  "flex gap-3 rounded-lg border border-border p-3 cursor-pointer transition-colors",
                  isChecked ? "border-primary/40 bg-primary/5" : "hover:border-primary/30",
                )}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggle(r.field)}
                  className="mt-0.5 h-4 w-4 accent-primary shrink-0"
                />
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-sm font-semibold text-content-primary font-sans">{r.label}</p>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <div>
                      <p className="text-[10px] uppercase tracking-wide text-content-secondary font-sans">Atual</p>
                      <p className="text-xs text-content-secondary font-sans break-words">
                        {curText || <span className="italic">vazio</span>}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase tracking-wide text-primary font-sans">Sugerido</p>
                      <p className="text-xs text-content-primary font-sans break-words">{sugText}</p>
                    </div>
                  </div>
                </div>
              </label>
            );
          })}
        </div>
      )}
    </Modal>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function LibraryPage() {
  const { getToken } = useAuth();
  const [activeTab, setActiveTab] = useState<ContentItemType | "all">("all");
  const [items, setItems] = useState<ContentItemSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [enrichingId, setEnrichingId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);

  useEffect(() => {
    getToken().then(t => {
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
      const data = await getLibraryItems(
        tk,
        activeTab !== "all" ? activeTab : undefined,
        search || undefined,
        includeArchived
      );
      setItems(data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (token) loadItems();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, token, includeArchived]);

  // Polling: enquanto algum item estiver em pending/processing, recarrega a cada 4s
  // pra mostrar a transição pending → done sem o usuário ter que clicar refresh.
  const hasPendingItems = items.some(
    i => i.enrichment_status === "pending" || i.enrichment_status === "processing"
  );
  useEffect(() => {
    if (!hasPendingItems || !token) return;
    const id = setInterval(() => loadItems(), 4000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingItems, token]);

  async function handleDelete(id: string) {
    if (!token) return;
    setItems(prev => prev.filter(i => i.id !== id));
    try {
      await deleteLibraryItem(id, token);
      toast.success("Item excluído");
    } catch {
      toast.error("Erro ao excluir — recarregando…");
      loadItems();
    }
  }

  async function handleArchive(id: string) {
    if (!token) return;
    // optimistic remove (or keep if viewing archived)
    if (!includeArchived) {
      setItems(prev => prev.filter(i => i.id !== id));
    }
    try {
      await archiveLibraryItem(id, token);
      toast.success("Item arquivado");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "";
      if (msg.includes("404")) {
        // backend endpoint not yet deployed — surface gentle hint
        toast.error("Arquivar ainda não está disponível (404). Recarregando…");
      } else {
        toast.error("Erro ao arquivar — recarregando…");
      }
      loadItems();
    }
  }

  return (
    <DashboardLayout>
      <div className="max-w-5xl mx-auto px-4 py-8 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="font-heading text-xl font-bold text-content-primary">Biblioteca</h1>
            <p className="text-sm text-content-secondary font-sans mt-0.5">
              Propostas, projetos e documentos que informam suas novas propostas
            </p>
          </div>
          <button
            onClick={() => setShowModal(true)}
            className="px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors"
          >
            + Adicionar
          </button>
        </div>

        {/* Search + Tabs */}
        <div className="space-y-3">
          <input
            className={INPUT_CLS}
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => e.key === "Enter" && loadItems()}
            placeholder="Buscar por título..."
          />
          <div className="flex gap-1 overflow-x-auto pb-1">
            {TABS.map(tab => (
              <button
                key={tab.value}
                onClick={() => setActiveTab(tab.value)}
                className={cn(
                  "shrink-0 px-3 py-1.5 rounded-full text-xs font-sans font-medium transition-colors",
                  activeTab === tab.value
                    ? "bg-primary text-white"
                    : "bg-white border border-border text-content-secondary hover:border-primary/40"
                )}
              >
                {tab.label}
              </button>
            ))}
            <div className="ml-auto flex items-center gap-2 pl-2">
              <label className="text-xs font-sans text-content-secondary flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={includeArchived}
                  onChange={e => setIncludeArchived(e.target.checked)}
                  className="h-3.5 w-3.5 accent-primary"
                />
                Ver arquivados
              </label>
            </div>
          </div>
        </div>

        {/* Grid */}
        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1,2,3].map(i => (
              <div key={i} className="bg-white rounded-xl border border-border p-4 space-y-3 animate-pulse">
                <div className="h-3 bg-gray-100 rounded w-24" />
                <div className="h-4 bg-gray-100 rounded w-3/4" />
                <div className="h-3 bg-gray-100 rounded w-full" />
                <div className="h-3 bg-gray-100 rounded w-2/3" />
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-16 space-y-3">
            <p className="text-4xl">📂</p>
            <p className="text-sm font-sans text-content-secondary">
              Nenhum documento encontrado.<br />
              Adicione propostas ou projetos anteriores para enriquecer suas novas propostas.
            </p>
            <button
              onClick={() => setShowModal(true)}
              className="mt-2 px-4 py-2 rounded-xl text-sm font-semibold font-sans text-white bg-primary hover:bg-primary-hover transition-colors"
            >
              Adicionar primeiro documento
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {items.map(item => (
              <ItemCard
                key={item.id}
                item={item}
                onEdit={(id) => setEditingId(id)}
                onDelete={handleDelete}
                onArchive={handleArchive}
                onEnrich={(id) => setEnrichingId(id)}
              />
            ))}
          </div>
        )}
      </div>

      {showModal && token && (
        <AddModal
          token={token}
          onClose={() => setShowModal(false)}
          onSaved={() => loadItems()}
        />
      )}

      {editingId && token && (
        <EditModal
          itemId={editingId}
          token={token}
          onClose={() => setEditingId(null)}
          onSaved={() => {
            toast.success("Item atualizado");
            loadItems();
          }}
        />
      )}

      {enrichingId && token && (
        <EnrichProfileModal
          itemId={enrichingId}
          token={token}
          onClose={() => setEnrichingId(null)}
        />
      )}
    </DashboardLayout>
  );
}
