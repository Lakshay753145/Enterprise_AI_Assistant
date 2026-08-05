import { useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { useAuth } from "@/context/AuthContext";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "@/types";

interface AppShellProps {
  title: string;
  subtitle?: string;
  conversations: ConversationSummary[];
  activeId: string | null;
  onNew: () => void;
  onOpen: (uuid: string) => void;
  onDelete: (uuid: string) => Promise<void>;
  onRename: (uuid: string, title: string) => Promise<void>;
  children: ReactNode;
}

export function AppShell({
  title,
  subtitle,
  conversations,
  activeId,
  onNew,
  onOpen,
  onDelete,
  onRename,
  children,
}: AppShellProps) {
  const { isAdmin } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  // Close the drawer on navigation, otherwise it stays over the new page.
  useEffect(() => setMobileOpen(false), [location.pathname]);

  // Lock body scroll behind the drawer.
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileOpen]);

  const sidebarProps = {
    conversations,
    activeId,
    isAdmin,
    onNew,
    onOpen,
    onDelete,
    onRename,
  };

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      {/* Desktop sidebar */}
      <Sidebar {...sidebarProps} className="hidden lg:flex" />

      {/* Mobile drawer */}
      <div
        className={cn(
          "fixed inset-0 z-50 lg:hidden",
          mobileOpen ? "pointer-events-auto" : "pointer-events-none",
        )}
      >
        <div
          className={cn(
            "absolute inset-0 bg-alloy-950/60 backdrop-blur-sm transition-opacity duration-200",
            mobileOpen ? "opacity-100" : "opacity-0",
          )}
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
        <div
          className={cn(
            "absolute inset-y-0 left-0 transition-transform duration-250 ease-out",
            mobileOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <Sidebar
            {...sidebarProps}
            className="bg-card shadow-2xl"
            onNavigate={() => setMobileOpen(false)}
          />
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          title={title}
          subtitle={subtitle}
          onOpenSidebar={() => setMobileOpen(true)}
        />
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
