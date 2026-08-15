const PENDING_CONSULTANT_INTENT_KEY = "pending_consultant_intent";

type StorageLike = Pick<Storage, "getItem" | "removeItem" | "setItem">;

/** Guarda a mensagem que motivou o login para retomá-la ao voltar ao Consultor. */
export function savePendingConsultantIntent(storage: StorageLike, intent: string): void {
  storage.setItem(PENDING_CONSULTANT_INTENT_KEY, intent);
}

/** Consome a intenção uma única vez, evitando reenvio a cada recarga. */
export function takePendingConsultantIntent(storage: StorageLike): string | null {
  const intent = storage.getItem(PENDING_CONSULTANT_INTENT_KEY);
  storage.removeItem(PENDING_CONSULTANT_INTENT_KEY);
  return intent?.trim() || null;
}
