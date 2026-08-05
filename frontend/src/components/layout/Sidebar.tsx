import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  BarChart3,
  FileText,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Trash2,
  X,
} from "lucide-react";

import { BrandLockup } from "@/components/Brand";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, groupByRecency } from "@/lib/utils";
import type { ConversationSummary } from "@/types";

interface SidebarProps {
  conversations: ConversationSummary[];
  activeId: string | null;
  isAdmin: boolean;
  isLoading?: boolean;
  onNew: () => void;
  onOpen: (uuid: string) => void;
  onDelete: (uuid: string) => Promise<void>;
  onRename: (uuid: string, title: string) => Promise<void>;
  onNavigate?: () => void;
  className?: string;
}

export function Sidebar({
  conversations,
  activeId,
  isAdmin,
  isLoading,
  onNew,
  onOpen,
  onDelete,
  onRename,
  onNavigate,
  className,
}: SidebarProps) {
  const [renaming, setRenaming] = useState<ConversationSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleting, setDeleting] = useState<ConversationSummary | null>(null);
  const [busy, setBusy] = useState(false);

  const groups = groupByRecency(conversations);

  const navItemClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
      isActive
        ? "bg-secondary text-secondary-foreground"
        : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
    );

  return (
    <aside
      className={cn(
        "flex h-full w-[17.5rem] shrink-0 flex-col border-r bg-card/40",
        className,
      )}
    >
      <div className="p-3">
        <div className="px-1.5 py-2">
          <BrandLockup size="sm" subtitle="AI Assistant" />
        </div>

        <Button
          onClick={() => {
            onNew();
            onNavigate?.();
          }}
          className="mt-3 w-full justify-start gap-2"
          variant="outline"
        >
          <MessageSquarePlus />
          New chat
        </Button>
      </div>

      {/* Conversation history */}
      <div className="scrollbar-slim min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        {isLoading ? (
          <div className="space-y-2 pt-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-2 pt-4 text-xs leading-relaxed text-muted-foreground">
            Your conversations will appear here.
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="mb-4">
              <div className="px-2 pb-1.5 pt-1 text-[0.7rem] font-medium uppercase tracking-wider text-muted-foreground/70">
                {group.label}
              </div>
              <ul className="space-y-0.5">
                {group.items.map((conversation) => {
                  const isActive = conversation.uuid === activeId;
                  return (
                    <li key={conversation.uuid} className="group relative">
                      <button
                        type="button"
                        onClick={() => {
                          onOpen(conversation.uuid);
                          onNavigate?.();
                        }}
                        className={cn(
                          "w-full truncate rounded-lg py-2 pl-2.5 pr-9 text-left text-sm transition-colors",
                          isActive
                            ? "bg-secondary font-medium text-secondary-foreground"
                            : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                        )}
                        title={conversation.title}
                      >
                        {conversation.title}
                      </button>

                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className={cn(
                              "absolute right-1 top-1/2 -translate-y-1/2 text-muted-foreground opacity-0 transition-opacity",
                              "group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100",
                            )}
                            aria-label="Conversation options"
                          >
                            <MoreHorizontal />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-40">
                          <DropdownMenuItem
                            onClick={() => {
                              setRenaming(conversation);
                              setRenameValue(conversation.title);
                            }}
                          >
                            <Pencil />
                            Rename
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            className="text-destructive focus:text-destructive"
                            onClick={() => setDeleting(conversation)}
                          >
                            <Trash2 />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </div>

      {/* Section navigation */}
      <div className="space-y-0.5 border-t p-3">
        <NavLink to="/documents" className={navItemClass} onClick={onNavigate}>
          <FileText className="h-4 w-4" />
          Knowledge base
        </NavLink>
        {isAdmin && (
          <NavLink to="/admin" className={navItemClass} onClick={onNavigate}>
            <BarChart3 className="h-4 w-4" />
            Administration
          </NavLink>
        )}
      </div>

      {/* Rename dialog */}
      <Dialog
        open={renaming !== null}
        onOpenChange={(open) => !open && setRenaming(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Rename conversation</DialogTitle>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            maxLength={300}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter" && renameValue.trim()) {
                e.preventDefault();
                void (async () => {
                  setBusy(true);
                  await onRename(renaming!.uuid, renameValue.trim());
                  setBusy(false);
                  setRenaming(null);
                })();
              }
            }}
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenaming(null)}>
              Cancel
            </Button>
            <Button
              loading={busy}
              disabled={!renameValue.trim()}
              onClick={async () => {
                setBusy(true);
                await onRename(renaming!.uuid, renameValue.trim());
                setBusy(false);
                setRenaming(null);
              }}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirmation */}
      <Dialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription className="leading-relaxed">
              &ldquo;{deleting?.title}&rdquo; will be removed from your history.
              The archived transcript is retained in the company audit record.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              <X />
              Cancel
            </Button>
            <Button
              variant="destructive"
              loading={busy}
              onClick={async () => {
                setBusy(true);
                await onDelete(deleting!.uuid);
                setBusy(false);
                setDeleting(null);
              }}
            >
              <Trash2 />
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  );
}
