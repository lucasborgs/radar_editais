// Transcript tipado do front-door (M2+). O transcript deixou de ser uma lista
// de {role, content} (M1) e passa a ser uma lista de ENTRADAS heterogêneas:
// mensagens, cards de diff de perfil, cards de radar e gates de login. Persiste
// em sessionStorage `frontdoor_history` v2 (conversa por visita/aba) — `migrateHistory` converte o formato
// antigo (array de {role,content}) em entradas `msg`, sem descartar nada.

import type { CompanyProfile } from "./profile";
import { EMPTY_PROFILE } from "./profile";
import type { ConversationEntry, MatchedEdital, MatchedEntity, ProfileDiffItem } from "@/lib/api";

export type ChatRole = "user" | "assistant";

// Status de decisão de um card de diff: pendente até o usuário aceitar/descartar.
export type DiffStatus = "pending" | "accepted" | "dismissed";

// Ações que exigem conta (gate de login) — texto do card varia por ação.
export type GateAction = "anexo" | "brief" | "proposta";

export interface MsgEntry {
  kind: "msg";
  role: ChatRole;
  content: string;
  // PR6.2: resposta interrompida no teto de passos do agente (aviso discreto).
  truncated?: boolean;
  // PR1 (4-phase): oferta de planejamento de proposta.
  nextAction?: {
    offer: string;
    options: Array<{ label: string; action: string }>;
  };
}

export interface DiffEntry {
  kind: "diff";
  items: ProfileDiffItem[];
  status: DiffStatus;
  // Origem do diff: "turn" (proposta do LLM no turno), "document" (extração de
  // anexo), "merge" (conflito conta vs. conversa no login), "manual" (editar
  // perfil pela barra de status), "extract" (hero de URL da Etapa 1 do
  // onboarding). Só muda a copy/título do card.
  origin?: "turn" | "document" | "merge" | "manual" | "extract";
  // Id da row em session_turns quando a conversa é persistida (logado, spec
  // chat-first fase 2) — alvo do PATCH no aceite/descarte.
  entryId?: number;
}

export interface GateEntry {
  kind: "gate";
  action: GateAction;
}

export interface ProfileIncompleteEntry {
  kind: "profile_incomplete";
  missingFields: string[];
}

// Snapshot de match devolvido durante Explorar. A entrada preserva o contrato e
// o histórico, mas a home mostra apenas uma prévia; a experiência completa vive
// em /radar.
export interface RadarEntry {
  kind: "radar";
  matchedEditais: MatchedEdital[];
  matchedEntities: MatchedEntity[];
}

export type TranscriptEntry =
  | MsgEntry
  | DiffEntry
  | GateEntry
  | ProfileIncompleteEntry
  | RadarEntry;

// ── Persistência (sessionStorage v2) ──────────────────────────────────────────
export const HISTORY_KEY = "frontdoor_history";
// Binding com a conversa persistida no servidor (logado, spec chat-first fase
// 2): F5 na mesma aba retoma a MESMA conversa do banco em vez de criar outra.
export const SESSION_ID_KEY = "frontdoor_session_id";

// Migra/valida o conteúdo cru do localStorage para entradas tipadas. Aceita:
//   • v2: array de TranscriptEntry (passa por sanidade leve);
//   • v1 (M1): array de {role, content} → cada um vira uma MsgEntry.
export function migrateHistory(raw: unknown): TranscriptEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: TranscriptEntry[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const e = item as Record<string, unknown>;
    // v1: {role, content} sem `kind` → MsgEntry.
    if (!("kind" in e) && (e.role === "user" || e.role === "assistant")) {
      if (typeof e.content === "string") {
        out.push({ kind: "msg", role: e.role, content: e.content });
      }
      continue;
    }
    // v2: confia no `kind` (sanidade mínima por tipo).
    switch (e.kind) {
      case "msg":
        if (
          (e.role === "user" || e.role === "assistant") &&
          typeof e.content === "string"
        ) {
          out.push({ kind: "msg", role: e.role, content: e.content });
        }
        break;
      case "diff":
        if (Array.isArray(e.items)) {
          out.push({
            kind: "diff",
            items: e.items as DiffEntry["items"],
            status: (e.status as DiffStatus) ?? "pending",
            origin: e.origin as DiffEntry["origin"],
            entryId: typeof e.entryId === "number" ? e.entryId : undefined,
          });
        }
        break;
      case "gate":
        if (e.action === "anexo" || e.action === "brief" || e.action === "proposta") {
          out.push({ kind: "gate", action: e.action });
        }
        break;
      case "profile_incomplete":
        if (Array.isArray(e.missingFields)) {
          out.push({ kind: "profile_incomplete", missingFields: e.missingFields as string[] });
        }
        break;
      case "radar":
        if (Array.isArray(e.matchedEditais) || Array.isArray(e.matchedEntities)) {
          out.push({
            kind: "radar",
            matchedEditais: (e.matchedEditais as MatchedEdital[]) ?? [],
            matchedEntities: (e.matchedEntities as MatchedEntity[]) ?? [],
          });
        }
        break;
      default:
        break;
    }
  }
  return out;
}

// Reconstrói o transcript a partir do GET /conversations/{id} (retomada de
// conversa frontdoor, spec chat-first fase 2). Mesma postura defensiva do
// migrateHistory: entradas malformadas/kinds desconhecidos são ignorados.
// O payload de diff/radar é a própria entrada serializada (persist_frontdoor_turn
// e o POST /entries gravam {items,status,origin} / {data,ts}).
export function entriesFromServer(entries: ConversationEntry[]): TranscriptEntry[] {
  const out: TranscriptEntry[] = [];
  for (const e of entries) {
    switch (e.entry_kind) {
      case "msg":
        if ((e.role === "user" || e.role === "assistant") && e.content) {
          out.push({ kind: "msg", role: e.role, content: e.content });
        }
        break;
      case "diff": {
        const p = (e.payload ?? {}) as Record<string, unknown>;
        if (Array.isArray(p.items)) {
          out.push({
            kind: "diff",
            items: p.items as DiffEntry["items"],
            status: (p.status as DiffStatus) ?? "pending",
            origin: p.origin as DiffEntry["origin"],
            entryId: e.id,
          });
        }
        break;
      }
      case "profile_incomplete": {
        const p = (e.payload ?? {}) as Record<string, unknown>;
        if (Array.isArray(p.missingFields)) {
          out.push({ kind: "profile_incomplete", missingFields: p.missingFields as string[] });
        }
        break;
      }
      case "radar": {
        const p = (e.payload ?? {}) as Record<string, unknown>;
        if (Array.isArray(p.matched_editais) || Array.isArray(p.matched_entities)) {
          out.push({
            kind: "radar",
            matchedEditais: (p.matched_editais as MatchedEdital[]) ?? [],
            matchedEntities: (p.matched_entities as MatchedEntity[]) ?? [],
          });
        }
        break;
      }
      default:
        break;
    }
  }
  return out;
}

// Serializa só as mensagens p/ o `history` enviado ao /frontdoor/turn (o backend
// só entende {role, content}; cards são estado de UI).
export function toApiHistory(
  entries: TranscriptEntry[],
): { role: ChatRole; content: string }[] {
  // O request envia a mensagem atual no campo `message`; history é somente o
  // transcript anterior.
  return entries
    .filter((e): e is MsgEntry => e.kind === "msg")
    .map((e) => ({ role: e.role, content: e.content }));
}

// ── Completude do perfil (barra de status) ──────────────────────────────────
// Heurística da spec §3: fração preenchida de um subconjunto representativo.
const COMPLETENESS_FIELDS: (keyof CompanyProfile)[] = [
  "nome",
  "one_liner",
  "descricao_atividades",
  "tamanho_empresa",
  "uf",
  "trl",
  "ano_fundacao",
  "tipos_financiamento_interesse",
];

export function profileCompleteness(profile: CompanyProfile): number {
  let filled = 0;
  for (const field of COMPLETENESS_FIELDS) {
    const v = profile[field];
    if (Array.isArray(v) ? v.length > 0 : v !== null && v !== "") filled += 1;
  }
  return Math.round((filled / COMPLETENESS_FIELDS.length) * 100);
}

// Perfil "rodável" pelo radar: o backend exige nome + descricao_atividades (422).
export function isRadarReady(profile: CompanyProfile): boolean {
  return !!profile.nome.trim() && !!profile.descricao_atividades.trim();
}

const WRITING_MIN_FIELDS: (keyof CompanyProfile)[] = [
  "nome", "tipo_entidade", "trl", "tamanho_empresa", "uf", "descricao_atividades",
];

export function isCompleteForWriting(profile: CompanyProfile): { ok: boolean; missing: string[] } {
  const missing: string[] = [];
  for (const field of WRITING_MIN_FIELDS) {
    const val = profile[field];
    if (field === "trl") {
      if (val === null || val === undefined) missing.push(field);
    } else {
      if (val === "" || val === null || val === undefined) missing.push(field);
    }
  }
  return { ok: missing.length === 0, missing };
}

// Aplica os items de um diff (com `new` final) sobre um perfil, devolvendo um
// novo perfil. Tolera campos fora do CompanyProfile (ignora-os).
export function applyDiff(
  profile: CompanyProfile,
  items: ProfileDiffItem[],
): CompanyProfile {
  const next: CompanyProfile = { ...profile };
  for (const it of items) {
    if (it.field in EMPTY_PROFILE) {
      // O `new` já vem coerido (DiffCard coage no aceite com edição).
      (next as unknown as Record<string, unknown>)[it.field] = it.new;
    }
  }
  return next;
}

// Rótulos PT-BR dos campos do perfil para os cards de diff. Compartilhado entre
// o "editar perfil" (diffFromProfile) e a extração por URL/documento
// (diffFromExtracted) — evita rótulos crus (nome do campo) na 1ª impressão.
export const PROFILE_FIELD_LABELS: Partial<Record<keyof CompanyProfile, string>> = {
  nome: "Nome",
  tipo_entidade: "Tipo de entidade",
  one_liner: "Proposta (one-liner)",
  solution_summary: "Resumo da solução",
  descricao_atividades: "Descrição das atividades",
  tamanho_empresa: "Porte",
  uf: "UF",
  ano_fundacao: "Ano de fundação",
  trl: "TRL",
  faturamento_anual: "Faturamento anual",
  tipos_financiamento_interesse: "Interesse de financiamento",
  estagio: "Estágio",
  mrr_arr: "MRR/ARR",
  round_alvo_brl: "Round alvo (R$)",
};

// Constrói um diff "manual" com TODOS os campos atuais do perfil (para o
// "editar perfil" da barra de status reusar o DiffCard em modo edição).
export function diffFromProfile(profile: CompanyProfile): ProfileDiffItem[] {
  return (Object.keys(PROFILE_FIELD_LABELS) as (keyof CompanyProfile)[]).map((field) => ({
    field,
    label: PROFILE_FIELD_LABELS[field] ?? field,
    old: profile[field],
    new: profile[field],
  }));
}

// Diff só dos campos que a extração (URL/documento) preencheu, em relação ao
// perfil atual. Usado pelo hero de URL (Etapa 1) e pelo anexo de documento:
// "AI drafts, humans decide" — o humano revisa antes de aplicar. Campos vazios
// na extração ficam de fora.
export function diffFromExtracted(
  current: CompanyProfile,
  extracted: CompanyProfile,
): ProfileDiffItem[] {
  const out: ProfileDiffItem[] = [];
  for (const field of Object.keys(EMPTY_PROFILE) as (keyof CompanyProfile)[]) {
    const v = extracted[field];
    const empty = Array.isArray(v) ? v.length === 0 : v === "" || v === null;
    if (empty) continue;
    out.push({
      field,
      label: PROFILE_FIELD_LABELS[field] ?? field,
      old: current[field],
      new: v,
    });
  }
  return out;
}

// ── Etapa 2 do onboarding: "destravar mais matches" (gap-driven) ─────────────
// Campo faltante de alto impacto + por que pedir. Determinístico (não depende do
// LLM lembrar de perguntar). Spec onboarding-input-ux, Decisão 3.
export interface ProfileGap {
  field: keyof CompanyProfile;
  prompt: string; // pergunta curta
  why: string; // o que destrava
}

// Lacunas de maior alavanca, ordenadas por impacto, dado o perfil. Profile-only
// (pós-Sprint 3 o radar legacy saiu): `capital_social` é pedido para porte
// pequeno (MEI/ME) — proxy do "edital pode exigir contrapartida". Máx. 3 itens.
export function missingHighImpact(profile: CompanyProfile): ProfileGap[] {
  const gaps: ProfileGap[] = [];

  if (profile.tipos_financiamento_interesse.length === 0) {
    gaps.push({
      field: "tipos_financiamento_interesse",
      prompt: "Que tipo de fomento te interessa?",
      why: "Define o eixo de mecanismo do match.",
    });
  }
  if (profile.faturamento_anual === null) {
    gaps.push({
      field: "faturamento_anual",
      prompt: "Qual o faturamento anual aproximado (R$)?",
      why: "Vários editais filtram por porte/receita.",
    });
  }
  const small = profile.tamanho_empresa === "MEI" || profile.tamanho_empresa === "ME";
  if (profile.capital_social === null && small) {
    gaps.push({
      field: "capital_social",
      prompt: "Qual o capital social da empresa (R$)?",
      why: "Pode destravar editais que exigem contrapartida.",
    });
  }
  return gaps.slice(0, 3);
}

// Mensagem local (sem LLM) explicando o que falta para rodar o radar.
export function missingForRadar(profile: CompanyProfile): string {
  const faltas: string[] = [];
  if (!profile.nome.trim()) faltas.push("o nome da empresa");
  if (!profile.descricao_atividades.trim()) faltas.push("uma descrição do que ela faz");
  if (faltas.length === 0) return "";
  return `Falta ${faltas.join(" e ")} para eu rodar o radar.`;
}
