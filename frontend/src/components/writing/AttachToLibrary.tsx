"use client";

// Botão de clipe (📎) para o composer de escrita: faz upload de um arquivo para
// a biblioteca pelo MESMO caminho que a página /library usa (uploadLibraryPdf →
// POST /library/upload-pdf). O enriquecimento LLM dispara assíncrono no backend
// (enrich_content_task) ao criar o item; aqui só avisamos o usuário, via toast,
// que o item ficou disponível para @ menção.
//
// É um componente isolado para mexer o mínimo possível na página da fase 3 —
// plugado ao lado do MentionsTextarea no composer. NÃO confundir com o clipe do
// Composer da home (extração de perfil, decisão D4).

import { useRef, useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { uploadLibraryPdf } from "@/lib/api";

interface AttachToLibraryProps {
  token: string | null;
  disabled?: boolean;
  // Notifica o pai do item recém-criado (ex.: para inserir a @ menção). Opcional.
  onAttached?: (item: { id: string; title: string }) => void;
  className?: string;
}

// Mesmas extensões aceitas pelo backend (SUPPORTED_UPLOAD_EXTS) e pela /library.
const ACCEPT = ".pdf,.docx,.txt,.md";

export function AttachToLibrary({
  token,
  disabled,
  onAttached,
  className,
}: AttachToLibraryProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  async function handleFile(file: File) {
    if (!token) {
      toast.error("Faça login para anexar arquivos.");
      return;
    }
    // Título derivado do nome do arquivo (sem extensão); type padrão como no
    // backend (technical_doc) — o usuário pode reclassificar depois em Arquivos.
    const title = file.name.replace(/\.[^.]+$/, "") || file.name;
    setUploading(true);
    const toastId = toast.loading(`Anexando "${title}"…`);
    try {
      const item = await uploadLibraryPdf(
        file,
        { title, type: "technical_doc", tags: "" },
        token,
      );
      toast.success(
        `"${item.title}" anexado — disponível via @ menção (enriquecendo em segundo plano).`,
        { id: toastId },
      );
      onAttached?.({ id: item.id, title: item.title });
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Erro ao anexar arquivo.", {
        id: toastId,
      });
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleFile(f);
          // Permite re-selecionar o mesmo arquivo depois.
          e.target.value = "";
        }}
      />
      <button
        type="button"
        aria-label="Anexar arquivo à biblioteca"
        title="Anexar arquivo (disponível via @ menção)"
        onClick={() => fileRef.current?.click()}
        disabled={disabled || uploading}
        className={cn(
          "self-end shrink-0 w-10 h-10 rounded-xl flex items-center justify-center",
          "border border-border text-content-secondary bg-white",
          "hover:text-content-primary hover:border-content-secondary transition-colors",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          className,
        )}
      >
        {uploading ? (
          <span className="w-4 h-4 border-2 border-content-secondary border-t-transparent rounded-full animate-spin" />
        ) : (
          // Ícone de clipe (paperclip)
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
          </svg>
        )}
      </button>
    </>
  );
}
