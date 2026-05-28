"use client";

import { useEffect } from "react";

// Fallback de último recurso: captura erros que estouram no próprio RootLayout.
// Precisa renderizar <html>/<body> porque substitui o layout raiz.
export default function GlobalError({
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
    <html lang="pt-BR">
      <body
        style={{
          display: "flex",
          minHeight: "100vh",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "1rem",
          fontFamily: "system-ui, sans-serif",
          padding: "1.5rem",
          textAlign: "center",
        }}
      >
        <h2 style={{ fontSize: "1.125rem", fontWeight: 600 }}>
          Erro inesperado
        </h2>
        <p style={{ maxWidth: "28rem", fontSize: "0.875rem", color: "#666" }}>
          A aplicação encontrou um erro grave. Recarregue a página para
          continuar.
        </p>
        <button
          onClick={reset}
          style={{
            borderRadius: "0.375rem",
            background: "#111",
            color: "#fff",
            padding: "0.5rem 1rem",
            fontSize: "0.875rem",
            border: "none",
            cursor: "pointer",
          }}
        >
          Recarregar
        </button>
      </body>
    </html>
  );
}
