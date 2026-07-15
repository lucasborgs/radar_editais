"use client";

// Sidebar do produto: destinos primários, histórico de conversas e páginas de
// suporte. A conversa continua sendo a entrada de Explorar, mas não precisa
// carregar sozinha o modelo mental das demais capacidades.
//
// Fase 2: o histórico vem de `listConversations` (/conversations) — lista
// unificada de conversas de escrita (kind=writing, retomada via /chat?edital=)
// e do front door (kind=frontdoor, retomada via /?c=<session_id>).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { useIsAdmin } from "@/lib/hooks";
import { ThemeToggle } from "./ThemeToggle";
import { HISTORY_KEY, SESSION_ID_KEY } from "@/types/frontdoor";
import {
  listConversations,
  deleteWritingSession,
  getEditalById,
  type ConversationSummary,
} from "@/lib/api";

// Histórico secundário: conversas feitas em Explorar e projetos de escrita.
// Os destinos primários ficam estáveis acima; aqui entram apenas retomadas
// recentes, ordenadas por updated_at desc pela API.
type Section = "Explorar" | "Projetos recentes";
const SECTION_ORDER: Section[] = ["Explorar", "Projetos recentes"];
const SECTION_KIND: Record<Section, ConversationSummary["kind"]> = {
  Explorar: "frontdoor",
  "Projetos recentes": "writing",
};
const SECTION_EMPTY: Record<Section, string> = {
  Explorar: "Nenhuma conversa ainda.",
  "Projetos recentes": "Nenhum projeto em andamento.",
};

// ── Ícones (stroke 1.75, alinhados ao restante do app) ─────────────────────────
function Icon({ d, className }: { d: string; className?: string }) {
  return (
    <svg
      className={cn("w-4 h-4", className)}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.75}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

const ICON_PATHS = {
  plus: "M12 4v16m8-8H4",
  search: "M21 21l-4.35-4.35M11 18a7 7 0 100-14 7 7 0 000 14z",
  pipeline:
    "M4 5a1 1 0 011-1h3a1 1 0 011 1v14a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM15 5a1 1 0 011-1h3a1 1 0 011 1v9a1 1 0 01-1 1h-3a1 1 0 01-1-1V5z",
  editais:
    "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
  files:
    "M5 19a2 2 0 01-2-2V7a2 2 0 012-2h4l2 2h4a2 2 0 012 2v1M5 19h14a2 2 0 002-2v-5a2 2 0 00-2-2H9a2 2 0 00-2 2v5a2 2 0 01-2 2z",
  chat: "M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z",
  profile: "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z",
  settings:
    "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z",
  dots: "M5 12h.01M12 12h.01M19 12h.01",
  discovered:
    "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm4-12l-6 2-2 6 6-2 2-6z",
  radar:
    "M12 3a9 9 0 109 9M12 7a5 5 0 105 5M12 11a1 1 0 100 2M12 12l6.5-6.5",
  projects:
    "M4 7a2 2 0 012-2h4l2 2h6a2 2 0 012 2v10a2 2 0 01-2 2H6a2 2 0 01-2-2V7z",
} as const;

const PRIMARY_ITEMS = [
  { href: "/", label: "Explorar", d: ICON_PATHS.discovered },
  { href: "/radar", label: "Radar", d: ICON_PATHS.radar },
  { href: "/projects", label: "Projetos", d: ICON_PATHS.projects },
] as const;

const UTILITY_ITEMS: { href: string; label: string; d: string; adminOnly?: boolean }[] = [
  { href: "/perfil", label: "Perfil", d: ICON_PATHS.profile },
  { href: "/pipeline", label: "Pipeline", d: ICON_PATHS.pipeline },
  { href: "/oportunidades", label: "Ecossistema", d: ICON_PATHS.editais },
  { href: "/library", label: "Arquivos", d: ICON_PATHS.files },
  // Fila da Descoberta é ferramenta do operador (ADMIN_EMAILS), não do cliente.
  { href: "/discovered", label: "Descobertas", d: ICON_PATHS.discovered, adminOnly: true },
  { href: "/settings", label: "Configurações", d: ICON_PATHS.settings },
];

// ── Componente ─────────────────────────────────────────────────────────────────
export function ConversationSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { session, user, getToken, signOut } = useAuth();
  const isAuthed = !!session;
  const isAdmin = useIsAdmin();

  const [sessions, setSessions] = useState<ConversationSummary[]>([]);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState<string | null>(null);
  // Bump força recarga da lista (a home dispara `conversations:refresh` quando
  // uma conversa nova é criada no servidor).
  const [reloadKey, setReloadKey] = useState(0);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const bump = () => setReloadKey((k) => k + 1);
    window.addEventListener("conversations:refresh", bump);
    return () => window.removeEventListener("conversations:refresh", bump);
  }, []);

  // Carrega o histórico de conversas (deslogado não lista).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!isAuthed) {
        setSessions([]);
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const token = await getToken();
        if (!token) {
          if (!cancelled) setLoading(false);
          return;
        }
        const res = await listConversations(token);
        if (cancelled) return;
        setSessions(res.conversations ?? []);

        // Conversas de escrita: resolve o título via lookup do edital (com
        // cache em `titles`), mesmo padrão da antiga /sessions. frontdoor já
        // traz `title` próprio do banco.
        const missing = (res.conversations ?? []).filter(
          (s): s is ConversationSummary & { edital_id: string } =>
            s.kind === "writing" && !!s.edital_id,
        );
        await Promise.all(
          missing.map(async (s) => {
            try {
              const card = await getEditalById(s.edital_id);
              if (!cancelled) {
                setTitles((prev) => ({ ...prev, [s.edital_id]: card.title }));
              }
            } catch {
              /* ignore — cai no fallback edital_id */
            }
          }),
        );
      } catch {
        // Silencioso: a sidebar não deve gritar erro de listagem (ambiente sem
        // o endpoint, offline, etc.). Fica como "vazia".
        if (!cancelled) setSessions([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isAuthed, getToken, reloadKey]);

  // Fecha o hover-menu "..." ao clicar fora.
  useEffect(() => {
    if (!menuOpen) return;
    function onClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(null);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  // Título exibido: frontdoor usa `title` do banco (1ª mensagem truncada);
  // writing deriva do edital (lookup cacheado → fallback edital_id).
  const titleFor = useCallback(
    (s: ConversationSummary) => {
      if (s.kind === "frontdoor") return s.title || "Conversa";
      return (s.edital_id && titles[s.edital_id]) || s.edital_id || "Proposta";
    },
    [titles],
  );

  // Filtro client-side por título + partição por capacidade (via kind).
  // Sempre renderiza as duas seções (mesmo vazias) para os destinos serem
  // estáveis — exceto quando há busca, aí só mostra seções com resultado.
  const sections = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? sessions.filter((s) => titleFor(s).toLowerCase().includes(q))
      : sessions;
    return SECTION_ORDER.flatMap((section) => {
      const items = filtered.filter((s) => s.kind === SECTION_KIND[section]);
      if (q && items.length === 0) return [];
      return [{ section, items }];
    });
  }, [sessions, query, titleFor]);

  // "Nova conversa": limpa o transcript do front door e vai para "/". Se já
  // estamos em "/", a home não remonta — disparamos um evento que ela escuta
  // para resetar o estado em memória.
  const handleNew = useCallback(() => {
    try {
      window.sessionStorage.removeItem(HISTORY_KEY);
      window.sessionStorage.removeItem(SESSION_ID_KEY);
    } catch {
      /* quota/modo privado — segue */
    }
    if (pathname === "/") {
      window.dispatchEvent(new Event("frontdoor:new"));
    } else {
      router.push("/");
    }
  }, [pathname, router]);

  // Retomada de conversa frontdoor: navegando de outra página, a home monta e
  // lê o ?c=; já estando em "/", o Link não remonta a página — disparamos o
  // evento que a home escuta (mesmo seam do "Nova conversa").
  const handleOpenFrontdoor = useCallback(
    (e: React.MouseEvent, sessionId: string) => {
      if (pathname !== "/") return; // deixa o Link navegar normalmente
      e.preventDefault();
      window.dispatchEvent(
        new CustomEvent("frontdoor:resume", { detail: sessionId }),
      );
    },
    [pathname],
  );

  const handleDelete = useCallback(
    async (sessionId: string) => {
      setMenuOpen(null);
      if (!confirm("Excluir esta conversa permanentemente?")) return;
      setDeleting(sessionId);
      try {
        const token = await getToken();
        if (!token) return;
        await deleteWritingSession(sessionId, token);
        setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
      } catch {
        toast.error("Erro ao excluir a conversa.");
      } finally {
        setDeleting(null);
      }
    },
    [getToken],
  );

  return (
    <aside className="w-64 flex-shrink-0 flex flex-col bg-surface border-r border-border">
      {/* Topo: marca + Nova conversa */}
      <div className="px-3 pt-3 pb-2 space-y-3">
        <Link href="/" className="flex items-center gap-2.5 px-2 pt-1">
          <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center shrink-0">
            <svg
              className="w-4 h-4 text-white"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <circle cx="12" cy="12" r="2" fill="currentColor" />
              <path strokeLinecap="round" d="M12 2a10 10 0 110 20A10 10 0 0112 2z" opacity={0.3} />
              <path strokeLinecap="round" d="M12 6a6 6 0 110 12A6 6 0 0112 6z" opacity={0.5} />
            </svg>
          </div>
          <span className="font-heading text-sm font-bold text-content-primary">
            Radar Editais
          </span>
        </Link>

        <button
          onClick={handleNew}
          className={cn(
            "w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium font-sans",
            "border border-border text-content-primary hover:bg-content-secondary/10 transition-colors",
          )}
        >
          <Icon d={ICON_PATHS.plus} />
          Nova conversa
        </button>

        <nav aria-label="Jornadas principais" className="space-y-0.5">
          {PRIMARY_ITEMS.map(({ href, label, d }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium font-sans transition-colors",
                  active
                    ? "bg-primary/10 text-primary"
                    : "text-content-secondary hover:bg-content-secondary/10 hover:text-content-primary",
                )}
              >
                <Icon d={d} />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Busca client-side por título */}
        <div className="relative">
          <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-content-secondary">
            <Icon d={ICON_PATHS.search} className="w-3.5 h-3.5" />
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar conversas"
            className={cn(
              "w-full pl-8 pr-3 py-2 rounded-lg text-sm font-sans bg-content-secondary/5",
              "border border-transparent focus:border-border focus:bg-surface",
              "text-content-primary placeholder:text-content-secondary outline-none transition-colors",
            )}
          />
        </div>
      </div>

      {/* Histórico de conversas */}
      <nav className="flex-1 overflow-y-auto px-3 pb-2">
        {!isAuthed ? (
          // Deslogado: não lista; call-to-login discreto (padrão ChatGPT).
          <div className="px-2 py-6 text-center">
            <p className="text-xs text-content-secondary font-sans mb-3">
              Entre para salvar e retomar suas conversas.
            </p>
            <Link
              href="/login"
              className="inline-block px-3 py-1.5 rounded-lg text-xs font-semibold font-sans text-primary border border-primary/30 hover:bg-primary/5 transition-colors"
            >
              Entrar
            </Link>
          </div>
        ) : loading ? (
          // Carregando: skeleton.
          <div className="space-y-1.5 pt-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-8 rounded-lg bg-content-secondary/10 animate-pulse" />
            ))}
          </div>
        ) : sections.length === 0 ? (
          // Busca sem resultado (sem busca, as duas seções sempre aparecem).
          <div className="px-2 py-6 text-center">
            <p className="text-xs text-content-secondary font-sans">
              Nenhuma conversa encontrada.
            </p>
          </div>
        ) : (
          sections.map(({ section, items }) => (
            <div key={section} className="mb-3">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-content-secondary px-2 mb-1 font-sans">
                {section}
              </p>
              {items.length === 0 ? (
                <p className="text-xs text-content-secondary/70 font-sans px-2 py-1">
                  {SECTION_EMPTY[section]}
                </p>
              ) : (
              <div className="space-y-0.5">
                {items.map((s) => {
                  // Retomada por kind: escrita resolve a sessão pelo edital
                  // (/chat?edital={id}; /chat não consome ?session); frontdoor
                  // retoma pela home (/?c={session_id}).
                  const isFrontdoor = s.kind === "frontdoor";
                  const href = isFrontdoor
                    ? `/?c=${encodeURIComponent(s.session_id)}`
                    : `/chat?edital=${encodeURIComponent(s.edital_id ?? "")}`;
                  return (
                    <div key={s.session_id} className="group relative">
                      <Link
                        href={href}
                        title={titleFor(s)}
                        onClick={
                          isFrontdoor
                            ? (e) => handleOpenFrontdoor(e, s.session_id)
                            : undefined
                        }
                        className={cn(
                          "flex items-center gap-2 pl-2 pr-7 py-2 rounded-lg text-sm font-sans transition-colors",
                          "text-content-secondary hover:bg-content-secondary/10 hover:text-content-primary",
                          deleting === s.session_id && "opacity-40",
                        )}
                      >
                        <Icon
                          d={isFrontdoor ? ICON_PATHS.chat : ICON_PATHS.editais}
                          className="w-3.5 h-3.5 shrink-0 opacity-70"
                        />
                        <span className="truncate">{titleFor(s)}</span>
                      </Link>
                      {/* Hover-menu "..." */}
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          setMenuOpen((cur) =>
                            cur === s.session_id ? null : s.session_id,
                          );
                        }}
                        className={cn(
                          "absolute right-1 top-1/2 -translate-y-1/2 p-1 rounded-md text-content-secondary",
                          "opacity-0 group-hover:opacity-100 hover:bg-content-secondary/15 transition-opacity",
                          menuOpen === s.session_id && "opacity-100",
                        )}
                        aria-label="Ações da conversa"
                      >
                        <Icon d={ICON_PATHS.dots} className="w-4 h-4" />
                      </button>
                      {menuOpen === s.session_id && (
                        <div
                          ref={menuRef}
                          className="absolute right-1 top-9 z-10 w-32 rounded-lg border border-border bg-surface shadow-card py-1"
                        >
                          <button
                            onClick={() => handleDelete(s.session_id)}
                            className="w-full text-left px-3 py-1.5 text-sm font-sans text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40 transition-colors"
                          >
                            Excluir
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              )}
            </div>
          ))
        )}
      </nav>

      {/* Rodapé: utilitárias discretas + identidade */}
      <div className="border-t border-border px-3 py-2 space-y-0.5">
        {UTILITY_ITEMS.filter((i) => !i.adminOnly || isAdmin).map(({ href, label, d }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 px-2 py-1.5 rounded-lg text-sm font-sans transition-colors",
                active
                  ? "text-primary bg-primary/10"
                  : "text-content-secondary hover:bg-content-secondary/10 hover:text-content-primary",
              )}
            >
              <Icon d={d} />
              {label}
            </Link>
          );
        })}

        {/* Tema claro/escuro */}
        <ThemeToggle />

        {/* Identidade do usuário */}
        {isAuthed ? (
          <div className="flex items-center gap-2 px-2 pt-2 mt-1 border-t border-border">
            <div className="w-7 h-7 rounded-full bg-primary/15 text-primary flex items-center justify-center text-xs font-semibold shrink-0">
              {(user?.email ?? "?").charAt(0).toUpperCase()}
            </div>
            <span className="text-xs text-content-secondary font-sans truncate flex-1">
              {user?.email}
            </span>
            <button
              onClick={() => signOut()}
              title="Sair"
              className="p-1 rounded-md text-content-secondary hover:bg-content-secondary/10 hover:text-content-primary transition-colors"
              aria-label="Sair"
            >
              <Icon
                d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
                className="w-4 h-4"
              />
            </button>
          </div>
        ) : (
          <Link
            href="/login"
            className="flex items-center gap-2.5 px-2 py-1.5 mt-1 rounded-lg text-sm font-sans text-content-secondary hover:bg-content-secondary/10 hover:text-content-primary transition-colors border-t border-border pt-2"
          >
            Entrar
          </Link>
        )}
      </div>
    </aside>
  );
}
