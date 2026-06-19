import { ConversationSidebar } from "./ConversationSidebar";
import { cn } from "@/lib/utils";

interface DashboardLayoutProps {
  children: React.ReactNode;
  /** Optional content for a right-side panel (e.g. SidebarFilter) */
  sidebar?: React.ReactNode;
  /** Page title shown in the top bar */
  title?: string;
}

export function DashboardLayout({
  children,
  sidebar,
  title,
}: DashboardLayoutProps) {
  return (
    <div className="flex h-screen overflow-hidden bg-app-bg">
      {/* Sidebar chat-first (conversas + utilitárias) */}
      <ConversationSidebar />

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        {title && (
          <header className="h-14 flex-shrink-0 border-b border-border bg-surface flex items-center px-6">
            <h1 className="font-heading text-base font-bold text-content-primary">
              {title}
            </h1>
          </header>
        )}

        {/* Scrollable area */}
        <div className="flex-1 overflow-y-auto">
          {sidebar ? (
            /* Two-column layout: filter sidebar + main */
            <div className="flex gap-6 p-6 h-full">
              <div className="w-64 flex-shrink-0">{sidebar}</div>
              <div className={cn("flex-1 min-w-0")}>{children}</div>
            </div>
          ) : (
            /* Single column */
            <div className="p-6">{children}</div>
          )}
        </div>
      </div>
    </div>
  );
}
