// Transcript tipado do front-door (M2+). O transcript deixou de ser uma lista
// de {role, content} (M1) e passa a ser uma lista de ENTRADAS heterogêneas:
// mensagens, cards de diff de perfil, cards de radar e gates de login. Persiste
// em sessionStorage `frontdoor_history` v2 (conversa por visita/aba) — `migrateHistory` converte o formato
// antigo (array de {role,content}) em entradas `msg`, sem descartar nada.

import type { CompanyProfile } from "./profile";
import { EMPTY_PROFILE } from "./profile";
import type { ProfileDiffItem, RadarResponse } from "@/lib/api";

export type ChatRole = "user" | "assistant";

// Status de decisão de um card de diff: pendente até o usuário aceitar/descartar.
export type DiffStatus = "pending" | "accepted" | "dismissed";

// Ações que exigem conta (gate de login) — texto do card varia por ação.
export type GateAction = "anexo" | "brief" | "proposta";

export interface MsgEntry {
  kind: "msg";
  role: ChatRole;
  content: string;
}

export interface DiffEntry {
  kind: "diff";
  items: ProfileDiffItem[];
  status: DiffStatus;
  // Origem do diff: "turn" (proposta do LLM no turno), "document" (extração de
  // anexo), "merge" (conflito conta vs. conversa no login), "manual" (editar
  // perfil pela barra de status). Só muda a copy/título do card.
  origin?: "turn" | "document" | "merge" | "manual";
}

export interface RadarEntry {
  kind: "radar";
  data: RadarResponse;
  ts: number;
}

export interface GateEntry {
  kind: "gate";
  action: GateAction;
}

export type TranscriptEntry = MsgEntry | DiffEntry | RadarEntry | GateEntry;

// ── Persistência (sessionStorage v2) ──────────────────────────────────────────
export const HISTORY_KEY = "frontdoor_history";

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
          });
        }
        break;
      case "radar":
        if (e.data && typeof e.data === "object") {
          out.push({
            kind: "radar",
            data: e.data as RadarResponse,
            ts: typeof e.ts === "number" ? e.ts : Date.now(),
          });
        }
        break;
      case "gate":
        if (e.action === "anexo" || e.action === "brief" || e.action === "proposta") {
          out.push({ kind: "gate", action: e.action });
        }
        break;
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

// Constrói um diff "manual" com TODOS os campos atuais do perfil (para o
// "editar perfil" da barra de status reusar o DiffCard em modo edição).
export function diffFromProfile(profile: CompanyProfile): ProfileDiffItem[] {
  const LABELS: Partial<Record<keyof CompanyProfile, string>> = {
    nome: "Nome",
    tipo_entidade: "Tipo de entidade",
    one_liner: "Proposta (one-liner)",
    solution_summary: "Resumo da solução",
    descricao_atividades: "Descrição das atividades",
    tamanho_empresa: "Porte",
    trl: "TRL",
    uf: "UF",
    ano_fundacao: "Ano de fundação",
    faturamento_anual: "Faturamento anual",
    tipos_financiamento_interesse: "Interesse de financiamento",
    estagio: "Estágio",
    mrr_arr: "MRR/ARR",
    round_alvo_brl: "Round alvo (R$)",
  };
  return (Object.keys(LABELS) as (keyof CompanyProfile)[]).map((field) => ({
    field,
    label: LABELS[field] ?? field,
    old: profile[field],
    new: profile[field],
  }));
}

// Mensagem local (sem LLM) explicando o que falta para rodar o radar.
export function missingForRadar(profile: CompanyProfile): string {
  const faltas: string[] = [];
  if (!profile.nome.trim()) faltas.push("o nome da empresa");
  if (!profile.descricao_atividades.trim()) faltas.push("uma descrição do que ela faz");
  if (faltas.length === 0) return "";
  return `Falta ${faltas.join(" e ")} para eu rodar o radar.`;
}
