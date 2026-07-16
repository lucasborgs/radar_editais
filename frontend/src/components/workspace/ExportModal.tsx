"use client";

import { useState } from "react";
import { toast } from "sonner";
import { exportDocument, saveSessionToStorage } from "@/lib/api";
import type { WritingMode } from "@/types/api";
import { buildDocxBlob } from "./exportDocx";
import { modeLabel, type WorkspaceSection } from "./types";

/**
 * Modal de export (spec §F5): PDF (print stylesheet), DOCX (lib client),
 * Markdown (download + copiar) e Salvar na nuvem (POST save-to-storage).
 * Geração client-side a partir das seções; o markdown canônico vem de
 * GET /writing/{id}/export.
 */
export function ExportModal({
  sessionId,
  sections,
  targetTitle,
  mode,
  token,
  onClose,
}: {
  sessionId: string;
  sections: WorkspaceSection[];
  targetTitle: string;
  mode: WritingMode;
  token: string | null;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState<null | "pdf" | "docx" | "md" | "copy" | "cloud">(null);

  const docTitle = `${modeLabel(mode)} — ${targetTitle}`;

  // ── PDF: abre uma janela de impressão limpa e chama window.print() ──────────
  function handlePdf() {
    setBusy("pdf");
    try {
      const w = window.open("", "_blank", "width=820,height=1000");
      if (!w) {
        toast.error("Permita pop-ups para gerar o PDF.");
        return;
      }
      w.document.write(printHtml(docTitle, targetTitle, mode, sections));
      w.document.close();
      w.focus();
      // Aguarda o layout antes de imprimir.
      w.onload = () => {
        w.print();
      };
      // Fallback caso onload não dispare (conteúdo síncrono).
      setTimeout(() => {
        try {
          w.print();
        } catch {
          /* janela já fechada */
        }
      }, 400);
    } finally {
      setBusy(null);
    }
  }

  // ── DOCX: gera blob via lib docx e dispara download ────────────────────────
  async function handleDocx() {
    setBusy("docx");
    try {
      const blob = await buildDocxBlob(docTitle, sections);
      downloadBlob(blob, `${slug(targetTitle)}.docx`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Falha ao gerar o DOCX.");
    } finally {
      setBusy(null);
    }
  }

  // ── Markdown: baixa .md (de GET /export) ───────────────────────────────────
  async function handleMd() {
    setBusy("md");
    try {
      const { markdown } = await exportDocument(sessionId);
      downloadBlob(new Blob([markdown], { type: "text/markdown" }), `${slug(targetTitle)}.md`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Falha ao exportar o Markdown.");
    } finally {
      setBusy(null);
    }
  }

  async function handleCopyMd() {
    setBusy("copy");
    try {
      const { markdown } = await exportDocument(sessionId);
      await navigator.clipboard.writeText(markdown);
      toast.success("Markdown copiado.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Falha ao copiar.");
    } finally {
      setBusy(null);
    }
  }

  // ── Salvar na nuvem: POST save-to-storage ──────────────────────────────────
  async function handleCloud() {
    if (!token) {
      toast.error("Faça login para salvar na nuvem.");
      return;
    }
    setBusy("cloud");
    try {
      const res = await saveSessionToStorage(sessionId, token);
      toast.success("Proposta salva na nuvem.", {
        description: res.path,
        action: res.signed_url
          ? { label: "Abrir", onClick: () => window.open(res.signed_url, "_blank") }
          : undefined,
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Falha ao salvar na nuvem.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-surface rounded-2xl border border-border w-full max-w-md shadow-xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="font-heading text-base font-bold text-content-primary">Exportar</h2>
          <button
            onClick={onClose}
            className="text-content-secondary hover:text-content-primary transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-4 space-y-2">
          <ExportOption
            icon="🖨"
            title="PDF"
            desc="Imagem limpa para impressão ou salvar como PDF."
            loading={busy === "pdf"}
            onClick={handlePdf}
          />
          <ExportOption
            icon="📄"
            title="Word (.docx)"
            desc="Documento editável por seções."
            loading={busy === "docx"}
            onClick={() => void handleDocx()}
          />
          <div className="rounded-xl border border-border p-3">
            <div className="flex items-center gap-3">
              <span className="text-lg" aria-hidden>
                ⬇
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-content-primary font-sans">Markdown</p>
                <p className="text-xs text-content-secondary font-sans">
                  Baixe o .md ou copie tudo.
                </p>
              </div>
            </div>
            <div className="mt-2 flex gap-2">
              <button
                onClick={() => void handleMd()}
                disabled={busy === "md"}
                className="flex-1 text-xs font-sans text-content-primary border border-border rounded-lg py-1.5 hover:bg-app-bg transition-colors disabled:opacity-50"
              >
                {busy === "md" ? "Baixando…" : "Baixar .md"}
              </button>
              <button
                onClick={() => void handleCopyMd()}
                disabled={busy === "copy"}
                className="flex-1 text-xs font-sans text-content-primary border border-border rounded-lg py-1.5 hover:bg-app-bg transition-colors disabled:opacity-50"
              >
                {busy === "copy" ? "Copiando…" : "Copiar tudo"}
              </button>
            </div>
          </div>
          <ExportOption
            icon="☁"
            title="Salvar na nuvem"
            desc="Guarda o documento no seu workspace (Supabase Storage)."
            loading={busy === "cloud"}
            onClick={() => void handleCloud()}
          />
        </div>
      </div>
    </div>
  );
}

function ExportOption({
  icon,
  title,
  desc,
  loading,
  onClick,
}: {
  icon: string;
  title: string;
  desc: string;
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="w-full flex items-center gap-3 rounded-xl border border-border p-3 text-left hover:bg-app-bg transition-colors disabled:opacity-60"
    >
      <span className="text-lg" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-content-primary font-sans">{title}</p>
        <p className="text-xs text-content-secondary font-sans">{desc}</p>
      </div>
      {loading && (
        <span className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin shrink-0" />
      )}
    </button>
  );
}

// ── Helpers ────────────────────────────────────────────────────────────────

function slug(s: string): string {
  return (
    s
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60) || "proposta"
  );
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Markdown inline básico → HTML (bold/itálico) para a visão de impressão.
function inlineMd(line: string): string {
  return escapeHtml(line)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*(?!\*)(.+?)\*(?!\*)/g, "<em>$1</em>")
    .replace(/(?<!_)_(?!_)(.+?)_(?!_)/g, "<em>$1</em>");
}

// Corpo de seção (markdown) → HTML simples (parágrafos + listas).
function bodyHtml(content: string): string {
  const lines = content.split(/\r?\n/);
  const html: string[] = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      closeList();
      continue;
    }
    const bullet = line.match(/^[-*+]\s+(.*)$/) || line.match(/^\d+[.)]\s+(.*)$/);
    if (bullet) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${inlineMd(bullet[1])}</li>`);
      continue;
    }
    closeList();
    const sub = line.match(/^#{1,6}\s+(.*)$/);
    if (sub) {
      html.push(`<p><strong>${inlineMd(sub[1])}</strong></p>`);
      continue;
    }
    html.push(`<p>${inlineMd(line)}</p>`);
  }
  closeList();
  return html.join("\n");
}

// Documento HTML completo para a janela de impressão (cabeçalho + seções numeradas).
function printHtml(
  docTitle: string,
  targetTitle: string,
  mode: WritingMode,
  sections: WorkspaceSection[],
): string {
  const date = new Date().toLocaleDateString("pt-BR");
  const body = sections
    .map((s, i) => {
      const content = s.content.trim()
        ? bodyHtml(s.content)
        : "<p><em>[A preencher]</em></p>";
      return `<section><h2>${i + 1}. ${escapeHtml(s.title)}</h2>${content}</section>`;
    })
    .join("\n");

  return `<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(docTitle)}</title>
  <style>
    @page { margin: 2.5cm 2cm; }
    * { box-sizing: border-box; }
    body { font-family: Georgia, "Times New Roman", serif; color: #1a1a1a; line-height: 1.5; max-width: 720px; margin: 0 auto; padding: 2rem; }
    header { border-bottom: 2px solid #333; padding-bottom: 0.75rem; margin-bottom: 1.5rem; }
    header .meta { font-size: 0.8rem; color: #666; }
    h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
    h2 { font-size: 1.05rem; margin: 1.5rem 0 0.5rem; page-break-after: avoid; }
    p { margin: 0.4rem 0; }
    ul { margin: 0.4rem 0 0.4rem 1.25rem; }
    section { page-break-inside: avoid; }
    @media print { body { padding: 0; } }
  </style>
</head>
<body>
  <header>
    <h1>${escapeHtml(targetTitle)}</h1>
    <p class="meta">${modeLabel(mode)} · ${date}</p>
  </header>
  ${body}
</body>
</html>`;
}
