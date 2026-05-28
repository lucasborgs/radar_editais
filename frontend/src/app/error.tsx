"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-6 text-center">
      <h2 className="text-lg font-semibold text-content-primary">
        Algo deu errado
      </h2>
      <p className="max-w-md text-sm text-content-secondary">
        Encontramos um problema ao carregar esta página. Você pode tentar de
        novo — se persistir, recarregue a aplicação.
      </p>
      <button
        onClick={reset}
        className="rounded-md bg-content-primary px-4 py-2 text-sm font-medium text-app-bg transition hover:opacity-90"
      >
        Tentar novamente
      </button>
    </div>
  );
}
