// Resolver de sessão do workspace (spec §6/F1): dado um alvo (edital ou
// `investidor:<slug>`), encontra a sessão de escrita mais recente desse alvo no
// workspace do usuário; se não houver, cria uma nova. Retorna o session_id para
// navegar a /workspace/{id}.
//
// Centraliza o que o redirect de /chat?edital= e os botões da 1a ("Começar
// proposta" / "Escrever pitch") precisam — um único ponto de criação/resolução.

import {
  listWritingSessions,
  startWritingSession,
  type WritingSessionSummary,
} from "@/lib/api";
import type { CompanyProfile } from "@/types/profile";
import type { WritingMode } from "@/types/api";

// Escolhe a sessão mais recente (por updated_at, fallback created_at) do alvo.
function mostRecent(
  sessions: WritingSessionSummary[],
  editalId: string,
): WritingSessionSummary | null {
  const mine = sessions.filter((s) => s.edital_id === editalId);
  if (mine.length === 0) return null;
  return mine.reduce((a, b) =>
    (b.updated_at ?? b.created_at) > (a.updated_at ?? a.created_at) ? b : a,
  );
}

/**
 * Resolve (ou cria) a sessão de escrita do alvo e devolve o session_id.
 *
 * @param editalId  id do alvo (edital/desafio/programa OU `investidor:<slug>`).
 * @param profile   perfil da empresa (usado só na criação).
 * @param token     token de auth (a listagem é autenticada).
 * @param mode      opcional — força o modo na criação ('pitch' p/ investidor).
 */
export async function resolveWorkspaceSession(
  editalId: string,
  profile: CompanyProfile,
  token: string,
  mode?: WritingMode,
): Promise<string> {
  // 1. Tenta reaproveitar a sessão mais recente do alvo.
  try {
    const { sessions } = await listWritingSessions(token);
    const existing = mostRecent(sessions, editalId);
    if (existing) return existing.session_id;
  } catch {
    // Falha na listagem não deve impedir a criação — segue pro start.
  }

  // 2. Cria uma nova sessão.
  const res = await startWritingSession(editalId, profile, mode);
  return res.session_id;
}
