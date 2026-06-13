import { redirect } from "next/navigation";

// Aposentado (spec_frontend_chat_first.md, Fase 1): a lista de sessões virou a
// ConversationSidebar (histórico de conversas). Mantemos o diretório como
// redirect server-side para não quebrar links antigos.
export default function SessionsPage() {
  redirect("/");
}
