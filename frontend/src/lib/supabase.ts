import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

// Singleton no browser: cada `createBrowserClient` cria uma instância nova
// com seu próprio estado de auth (token em memória + listener). Quando o
// AuthProvider montava UM cliente e `apiFetch.getAccessToken` criava OUTRO
// a cada request, o segundo não enxergava a sessão do primeiro → 401
// "Not authenticated" em rotas autenticadas. Cachear no nível do módulo
// garante que AuthProvider e apiFetch usem a mesma instância.
let _browserClient: SupabaseClient | null = null;

export function createSupabaseClient(): SupabaseClient {
  // SSR: sempre cria novo (sem `window`, cache global não faz sentido).
  if (typeof window === "undefined") {
    return createBrowserClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    );
  }
  if (!_browserClient) {
    _browserClient = createBrowserClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    );
  }
  return _browserClient;
}
