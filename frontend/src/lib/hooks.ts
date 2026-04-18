"use client";

import { useState, useEffect, useCallback, useRef } from "react";

interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/**
 * Hook genérico para chamadas assíncronas com estados loading/error.
 *
 * @param fn  - Função que retorna uma Promise<T>
 * @param deps - Dependências (como useEffect). Muda quando a função deve re-executar.
 *
 * @example
 * const { data, loading, error } = useAsync(() => getDashboardStats(), []);
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[] = []
): AsyncState<T> & { refetch: () => void } {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  // Keep stable ref to fn to avoid stale closures
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const execute = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await fnRef.current();
      setState({ data, loading: false, error: null });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Erro desconhecido";
      setState({ data: null, loading: false, error: message });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    execute();
  }, [execute]);

  return { ...state, refetch: execute };
}
