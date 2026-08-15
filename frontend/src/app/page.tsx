"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { toast } from "sonner";
import { ChatBubble } from "@/components/chat/ChatBubble";
import { ChatMessageList } from "@/components/chat/ChatMessageList";
import { TypingIndicator } from "@/components/chat/TypingIndicator";
import { FrontDoorHeader } from "@/components/frontdoor/FrontDoorHeader";
import { ConversationSidebar } from "@/components/layout/ConversationSidebar";
import { StatusBar } from "@/components/frontdoor/StatusBar";
import { SuggestionChips } from "@/components/frontdoor/SuggestionChips";
import { Composer } from "@/components/frontdoor/Composer";
import { DiffCard } from "@/components/frontdoor/DiffCard";
import { GateCard } from "@/components/frontdoor/GateCard";
import { ProfileIncompleteCard } from "@/components/frontdoor/ProfileIncompleteCard";
import { UrlHero } from "@/components/frontdoor/UrlHero";
import { UnlockCard } from "@/components/frontdoor/UnlockCard";
import { ConsultantJourneyCard } from "@/components/consultant/ConsultantJourneyCard";
import {
  artifactTypeForConsultantPath,
  saveAndConfirmConsultantProject,
} from "@/lib/consultant-project";
import { savePendingConsultantIntent, takePendingConsultantIntent } from "@/lib/pending-consultant-intent";
import {
  consultantTurn,
  openGroundedWriting,
  getMe,
  saveProfile,
  extractProfileFromDocument,
  getConversation,
  getConsultantState,
  updateConsultantBrief,
  confirmConsultantProject,
  selectConsultantPath,
  reassessConsultantPath,
  type ProfileDiffItem,
  type ConsultantJourneyState,
  type ConsultantBriefUpdate,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  CompanyProfile,
  EMPTY_PROFILE,
  loadProfileFromStorage,
  saveProfileToStorage,
} from "@/types/profile";
import {
  applyDiff,
  diffFromProfile,
  diffFromExtracted,
  entriesFromServer,
  profileCompleteness,
  isRadarReady,
  missingForRadar,
  missingHighImpact,
  type TranscriptEntry,
} from "@/types/frontdoor";

// Boas-vindas do assistente (estado vazio). Não faz parte do `history` enviado
// ao backend — é só a abertura da conversa.
const WELCOME =
  "Oi! Me conte o que sua empresa faz — ou explore o que existe de fomento por aí.";

function entriesFromConsultant(state: ConsultantJourneyState): TranscriptEntry[] {
  return state.messages.map((message) => ({
    kind: "msg",
    role: message.role,
    content: message.content,
  }));
}

const SUGGESTIONS = [
  "O que existe de fomento para IA em saúde?",
  "Quais editais estão com prazo aberto?",
  "Como funciona subvenção da FINEP?",
  "Que apoio há para hardware/deep tech?",
];

// Evita re-disparar o merge de perfil em toda visita logada.
const MERGED_FLAG = "frontdoor_merged";

// ── Bolha ─────────────────────────────────────────────────────────────────────
function Bubble({
  role,
  content,
  truncated,
}: {
  role: "user" | "assistant";
  content: string;
  truncated?: boolean;
}) {
  const isUser = role === "user";

  return (
    <ChatBubble
      role={role}
      footer={
        <>
          {!isUser && truncated ? (
            <p className="px-1 mt-1 text-[11px] italic text-content-secondary font-sans">
              Resposta interrompida no limite de passos — continue a conversa para eu retomar.
            </p>
          ) : null}
        </>
      }
    >
      {isUser ? (
        <span className="whitespace-pre-wrap">{content}</span>
      ) : (
        <div className="prose prose-sm max-w-none prose-p:my-1 prose-li:my-0.5 prose-headings:font-semibold prose-headings:text-content-primary">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      )}
    </ChatBubble>
  );
}

// ── Página ──────────────────────────────────────────────────────────────────
export default function FrontDoorPage() {
  const { session, getToken, signOut, loading: authLoading } = useAuth();
  const router = useRouter();
  const isAuthed = !!session;

  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [entries, setEntries] = useState<TranscriptEntry[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // Preview ao vivo do turno em streaming (item 1, TASK 4). null = sem turno
  // em voo OU ainda sem primeiro token (mostra TypingIndicator); string
  // (mesmo vazia) = já recebeu o primeiro token, mostra a bolha parcial.
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  // Conversa persistida no servidor (logado, spec chat-first fase 2). null =
  // ainda sem binding (anônimo, ou logado antes do 1º turno).
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [consultantState, setConsultantState] = useState<ConsultantJourneyState | null>(null);
  const [legacyReadOnly, setLegacyReadOnly] = useState(false);
  // Retomada via sidebar: /?c=<session_id>. Lido uma vez na montagem.
  const [resumeId, setResumeId] = useState<string | null>(null);
  const [isResumeLoading, setIsResumeLoading] = useState(false);
  // Hero de URL (Etapa 1): some quando o usuário extrai um site ou escolhe
  // "prefiro descrever" (cai no chat). Estado de sessão — não persiste.
  const [heroDismissed, setHeroDismissed] = useState(false);
  // Card "destravar mais matches" (Etapa 2): dispensável por sessão.
  const [unlockDismissed, setUnlockDismissed] = useState(false);

  // Liga o estado local à sessão canônica do consultor.
  const bindSession = useCallback(
    (id: string) => {
      if (id !== sessionId) {
        window.dispatchEvent(new Event("conversations:refresh"));
      }
      setSessionId(id);
    },
    [sessionId],
  );

  // Hidrata transcript + perfil local ao montar. Com ?c= na URL, o transcript
  // local NÃO é carregado — a conversa vem do servidor (efeito de retomada).
  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("c");
    if (c) {
      setResumeId(c);
      setIsResumeLoading(true);
    } else {
      setEntries([]);
    }
    setProfile(loadProfileFromStorage() ?? EMPTY_PROFILE);
    setHydrated(true);
  }, []);

  // Retomada de conversa do consultor (com fallback para conversas antigas).
  useEffect(() => {
    if (!hydrated || !resumeId || authLoading) return;
    if (!isAuthed) {
      setConsultantState(null);
      setEntries([]);
      setLegacyReadOnly(false);
      setIsResumeLoading(false);
      return;
    }
    let alive = true;
    setIsResumeLoading(true);
    (async () => {
      try {
        const token = await getToken();
        if (!token || !alive) return;
        try {
          const detail = await getConsultantState(resumeId, token);
          if (!alive) return;
          setConsultantState(detail.state);
          setEntries(entriesFromConsultant(detail.state));
          setLegacyReadOnly(false);
          bindSession(detail.conversation_id);
        } catch {
          const detail = await getConversation(resumeId, token);
          if (!alive) return;
          setConsultantState(null);
          setEntries(entriesFromServer(detail.entries));
          setLegacyReadOnly(true);
          bindSession(detail.session_id);
        }
      } catch {
        if (alive) toast.error("Não consegui retomar esta conversa.");
      } finally {
        if (alive) setIsResumeLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [hydrated, isAuthed, resumeId, authLoading, getToken, bindSession]);

  // Recarregar a home sem ?c= também recupera a sessão do consultor vinculada
  // em memória. Conversas antigas nunca são promovidas a uma nova sessão.
  useEffect(() => {
    if (!hydrated || !isAuthed || resumeId || !sessionId) return;
    let alive = true;
    (async () => {
      try {
        const token = await getToken();
        if (!token) return;
        const detail = await getConsultantState(sessionId, token);
        if (!alive) return;
        setConsultantState(detail.state);
        setEntries(entriesFromConsultant(detail.state));
        setLegacyReadOnly(false);
      } catch {
        // Sessões legacy continuam sendo carregadas apenas quando abertas por ?c=.
      }
    })();
    return () => {
      alive = false;
    };
  }, [hydrated, isAuthed, resumeId, sessionId, getToken]);

  // ── Perfil persistido (anônimo: localStorage; logado: PUT /me/profile) ──────
  const persistProfile = useCallback(
    async (next: CompanyProfile) => {
      setProfile(next);
      saveProfileToStorage(next); // sempre mantém o espelho local
      if (isAuthed) {
        try {
          const token = await getToken();
          if (token) await saveProfile(next, token);
        } catch {
          toast.error("Não consegui salvar o perfil na sua conta.");
        }
      }
    },
    [isAuthed, getToken],
  );

  // ── Hidratação do perfil logado + merge da conversa ────────────────────────
  useEffect(() => {
    if (!hydrated || !isAuthed) return;
    let alive = true;
    (async () => {
      const token = await getToken();
      if (!token || !alive) return;
      try {
        const me = await getMe(token);
        const account = { ...EMPTY_PROFILE, ...(me.profile ?? {}) } as CompanyProfile;
        const local = loadProfileFromStorage();
        const alreadyMerged =
          window.localStorage.getItem(MERGED_FLAG) === me.workspace_id;

        // Sem perfil local relevante OU já mergeado nesta conta → só hidrata.
        if (!local || !local.nome || alreadyMerged) {
          if (alive) setProfile(account);
          saveProfileToStorage(account);
          return;
        }

        // Merge: campos vazios na conta ← valores locais; conflitos → diff.
        const filled: CompanyProfile = { ...account };
        const conflicts: ProfileDiffItem[] = [];
        let importedSilently = false;
        (Object.keys(EMPTY_PROFILE) as (keyof CompanyProfile)[]).forEach((f) => {
          const a = account[f];
          const l = local[f];
          const aEmpty = a === "" || a === null || (Array.isArray(a) && a.length === 0);
          const lEmpty = l === "" || l === null || (Array.isArray(l) && l.length === 0);
          if (lEmpty) return;
          if (aEmpty) {
            (filled as unknown as Record<string, unknown>)[f] = l;
            importedSilently = true;
          } else if (JSON.stringify(a) !== JSON.stringify(l)) {
            conflicts.push({ field: f, label: f, old: a, new: l });
          }
        });

        if (!alive) return;
        setProfile(filled);
        saveProfileToStorage(filled);
        if (importedSilently && token) {
          try {
            await saveProfile(filled, token);
            toast.success("Perfil da conversa importado.");
          } catch {
            /* silencioso — espelho local mantém o estado */
          }
        }
        if (conflicts.length > 0) {
          setEntries((prev) => [
            ...prev,
            { kind: "diff", items: conflicts, status: "pending", origin: "merge" },
          ]);
        }
        window.localStorage.setItem(MERGED_FLAG, me.workspace_id);
      } catch {
        // sem perfil de conta acessível — segue com o local.
      }
    })();
    return () => {
      alive = false;
    };
  }, [hydrated, isAuthed, getToken]);

  // Uma entrada de diff só pode ser decidida uma vez. O WeakSet mantém o
  // bloqueio mesmo se o primeiro aceite terminar antes do React re-renderizar o
  // card como aplicado (caso típico de clique duplo em fluxo anônimo).
  const decidedDiffEntries = useRef(new WeakSet<object>());
  const radarReadyMessage =
    "Perfil atualizado. Volte ao consultor para continuar formando o brief do seu projeto.";
  const appendRadarReadyMessage = useCallback(() => {
    setEntries((prev) => {
      const last = prev[prev.length - 1];
      if (last?.kind === "msg" && last.role === "assistant" && last.content === radarReadyMessage) return prev;
      return [...prev, { kind: "msg", role: "assistant", content: radarReadyMessage }];
    });
  }, []);

  // ── Turno de conversa ───────────────────────────────────────────────────────
  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      if (!isAuthed) {
        savePendingConsultantIntent(window.localStorage, trimmed);
        router.push("/login");
        return;
      }
      if (legacyReadOnly) {
        toast.message("Esta conversa antiga é somente leitura. Inicie uma nova jornada com o consultor.");
        return;
      }

      const userEntry: TranscriptEntry = { kind: "msg", role: "user", content: trimmed };
      const withUser = [...entries, userEntry];
      setEntries(withUser);
      setInput("");
      setSending(true);

      const applyConsultantResult = (payload: import("@/lib/api").ConsultantTurnResult) => {
        bindSession(payload.conversation_id);
        setConsultantState(payload.state);
        setEntries(entriesFromConsultant(payload.state));
      };

      try {
        const payload = await consultantTurn(
          trimmed,
          sessionId,
          crypto.randomUUID(),
          consultantState?.revision,
        );
        applyConsultantResult(payload);
      } catch (e) {
        setEntries((prev) => prev.filter((m) => m !== userEntry));
        setInput(trimmed);
        toast.error(
          e instanceof Error ? e.message : "Não consegui falar com o servidor. Tente novamente.",
        );
      } finally {
        setStreamingText(null);
        setSending(false);
      }
    },
    [entries, sending, sessionId, consultantState?.revision, bindSession, isAuthed, legacyReadOnly, router],
  );

  useEffect(() => {
    if (!hydrated || !isAuthed || sending || legacyReadOnly) return;
    const pendingIntent = takePendingConsultantIntent(window.localStorage);
    if (pendingIntent) void send(pendingIntent);
  }, [hydrated, isAuthed, sending, legacyReadOnly, send]);

  const saveAndConfirmProject = useCallback(async (updates: ConsultantBriefUpdate) => {
    if (!sessionId || !consultantState) return;
    const token = await getToken();
    if (!token) return;

    let stage: "save" | "confirm" = Object.keys(updates).length > 0 ? "save" : "confirm";
    try {
      await saveAndConfirmConsultantProject({
        updates,
        revision: consultantState.revision,
        confirmation: {
          saveBrief: async (briefUpdates, expectedRevision) => {
            const saved = await updateConsultantBrief(sessionId, expectedRevision, briefUpdates, token);
            stage = "confirm";
            setConsultantState(saved.state);
            setEntries(entriesFromConsultant(saved.state));
            return { revision: saved.state.revision };
          },
          confirmProject: async (expectedRevision) => {
            const payload = await confirmConsultantProject(sessionId, expectedRevision, token);
            setConsultantState(payload.state);
            setEntries(entriesFromConsultant(payload.state));
          },
        },
      });
    } catch (cause) {
      toast.error(
        cause instanceof Error
          ? cause.message
          : stage === "save"
            ? "Não consegui salvar o brief."
            : "Não consegui confirmar o projeto.",
      );
    }
  }, [consultantState, getToken, sessionId]);

  const selectPath = useCallback(async (pathId: string, reason: string) => {
    if (!sessionId || !consultantState) return;
    const token = await getToken();
    if (!token) return;
    try {
      const payload = await selectConsultantPath(sessionId, pathId, consultantState.revision, reason, token);
      setConsultantState(payload.state);
      toast.success("Caminho registrado para aprofundamento.");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Não consegui registrar o caminho.");
    }
  }, [consultantState, getToken, sessionId]);

  const reassessPath = useCallback(async (pathId: string, reason: string) => {
    if (!sessionId || !consultantState) return;
    const token = await getToken();
    if (!token) return;
    try {
      const payload = await reassessConsultantPath(sessionId, pathId, consultantState.revision, reason, token);
      setConsultantState(payload.state);
      setEntries(entriesFromConsultant(payload.state));
      toast.success("Caminho marcado para reavaliação.");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Não consegui pedir a reavaliação.");
    }
  }, [consultantState, getToken, sessionId]);

  const openWriting = useCallback(async (pathId: string) => {
    if (!sessionId) return;
    try {
      const path = consultantState?.paths.find((item) => item.id === pathId);
      const artifactType = artifactTypeForConsultantPath(path ?? {});
      const result = await openGroundedWriting(sessionId, pathId, artifactType);
      router.push(`/workspace/${result.writing_session_id}`);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Não consegui abrir a proposta.");
    }
  }, [consultantState, router, sessionId]);

  // ── Aceite/descarte de um diff (por índice no transcript) ───────────────────
  const decideDiff = useCallback(
    async (index: number, accepted: boolean, finalItems?: ProfileDiffItem[]) => {
      const entry = entries[index];
      if (!entry || entry.kind !== "diff" || decidedDiffEntries.current.has(entry)) return;
      decidedDiffEntries.current.add(entry);

      try {
        setEntries((prev) =>
          prev.map((e, i) =>
            i === index && e.kind === "diff"
              ? { ...e, status: accepted ? "accepted" : "dismissed", items: finalItems ?? e.items }
              : e,
          ),
        );

        if (!accepted) return;

        const next = applyDiff(profile, finalItems ?? entry.items);
        await persistProfile(next);

        // O Radar é a superfície de resultados. Não disparamos um match oculto
        // no chat: ele produzia narrativa sem os cards e podia duplicar respostas.
        if (isRadarReady(next)) {
          appendRadarReadyMessage();
        } else {
          const msg = missingForRadar(next);
          if (msg) {
            setEntries((prev) => [...prev, { kind: "msg", role: "assistant", content: msg }]);
          }
        }
      } finally {
        // O bloqueio é permanente para esta entrada; uma nova conversa cria
        // novas entradas e pode ser decidida normalmente.
      }
    },
    [entries, profile, persistProfile, appendRadarReadyMessage],
  );

  // ── Barra de status: editar perfil (diff manual com todos os campos) ────────
  const handleEditProfile = useCallback(() => {
    setEntries((prev) => [
      ...prev,
      { kind: "diff", items: diffFromProfile(profile), status: "pending", origin: "manual" },
    ]);
  }, [profile]);

  // ── Etapa 2: aplicar campos do UnlockCard → persiste + orienta ao Radar ────
  const handleUnlockApply = useCallback(
    async (updates: Partial<CompanyProfile>) => {
      const next = { ...profile, ...updates } as CompanyProfile;
      await persistProfile(next);
      appendRadarReadyMessage();
    },
    [profile, persistProfile, appendRadarReadyMessage],
  );

  // ── Anexo (📎) ──────────────────────────────────────────────────────────────
  const handleAttachClick = useCallback((): boolean | void => {
    if (!isAuthed) {
      setEntries((prev) => [...prev, { kind: "gate", action: "anexo" }]);
      return true; // já tratado (gate) — não abre o seletor
    }
    return false; // logado → deixa o Composer abrir o file picker
  }, [isAuthed]);

  // ── Hero de URL (Etapa 1): a extração vira um diff de revisão ───────────────
  // "AI drafts, humans decide": não aplica nada — empilha um DiffCard que o
  // usuário revisa/edita; o aceite (decideDiff) persiste e dispara o radar.
  const handleExtractResult = useCallback(
    (extracted: CompanyProfile, lowConfidence: boolean) => {
      setHeroDismissed(true);
      const items = diffFromExtracted(profile, extracted);
      setEntries((prev) => {
        if (items.length === 0) {
          return [
            ...prev,
            {
              kind: "msg",
              role: "assistant",
              content:
                "Não consegui extrair dados desse site. Me conta o que sua empresa faz que eu monto o perfil com você.",
            },
          ];
        }
        const next: TranscriptEntry[] = [...prev];
        if (lowConfidence) {
          next.push({
            kind: "msg",
            role: "assistant",
            content:
              "Achei pouca coisa no site — revise o rascunho abaixo e complemente o que faltar.",
          });
        }
        next.push({ kind: "diff", items, status: "pending", origin: "extract" });
        return next;
      });
    },
    [profile],
  );

  const handlePickFile = useCallback(
    async (file: File) => {
      const t = toast.loading("Lendo o documento…");
      try {
        const res = await extractProfileFromDocument(file);
        // Monta um diff (old = perfil atual) só com os campos preenchidos.
        const items = diffFromExtracted(profile, res.profile);
        toast.dismiss(t);
        if (items.length === 0) {
          toast.message("Não encontrei dados de perfil no documento.");
          return;
        }
        setEntries((prev) => [
          ...prev,
          { kind: "diff", items, status: "pending", origin: "document" },
        ]);
      } catch (e) {
        toast.dismiss(t);
        toast.error(e instanceof Error ? e.message : "Falha ao ler o documento.");
      }
    },
    [profile],
  );

  // Zera transcript local + binding com o servidor. A conversa persistida NÃO
  // é apagada (continua no histórico do sidebar) — só desligamos dela.
  const resetConversation = useCallback(() => {
    setEntries([]);
    setConsultantState(null);
    setLegacyReadOnly(false);
    setInput("");
    setSessionId(null);
    setResumeId(null);
    setIsResumeLoading(false);
    // Derruba um eventual ?c= da URL sem remontar a página.
    if (window.location.search) {
      window.history.replaceState(null, "", "/");
    }
  }, []);

  const handleReset = useCallback(() => {
    resetConversation();
    toast.success("Conversa reiniciada.");
  }, [resetConversation]);

  // "Nova conversa" da ConversationSidebar: quando já estamos em "/", a página
  // não remonta, então a sidebar dispara o evento e zeramos o estado em memória.
  useEffect(() => {
    window.addEventListener("consultant:new", resetConversation);
    return () => window.removeEventListener("consultant:new", resetConversation);
  }, [resetConversation]);

  // Retomada disparada pelo sidebar quando já estamos em "/".
  useEffect(() => {
    function onResume(ev: Event) {
      const id = (ev as CustomEvent<string>).detail;
      if (!id) return;
      resetConversation();
      setIsResumeLoading(true);
      setResumeId(id);
      window.history.replaceState(null, "", `/?c=${encodeURIComponent(id)}`);
    }
    window.addEventListener("consultant:resume", onResume);
    return () => window.removeEventListener("consultant:resume", onResume);
  }, [resetConversation]);

  const isEmpty = hydrated && !isResumeLoading && entries.length === 0;
  const hasConversationContext = hydrated && !isResumeLoading && entries.length > 0;
  // Hero de URL na 1ª tela (Etapa 1): só com perfil ainda não-rodável, conversa
  // vazia e antes de o usuário optar por descrever. Some ao extrair ou pular.
  const heroActive = isEmpty && !heroDismissed && !isRadarReady(profile);
  const completeness = profileCompleteness(profile);

  // Gaps de alto impacto (Etapa 2). Profile-only pós-Sprint 3 (o radar saiu): só
  // nudge quando o perfil já é rodável. Some sozinho quando os campos são preenchidos.
  const gaps = isRadarReady(profile) ? missingHighImpact(profile) : [];
  const showUnlock = hasConversationContext && gaps.length > 0 && !unlockDismissed;

  return (
    <div className="flex h-[100dvh] bg-app-bg">
      <div className="hidden md:flex">
        <ConversationSidebar />
      </div>
      <div className="flex flex-1 flex-col min-w-0">
        <>
        <FrontDoorHeader isAuthed={isAuthed} onReset={handleReset} onSignOut={signOut} />
      <StatusBar
        completeness={completeness}
        onEditProfile={handleEditProfile}
      />

      <ChatMessageList className="mx-auto w-full max-w-2xl" deps={[sending, hydrated]}>
        <Bubble role="assistant" content={WELCOME} />

        {legacyReadOnly && (
          <div className="mx-1 mb-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
            Esta é uma conversa antiga, disponível somente para leitura. Inicie uma nova jornada para continuar com o ConsultantGraph.
          </div>
        )}

        {heroActive && (
          <div className="pt-2">
            <UrlHero
              onResult={handleExtractResult}
              onSkip={() => setHeroDismissed(true)}
            />
          </div>
        )}

        {isEmpty && !heroActive && (
          <div className="pt-1">
            <SuggestionChips
              suggestions={SUGGESTIONS}
              onPick={(s) => void send(s)}
              disabled={sending}
            />
          </div>
        )}

        {entries.map((entry, i) => {
          switch (entry.kind) {
            case "msg":
              return (
                <Bubble
                  key={i}
                  role={entry.role}
                  content={entry.content}
                  truncated={entry.truncated}
                />
              );
            case "diff":
              if (legacyReadOnly) {
                return (
                  <div key={i} className="mx-1 rounded-xl border border-border bg-surface p-3 text-xs text-content-secondary">
                    Rascunho de perfil registrado na conversa antiga (somente leitura).
                  </div>
                );
              }
              return (
                <DiffCard
                  key={i}
                  items={entry.items}
                  status={entry.status}
                  origin={entry.origin}
                  onAccept={(finalItems) => void decideDiff(i, true, finalItems)}
                  onDismiss={() => void decideDiff(i, false)}
                />
              );
            case "gate":
              return <GateCard key={i} action={entry.action} />;
            case "profile_incomplete":
              return <ProfileIncompleteCard key={i} missingFields={entry.missingFields} />;
            case "radar": {
              const total = entry.matchedEditais.length + entry.matchedEntities.length;
              if (total === 0) return null;
              return (
                <div key={i} className="px-4 pb-2 pt-1">
                  <div className="rounded-xl border border-primary/25 bg-primary/5 p-4">
                    <p className="text-sm font-semibold text-content-primary">
                      O Consultor encontrou {total} {total === 1 ? "aderência" : "aderências"}
                    </p>
                    <p className="mt-1 text-sm text-content-secondary">
                      Avalie evidências, elegibilidade, filtros e comparação para cada caminho.
                    </p>
                  </div>
                </div>
              );
            }
            default:
              return null;
          }
        })}

        {consultantState && (
          <ConsultantJourneyCard
            state={consultantState}
            onSaveAndConfirm={saveAndConfirmProject}
            onSelect={selectPath}
            onReassess={reassessPath}
            onOpenWriting={openWriting}
          />
        )}

        {sending && streamingText ? (
          // Preview ao vivo (item 1, TASK 4) — substituído sem glitch pelo
          // "done" autoritativo assim que o turno termina (applyTurnResult
          // empurra a entrada final em `entries` e limpa este state no mesmo
          // tick; nunca aparecem os dois ao mesmo tempo).
          <Bubble role="assistant" content={streamingText} />
        ) : sending ? (
          <div className="flex items-start">
            <TypingIndicator />
          </div>
        ) : null}
      </ChatMessageList>

        {showUnlock && (
          <div className="mx-auto w-full max-w-2xl px-1 pb-2">
            <UnlockCard
              gaps={gaps}
              onApply={handleUnlockApply}
              onDismiss={() => setUnlockDismissed(true)}
            />
          </div>
        )}

        <Composer
          value={input}
          onChange={setInput}
          onSend={() => void send(input)}
          onAttach={hasConversationContext ? handleAttachClick : undefined}
          onPickFile={hasConversationContext ? handlePickFile : undefined}
          disabled={sending || legacyReadOnly}
          placeholder={legacyReadOnly ? "Conversa antiga somente leitura" : "Conte sua intenção para o consultor…"}
        />
        </>
      </div>
    </div>
  );
}
