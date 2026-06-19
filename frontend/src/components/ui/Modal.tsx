"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

type ModalSize = "sm" | "md" | "lg";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: ModalSize;
  className?: string;
}

const sizeStyles: Record<ModalSize, string> = {
  sm: "max-w-sm",
  md: "max-w-lg",
  lg: "max-w-2xl",
};

/**
 * Controlled modal dialog built on @radix-ui/react-dialog (already installed).
 * Closes on Escape and backdrop click (handled by Radix). Centered card with a
 * subtle fade/scale-in transition, scrollable body for tall content.
 */
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = "md",
  className,
}: ModalProps) {
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay
          className={cn(
            "fixed inset-0 z-50 bg-black/40 backdrop-blur-[1px]",
            "transition-opacity duration-200",
            "data-[state=open]:opacity-100 data-[state=closed]:opacity-0"
          )}
        />
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <Dialog.Content
            onPointerDownOutside={onClose}
            className={cn(
              "w-full bg-surface rounded-xl shadow-card border border-border",
              "flex max-h-[85vh] flex-col overflow-hidden focus:outline-none",
              "transition-all duration-200",
              "data-[state=open]:opacity-100 data-[state=open]:scale-100",
              "data-[state=closed]:opacity-0 data-[state=closed]:scale-95",
              sizeStyles[size],
              className
            )}
          >
            {title && (
              <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
                <Dialog.Title className="font-heading text-lg font-semibold text-content-primary">
                  {title}
                </Dialog.Title>
                <Dialog.Close
                  aria-label="Fechar"
                  className="rounded-lg p-1 text-content-secondary transition-colors hover:bg-app-bg hover:text-content-primary"
                >
                  <span aria-hidden className="text-lg leading-none">
                    ×
                  </span>
                </Dialog.Close>
              </div>
            )}

            <div className="flex-1 overflow-y-auto px-5 py-4 text-sm text-content-primary">
              {children}
            </div>

            {footer && (
              <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-4">
                {footer}
              </div>
            )}
          </Dialog.Content>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
