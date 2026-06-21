"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
import { RadarCard } from "@/components/frontdoor/RadarCard";
import { GateCard } from "@/components/frontdoor/GateCard";
import { UrlHero } from "@/components/frontdoor/UrlHero";
import { UnlockCard } from "@/components/frontdoor/UnlockCard";
import {
  frontdoorTurn,
  getRadar,
  getMe,
  saveProfile,
  getOpportunityBrief,
  extractProfileFromDocument,
  getConversation,
  appendConversationEntry,
  updateConversationEntry,
  type ProfileDiffItem,
  type OpportunityBrief,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { resolveWorkspaceSession } from "@/lib/workspace";
import { useRouter } from "next/navigation";
import {
  CompanyProfile,
  EMPTY_PROFILE,
  loadProfileFromStorage,
  saveProfileToStorage,
} from "@/types/profile";
import {
  HISTORY_KEY,
  SESSION_ID_KEY,
  migrateHistory,
  toApiHistory,
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

const SUGGESTIONS = [
  "O que existe de fomento para IA em saúde?",
  "Quais editais estão com prazo aberto?",
  "Como funciona subvenção da FINEP?",
  "Que apoio há para hardware/deep tech?",
];

// Flag p/ não re-disparar o merge de perfil (F5) em toda visita logada.
const MERGED_FLAG = "frontdoor_merged";

// Transcript vive em sessionStorage (decisão 2026-06-11): nova visita/aba =
// conversa limpa; F5 na mesma aba preserva. O PERFIL continua em localStorage
// (é o ativo durável). Limpa resíduo da era localStorage do transcript.
function loadHistory(): TranscriptEntry[] {
  if (typeof window === "undefined") return [];
  try {
    window.localStorage.removeItem(HISTORY_KEY);
    const raw = window.sessionStorage.getItem(HISTORY_KEY);
    return raw ? migrateHistory(JSON.parse(raw)) : [];
  } catch {
    return [];
  }
}

// ── Bolha ─────────────────────────────────────────────────────────────────────
function Bubble({ role, content }: { role: "user" | "assistant"; content: string }) {
  const isUser = role === "user";
  return (
    <ChatBubble role={role}>
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
  const { session, getToken, signOut } = useAuth();
  const isAuthed = !!session;
  const router = useRouter();

  const [profile, setProfile] = useState<CompanyProfile>(EMPTY_PROFILE);
  const [entries, setEntries] = useState<TranscriptEntry[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  // Briefs gerados, chaveados por edital_id (para render inline no RadarCard).
  const [briefs, setBriefs] = useState<Record<string, OpportunityBrief>>({});
  // Conversa persistida no servidor (logado, spec chat-first fase 2). null =
  // ainda sem binding (anônimo, ou logado antes do 1º turno).
  const [sessionId, setSessionId] = useState<string | null>(null);
  // Retomada via sidebar: /?c=<session_id>. Lido uma vez na montagem.
  const [resumeId, setResumeId] = useState<string | null>(null);
  // Hero de URL (Etapa 1): some quando o usuário extrai um site ou escolhe
  // "prefiro descrever" (cai no chat). Estado de sessão — não persiste.
  const [heroDismissed, setHeroDismissed] = useState(false);
  // Card "destravar mais matches" (Etapa 2): dispensável por sessão.
  const [unlockDismissed, setUnlockDismissed] = useState(false);

  const lastRadarRef = useRef<HTMLDivElement>(null);

  // Liga o estado local à conversa do servidor (e sobrevive a F5 na mesma aba).
  // Binding novo (1º turno) avisa o sidebar para recarregar a lista.
  const bindSession = useCallback(
    (id: string) => {
      if (id !== sessionId) {
        window.dispatchEvent(new Event("conversations:refresh"));
      }
      setSessionId(id);
      try {
        window.sessionStorage.setItem(SESSION_ID_KEY, id);
      } catch {
        /* quota/modo privado — segue só em memória */
      }
    },
    [sessionId],
  );

  // Hidrata transcript + perfil local ao montar. Com ?c= na URL, o transcript
  // local NÃO é carregado — a conversa vem do servidor (efeito de retomada).
  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("c");
    if (c) {
      setResumeId(c);
    } else {
      setEntries(loadHistory());
      try {
        setSessionId(window.sessionStorage.getItem(SESSION_ID_KEY));
      } catch {
        /* noop */
      }
    }
    setProfile(loadProfileFromStorage() ?? EMPTY_PROFILE);
    setHydrated(true);
  }, []);

  // Retomada de conversa frontdoor (logado): carrega o transcript do servidor.
  // Espera o auth resolver — se o usuário for mesmo anônimo, o efeito nunca
  // dispara e a home fica como conversa nova.
  useEffect(() => {
    if (!hydrated || !isAuthed || !resumeId) return;
    let alive = true;
    (async () => {
      try {
        const token = await getToken();
        if (!token || !alive) return;
        const detail = await getConversation(resumeId, token);
        if (!alive) return;
        setEntries(entriesFromServer(detail.entries));
        bindSession(detail.session_id);
      } catch {
        if (alive) toast.error("Não consegui retomar esta conversa.");
      }
    })();
    return () => {
      alive = false;
    };
  }, [hydrated, isAuthed, resumeId, getToken, bindSession]);

  // Persiste transcript a cada mudança (depois de hidratar).
  useEffect(() => {
    if (!hydrated) return;
    try {
      window.sessionStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
    } catch {
      // quota/modo privado — segue em memória.
    }
  }, [entries, hydrated]);

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

  // ── F5/F6: hidratação do perfil logado + merge da conversa ──────────────────
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

        // Merge F5: campos vazios na conta ← valores locais; conflitos → diff.
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

  // ── Disparo do radar (após perfil completo) ─────────────────────────────────
  const runRadar = useCallback(
    async (p: CompanyProfile) => {
      try {
        const data = await getRadar(p, 10);
        const entry: TranscriptEntry = { kind: "radar", data, ts: Date.now() };
        setEntries((prev) => [...prev, entry]);
        // Logado com conversa persistida: espelha o card no servidor (POST
        // /conversations/{id}/entries). Falha não bloqueia a UX — a conversa
        // vale mais que o histórico (spec fase 2).
        if (isAuthed && sessionId) {
          try {
            const token = await getToken();
            if (token) {
              const saved = await appendConversationEntry(
                sessionId,
                { entry_kind: "radar", payload: { data, ts: entry.ts } },
                token,
              );
              setEntries((prev) =>
                prev.map((e) => (e === entry ? { ...e, entryId: saved.id } : e)),
              );
            }
          } catch (err) {
            console.warn("Falha ao persistir card de radar:", err);
          }
        }
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Não consegui rodar o radar.");
      }
    },
    [isAuthed, sessionId, getToken],
  );

  // ── Turno de conversa ───────────────────────────────────────────────────────
  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;

      const userEntry: TranscriptEntry = { kind: "msg", role: "user", content: trimmed };
      const withUser = [...entries, userEntry];
      setEntries(withUser);
      setInput("");
      setSending(true);

      try {
        const { answer, profile_diff, session_id, entry_ids } = await frontdoorTurn(
          trimmed,
          toApiHistory(withUser),
          profile.nome ? profile : null,
          sessionId,
        );
        // Logado: o backend persistiu o turno e devolveu o binding da conversa
        // (1º turno cria; seguintes reusam). Anônimo: session_id ausente.
        if (session_id) bindSession(session_id);
        setEntries((prev) => {
          const next: TranscriptEntry[] = [
            ...prev,
            { kind: "msg", role: "assistant", content: answer },
          ];
          if (profile_diff && profile_diff.length > 0) {
            next.push({
              kind: "diff",
              items: profile_diff,
              status: "pending",
              origin: "turn",
              // Alvo do PATCH no aceite/descarte (persistido junto ao turno).
              entryId: entry_ids?.diff ?? undefined,
            });
          }
          return next;
        });
      } catch (e) {
        setEntries((prev) => prev.filter((m) => m !== userEntry));
        setInput(trimmed);
        toast.error(
          e instanceof Error ? e.message : "Não consegui falar com o servidor. Tente novamente.",
        );
      } finally {
        setSending(false);
      }
    },
    [entries, sending, profile, sessionId, bindSession],
  );

  // ── Aceite/descarte de um diff (por índice no transcript) ───────────────────
  const decideDiff = useCallback(
    async (index: number, accepted: boolean, finalItems?: ProfileDiffItem[]) => {
      const entry = entries[index];
      if (!entry || entry.kind !== "diff") return;

      setEntries((prev) =>
        prev.map((e, i) =>
          i === index && e.kind === "diff"
            ? { ...e, status: accepted ? "accepted" : "dismissed", items: finalItems ?? e.items }
            : e,
        ),
      );

      // Espelha a decisão no servidor quando a entrada está persistida (PATCH
      // payload — status pending→accepted/dismissed). Fire-and-forget: falha
      // não bloqueia o aceite local.
      if (isAuthed && sessionId && entry.entryId) {
        void (async () => {
          try {
            const token = await getToken();
            if (token) {
              await updateConversationEntry(
                sessionId,
                entry.entryId!,
                {
                  items: finalItems ?? entry.items,
                  status: accepted ? "accepted" : "dismissed",
                  origin: entry.origin ?? "turn",
                },
                token,
              );
            }
          } catch (err) {
            console.warn("Falha ao persistir decisão do diff:", err);
          }
        })();
      }

      if (!accepted) return;

      const next = applyDiff(profile, finalItems ?? entry.items);
      await persistProfile(next);

      // Aceite é o trigger do radar (D4) — se o perfil ficou rodável.
      if (isRadarReady(next)) {
        await runRadar(next);
      } else {
        const msg = missingForRadar(next);
        if (msg) {
          setEntries((prev) => [...prev, { kind: "msg", role: "assistant", content: msg }]);
        }
      }
    },
    [entries, profile, persistProfile, runRadar, isAuthed, sessionId, getToken],
  );

  // ── Barra de status: editar perfil (diff manual com todos os campos) ────────
  const handleEditProfile = useCallback(() => {
    setEntries((prev) => [
      ...prev,
      { kind: "diff", items: diffFromProfile(profile), status: "pending", origin: "manual" },
    ]);
  }, [profile]);

  const handleSeeRadar = useCallback(() => {
    lastRadarRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // ── Etapa 2: aplicar campos do UnlockCard → persiste + re-roda o radar ───────
  const handleUnlockApply = useCallback(
    async (updates: Partial<CompanyProfile>) => {
      const next = { ...profile, ...updates } as CompanyProfile;
      await persistProfile(next);
      await runRadar(next);
    },
    [profile, persistProfile, runRadar],
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

  // ── Brief GO/NO-GO (logado) / gate (anônimo) ────────────────────────────────
  const handleBrief = useCallback(
    async (editalId: string) => {
      if (!isAuthed) {
        setEntries((prev) => [...prev, { kind: "gate", action: "brief" }]);
        return;
      }
      try {
        const brief = await getOpportunityBrief(editalId, profile.nome ? profile : null);
        setBriefs((prev) => ({ ...prev, [editalId]: brief }));
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Não consegui gerar o brief.");
      }
    },
    [isAuthed, profile],
  );

  // ── Começar proposta / Escrever pitch: logado → cria sessão e abre o
  // workspace; anônimo → gate. O pitch usa mode='pitch' (id `investidor:`).
  const openWorkspace = useCallback(
    async (editalId: string, mode?: "proposal" | "pitch") => {
      if (!isAuthed) {
        setEntries((prev) => [...prev, { kind: "gate", action: "proposta" }]);
        return;
      }
      const t = toast.loading(mode === "pitch" ? "Abrindo pitch…" : "Abrindo proposta…");
      try {
        const token = await getToken();
        if (!token) {
          setEntries((prev) => [...prev, { kind: "gate", action: "proposta" }]);
          return;
        }
        const sessionId = await resolveWorkspaceSession(editalId, profile, token, mode);
        router.push(`/workspace/${sessionId}`);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Não foi possível abrir o workspace.");
      } finally {
        toast.dismiss(t);
      }
    },
    [isAuthed, getToken, profile, router],
  );

  const handleProposta = useCallback(
    (editalId: string) => void openWorkspace(editalId, "proposal"),
    [openWorkspace],
  );

  const handlePitch = useCallback(
    (investidorId: string) => void openWorkspace(investidorId, "pitch"),
    [openWorkspace],
  );

  // Zera transcript local + binding com o servidor. A conversa persistida NÃO
  // é apagada (continua no histórico do sidebar) — só desligamos dela.
  const resetConversation = useCallback(() => {
    try {
      window.sessionStorage.removeItem(HISTORY_KEY);
      window.sessionStorage.removeItem(SESSION_ID_KEY);
    } catch {
      /* noop */
    }
    setEntries([]);
    setInput("");
    setBriefs({});
    setSessionId(null);
    setResumeId(null);
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
  // não remonta, então a sidebar dispara `frontdoor:new` (já tendo limpado o
  // sessionStorage) e nós zeramos o estado em memória aqui.
  useEffect(() => {
    window.addEventListener("frontdoor:new", resetConversation);
    return () => window.removeEventListener("frontdoor:new", resetConversation);
  }, [resetConversation]);

  // Retomada disparada pelo sidebar quando JÁ estamos em "/" (clicar num Link
  // /?c=... não remonta a página, então o ?c= lido na montagem não muda).
  useEffect(() => {
    function onResume(ev: Event) {
      const id = (ev as CustomEvent<string>).detail;
      if (!id) return;
      resetConversation();
      setResumeId(id);
      window.history.replaceState(null, "", `/?c=${encodeURIComponent(id)}`);
    }
    window.addEventListener("frontdoor:resume", onResume);
    return () => window.removeEventListener("frontdoor:resume", onResume);
  }, [resetConversation]);

  const isEmpty = hydrated && entries.length === 0;
  // Hero de URL na 1ª tela (Etapa 1): só com perfil ainda não-rodável, conversa
  // vazia e antes de o usuário optar por descrever. Some ao extrair ou pular.
  const heroActive = isEmpty && !heroDismissed && !isRadarReady(profile);
  const completeness = profileCompleteness(profile);
  const hasRadar = entries.some((e) => e.kind === "radar");

  // Índice do último card de radar (para ancorar o ref de "ver radar atual").
  let lastRadarIndex = -1;
  entries.forEach((e, i) => {
    if (e.kind === "radar") lastRadarIndex = i;
  });

  // Gaps de alto impacto sobre o radar mais recente (Etapa 2). Recomputa a cada
  // mudança de perfil — some sozinho quando os campos são preenchidos.
  const lastRadar = lastRadarIndex >= 0 ? entries[lastRadarIndex] : null;
  const lastRadarData = lastRadar?.kind === "radar" ? lastRadar.data : null;
  const gaps = lastRadarData ? missingHighImpact(profile, lastRadarData) : [];
  const showUnlock = gaps.length > 0 && !unlockDismissed;

  return (
    // A home é a tela principal do chat — o sidebar de conversas vive aqui
    // também (não só nas páginas com DashboardLayout). Oculto em telas pequenas,
    // como nos apps de chat de referência.
    <div className="flex h-[100dvh] bg-app-bg">
      <div className="hidden md:flex">
        <ConversationSidebar />
      </div>
      <div className="flex flex-1 flex-col min-w-0">
        <FrontDoorHeader isAuthed={isAuthed} onReset={handleReset} onSignOut={signOut} />
      <StatusBar
        completeness={completeness}
        hasRadar={hasRadar}
        onSeeRadar={handleSeeRadar}
        onEditProfile={handleEditProfile}
      />

      <ChatMessageList className="mx-auto w-full max-w-2xl" deps={[sending, hydrated, briefs]}>
        <Bubble role="assistant" content={WELCOME} />

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
              return <Bubble key={i} role={entry.role} content={entry.content} />;
            case "diff":
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
            case "radar":
              return (
                <div key={i} ref={i === lastRadarIndex ? lastRadarRef : undefined}>
                  <RadarCard
                    data={entry.data}
                    briefs={briefs}
                    onBrief={handleBrief}
                    onProposta={handleProposta}
                    onPitch={handlePitch}
                  />
                </div>
              );
            case "gate":
              return <GateCard key={i} action={entry.action} />;
            default:
              return null;
          }
        })}

        {sending && (
          <div className="flex items-start">
            <TypingIndicator />
          </div>
        )}
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
          onAttach={handleAttachClick}
          onPickFile={handlePickFile}
          disabled={sending}
          placeholder="Conte o que sua empresa faz, ou pergunte sobre fomento…"
        />
      </div>
    </div>
  );
}
