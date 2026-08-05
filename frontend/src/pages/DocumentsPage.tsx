import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Download,
  FileText,
  Layers,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
} from "lucide-react";

import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAuth } from "@/context/AuthContext";
import { useChat } from "@/hooks/useChat";
import { ApiError, documentsApi } from "@/lib/api";
import { cn, formatBytes, formatDateTime } from "@/lib/utils";
import type { DocumentRecord, IngestionSummary } from "@/types";

const STATUS_META = {
  completed: { label: "Indexed", icon: CheckCircle2, variant: "success" },
  processing: { label: "Processing", icon: Loader2, variant: "warning" },
  pending: { label: "Queued", icon: Clock, variant: "secondary" },
  failed: { label: "Failed", icon: AlertCircle, variant: "destructive" },
} as const;

export default function DocumentsPage() {
  const { user, isAdmin } = useAuth();
  const chat = useChat();

  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [summary, setSummary] = useState<IngestionSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [deleting, setDeleting] = useState<DocumentRecord | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [list, stats] = await Promise.all([
        documentsApi.list(),
        documentsApi.summary(),
      ]);
      setDocuments(list.documents);
      setSummary(stats);
    } catch (error) {
      if (error instanceof ApiError) toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while anything is still being parsed. Docling can take minutes on a
  // large scanned PDF, and the admin should watch it land without refreshing.
  useEffect(() => {
    const inFlight = documents.some(
      (d) => d.status === "processing" || d.status === "pending",
    );
    if (!inFlight) return;

    const timer = setInterval(() => void load(), 4000);
    return () => clearInterval(timer);
  }, [documents, load]);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);

    for (const file of Array.from(files)) {
      try {
        const result = await documentsApi.upload(file);
        toast.success(result.message);
      } catch (error) {
        toast.error(
          error instanceof ApiError
            ? error.message
            : `Could not upload ${file.name}.`,
        );
      }
    }

    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
    void load();
  }

  async function handleReprocess(document: DocumentRecord) {
    try {
      const result = await documentsApi.reprocess(document.uuid);
      toast.success(result.message);
      void load();
    } catch (error) {
      if (error instanceof ApiError) toast.error(error.message);
    }
  }

  async function handleDelete(document: DocumentRecord) {
    try {
      await documentsApi.remove(document.uuid);
      toast.success(`"${document.original_filename}" removed from the index.`);
      setDocuments((prev) => prev.filter((d) => d.uuid !== document.uuid));
      void load();
    } catch (error) {
      if (error instanceof ApiError) toast.error(error.message);
    } finally {
      setDeleting(null);
    }
  }

  if (!user) return null;

  return (
    <AppShell
      title="Knowledge base"
      subtitle={`Documents the assistant can answer from for ${user.department}`}
      conversations={chat.conversations}
      activeId={chat.activeId}
      onNew={chat.startNewConversation}
      onOpen={chat.openConversation}
      onDelete={chat.deleteConversation}
      onRename={chat.renameConversation}
    >
      <div className="scrollbar-slim h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
          {/* --------------------------- Stats --------------------------- */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              {
                label: "Documents",
                value: summary?.total_documents ?? 0,
                icon: FileText,
              },
              {
                label: "Indexed passages",
                value: (summary?.total_chunks ?? 0).toLocaleString(),
                icon: Layers,
              },
              { label: "Pages parsed", value: summary?.total_pages ?? 0, icon: FileText },
              {
                label: "Last updated",
                value: summary?.last_ingested_at
                  ? formatDateTime(summary.last_ingested_at).split(",")[0]
                  : "Never",
                icon: Clock,
              },
            ].map((stat) => (
              <Card key={stat.label}>
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    <stat.icon className="h-3.5 w-3.5" />
                    {stat.label}
                  </div>
                  <div className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight">
                    {loading ? <Skeleton className="h-8 w-20" /> : stat.value}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* --------------------------- Upload -------------------------- */}
          {isAdmin && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                void handleFiles(e.dataTransfer.files);
              }}
              className={cn(
                "mt-5 rounded-xl border-2 border-dashed p-8 text-center transition-colors",
                dragging
                  ? "border-primary bg-primary/5"
                  : "border-border hover:border-primary/40",
              )}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".pdf,.docx,.pptx,.xlsx,.md,.html,.txt"
                className="hidden"
                onChange={(e) => void handleFiles(e.target.files)}
              />

              <Upload className="mx-auto h-7 w-7 text-muted-foreground" />
              <p className="mt-3 text-sm font-medium">
                Drop documents here, or{" "}
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="text-primary underline underline-offset-2 hover:opacity-80"
                >
                  browse
                </button>
              </p>
              <p className="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-muted-foreground">
                PDF, Word, PowerPoint, Excel, Markdown or HTML. Files are parsed
                with layout and table recognition, then indexed for{" "}
                <span className="font-medium text-foreground">
                  {user.department}
                </span>{" "}
                only. Large scanned PDFs can take a few minutes.
              </p>

              {uploading && (
                <div className="mt-4 inline-flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Uploading...
                </div>
              )}
            </div>
          )}

          {/* -------------------------- Document list -------------------- */}
          <div className="mt-6">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold">
                {user.department} documents
                {documents.length > 0 && (
                  <span className="ml-1.5 font-normal text-muted-foreground">
                    ({documents.length})
                  </span>
                )}
              </h2>
              <Button variant="ghost" size="sm" onClick={() => void load()}>
                <RefreshCw />
                Refresh
              </Button>
            </div>

            {loading ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-[4.5rem] w-full rounded-xl" />
                ))}
              </div>
            ) : documents.length === 0 ? (
              <Card>
                <CardContent className="py-14 text-center">
                  <FileText className="mx-auto h-9 w-9 text-muted-foreground/50" />
                  <p className="mt-3 text-sm font-medium">
                    No documents indexed yet
                  </p>
                  <p className="mx-auto mt-1.5 max-w-sm text-xs leading-relaxed text-muted-foreground">
                    {isAdmin
                      ? "Upload your department's documentation above. Until then, the assistant has nothing to answer from and will say so."
                      : "Ask your department administrator to upload the relevant documentation."}
                  </p>
                </CardContent>
              </Card>
            ) : (
              <ul className="space-y-2">
                {documents.map((document) => {
                  const meta =
                    STATUS_META[document.status] ?? STATUS_META.pending;
                  return (
                    <li key={document.uuid}>
                      <Card className="transition-colors hover:border-primary/30">
                        <CardContent className="flex items-center gap-4 p-4">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                            <FileText className="h-4.5 w-4.5 text-primary" />
                          </div>

                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium">
                              {document.title || document.original_filename}
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                              <span className="truncate">
                                {document.original_filename}
                              </span>
                              <span>{formatBytes(document.size_bytes)}</span>
                              {document.page_count > 0 && (
                                <span>{document.page_count} pages</span>
                              )}
                              {document.chunk_count > 0 && (
                                <span>{document.chunk_count} passages</span>
                              )}
                              <span>{formatDateTime(document.created_at)}</span>
                            </div>
                            {document.error_message && (
                              <p className="mt-1.5 line-clamp-2 text-xs text-destructive">
                                {document.error_message}
                              </p>
                            )}
                          </div>

                          <Badge
                            variant={meta.variant}
                            className="shrink-0 whitespace-nowrap"
                          >
                            <meta.icon
                              className={cn(
                                "h-3 w-3",
                                document.status === "processing" &&
                                  "animate-spin",
                              )}
                            />
                            {meta.label}
                          </Badge>

                          <div className="flex shrink-0 items-center gap-0.5">
                            <Tooltip delayDuration={300}>
                              <TooltipTrigger asChild>
                                <Button variant="ghost" size="icon-sm" asChild>
                                  <a
                                    href={documentsApi.downloadUrl(
                                      document.uuid,
                                    )}
                                    download
                                  >
                                    <Download />
                                    <span className="sr-only">Download</span>
                                  </a>
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent>Download original</TooltipContent>
                            </Tooltip>

                            {isAdmin && (
                              <>
                                <Tooltip delayDuration={300}>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon-sm"
                                      onClick={() =>
                                        void handleReprocess(document)
                                      }
                                    >
                                      <RefreshCw />
                                      <span className="sr-only">
                                        Reprocess
                                      </span>
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>
                                    Re-parse and re-index
                                  </TooltipContent>
                                </Tooltip>

                                <Tooltip delayDuration={300}>
                                  <TooltipTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      size="icon-sm"
                                      className="text-muted-foreground hover:text-destructive"
                                      onClick={() => setDeleting(document)}
                                    >
                                      <Trash2 />
                                      <span className="sr-only">Delete</span>
                                    </Button>
                                  </TooltipTrigger>
                                  <TooltipContent>Delete</TooltipContent>
                                </Tooltip>
                              </>
                            )}
                          </div>
                        </CardContent>
                      </Card>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </div>

      <Dialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove this document?</DialogTitle>
            <DialogDescription className="leading-relaxed">
              &ldquo;{deleting?.original_filename}&rdquo; and its{" "}
              {deleting?.chunk_count ?? 0} indexed passages will be deleted. The
              assistant will no longer be able to answer from it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleting && void handleDelete(deleting)}
            >
              <Trash2 />
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
