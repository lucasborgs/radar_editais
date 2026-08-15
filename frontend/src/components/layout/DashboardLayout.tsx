"use client";

import { ConversationSidebar } from "./ConversationSidebar";
import { FrontDoorHeader } from "../frontdoor/FrontDoorHeader";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface DashboardLayoutProps {
  children: React.ReactNode;
  /** Optional content for a right-side panel. */
  sidebar?: React.ReactNode;
  /** Page title shown in the top bar */
  title?: string;
}

export function DashboardLayout({
  children,
  sidebar,
  title,
}: DashboardLayoutProps) {
  const { session, signOut } = useAuth();

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-app-bg">
      {/* Sidebar chat-first (conversas + utilitárias) */}
      <div className="hidden md:flex">
        <ConversationSidebar />
      </div>

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Navegação compacta compartilhada com o front door no mobile */}
        <div className="md:hidden">
          <FrontDoorHeader
            isAuthed={!!session}
            onSignOut={signOut}
            label={title}
          />
        </div>

        {/* Top bar */}
        {title && (
          <header className="hidden md:flex h-14 flex-shrink-0 border-b border-border bg-surface items-center px-6">
            <h1 className="font-heading text-base font-bold text-content-primary">
              {title}
            </h1>
          </header>
        )}

        {/* Scrollable area */}
        <div className="flex-1 min-w-0 overflow-x-hidden overflow-y-auto">
          {sidebar ? (
            /* Two-column layout: filter sidebar + main */
            <div className="flex h-full flex-col gap-6 p-6 md:flex-row">
              <div className="w-full flex-shrink-0 md:w-64">{sidebar}</div>
              <div className={cn("flex-1 min-w-0")}>{children}</div>
            </div>
          ) : (
            /* Single column */
            <div className="min-w-0 p-6">{children}</div>
          )}
        </div>
      </div>
    </div>
  );
}
