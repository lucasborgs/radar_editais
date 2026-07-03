"use client";

import { useState, useEffect, useCallback, useRef } from "react";

import { getMe } from "@/lib/api";
import { useAuth } from "@/lib/auth";

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

// Cache de módulo: 1 GET /me por aba resolve o flag de operador para todos os
// componentes que o consultam (sidebar, header). Keyed por user id — troca de
// conta invalida naturalmente.
let adminCache: { userId: string; isAdmin: boolean } | null = null;

/**
 * Flag de operador do sistema (`is_admin` do GET /me — allowlist ADMIN_EMAILS
 * no backend). Controla a exibição de ferramentas de gestão na UI (ex.: fila
 * da Descoberta). Anônimo/erro → false; o enforcement real é o 403 da API.
 */
export function useIsAdmin(): boolean {
  const { session, getToken } = useAuth();
  const userId = session?.user?.id ?? null;
  const [isAdmin, setIsAdmin] = useState(
    adminCache !== null && adminCache.userId === userId ? adminCache.isAdmin : false,
  );

  useEffect(() => {
    let cancelled = false;
    if (!userId) {
      setIsAdmin(false);
      return;
    }
    if (adminCache !== null && adminCache.userId === userId) {
      setIsAdmin(adminCache.isAdmin);
      return;
    }
    (async () => {
      try {
        const token = await getToken();
        if (!token) return;
        const me = await getMe(token);
        adminCache = { userId, isAdmin: !!me.is_admin };
        if (!cancelled) setIsAdmin(!!me.is_admin);
      } catch {
        // segue não-admin — os endpoints devolvem 403 de qualquer forma.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [userId, getToken]);

  return isAdmin;
}
