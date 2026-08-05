import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Ban,
  CheckCircle2,
  Clock,
  FileText,
  Gauge,
  MessageSquare,
  ShieldCheck,
  ShieldOff,
  ThumbsDown,
  ThumbsUp,
  Unlock,
  Users,
} from "lucide-react";

import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/context/AuthContext";
import { useChat } from "@/hooks/useChat";
import { adminApi, ApiError } from "@/lib/api";
import { formatDateTime, formatDuration } from "@/lib/utils";
import type { Analytics, User } from "@/types";

export default function AdminPage() {
  const { user } = useAuth();
  const chat = useChat();

  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [stats, list] = await Promise.all([
        adminApi.analytics(),
        adminApi.listUsers(),
      ]);
      setAnalytics(stats);
      setUsers(list);
    } catch (error) {
      if (error instanceof ApiError) toast.error(error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(
    action: "activate" | "deactivate" | "unlock",
    target: User,
  ) {
    try {
      const updated =
        action === "activate"
          ? await adminApi.activateUser(target.id)
          : action === "deactivate"
            ? await adminApi.deactivateUser(target.id)
            : await adminApi.unlockUser(target.id);

      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      toast.success(`${target.username} updated.`);
    } catch (error) {
      if (error instanceof ApiError) toast.error(error.message);
    }
  }

  if (!user) return null;

  const chatStats = analytics?.chat;
  const docStats = analytics?.documents;

  const metrics = [
    {
      label: "Questions answered",
      value: chatStats?.total_answers ?? 0,
      icon: MessageSquare,
      hint: "Assistant responses in this department",
    },
    {
      label: "Median response",
      value: formatDuration(chatStats?.avg_latency_ms ?? null),
      icon: Clock,
      hint: "Average end-to-end answer time",
    },
    {
      label: "Retrieval confidence",
      value:
        chatStats?.avg_confidence != null
          ? `${(chatStats.avg_confidence * 100).toFixed(0)}%`
          : "-",
      icon: Gauge,
      hint: "Average reranker score of the best supporting passage",
    },
    {
      label: "Refusal rate",
      value:
        chatStats != null ? `${(chatStats.refusal_rate * 100).toFixed(0)}%` : "-",
      icon: ShieldCheck,
      hint: "Questions the assistant declined rather than guessing. A high rate usually means the knowledge base has gaps.",
    },
  ];

  return (
    <AppShell
      title="Administration"
      subtitle={`${user.department} department`}
      conversations={chat.conversations}
      activeId={chat.activeId}
      onNew={chat.startNewConversation}
      onOpen={chat.openConversation}
      onDelete={chat.deleteConversation}
      onRename={chat.renameConversation}
    >
      <div className="scrollbar-slim h-full overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
          {/* -------------------------- Metrics -------------------------- */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {metrics.map((metric) => (
              <Card key={metric.label}>
                <CardContent className="p-4">
                  <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    <metric.icon className="h-3.5 w-3.5" />
                    {metric.label}
                  </div>
                  <div className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight">
                    {loading ? <Skeleton className="h-8 w-20" /> : metric.value}
                  </div>
                  <p className="mt-1.5 text-[0.7rem] leading-relaxed text-muted-foreground">
                    {metric.hint}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="mt-5 grid gap-5 lg:grid-cols-3">
            {/* ------------------------ Knowledge base ------------------- */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <FileText className="h-4 w-4 text-primary" />
                  Knowledge base
                </CardTitle>
                <CardDescription>
                  What the assistant can currently answer from.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2.5 text-sm">
                {[
                  ["Documents", docStats?.total_documents ?? 0],
                  ["Indexed", docStats?.completed ?? 0],
                  ["Processing", docStats?.processing ?? 0],
                  ["Failed", docStats?.failed ?? 0],
                  ["Passages", (docStats?.total_chunks ?? 0).toLocaleString()],
                  ["Pages", (docStats?.total_pages ?? 0).toLocaleString()],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-medium tabular-nums">{value}</span>
                  </div>
                ))}
              </CardContent>
            </Card>

            {/* --------------------------- Feedback ---------------------- */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <ThumbsUp className="h-4 w-4 text-primary" />
                  User feedback
                </CardTitle>
                <CardDescription>
                  Ratings users gave on individual answers.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="flex items-center gap-6">
                  <div className="flex items-center gap-2">
                    <ThumbsUp className="h-4 w-4 text-emerald-500" />
                    <span className="text-2xl font-semibold tabular-nums">
                      {chatStats?.feedback_helpful ?? 0}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <ThumbsDown className="h-4 w-4 text-destructive" />
                    <span className="text-2xl font-semibold tabular-nums">
                      {chatStats?.feedback_unhelpful ?? 0}
                    </span>
                  </div>
                </div>
                <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                  Answers rated unhelpful are the fastest route to finding gaps
                  in your documentation.
                </p>
              </CardContent>
            </Card>

            {/* ---------------------------- Users ------------------------ */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Users className="h-4 w-4 text-primary" />
                  People
                </CardTitle>
                <CardDescription>
                  Accounts with access to {user.department}.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Total accounts</span>
                  <span className="font-medium tabular-nums">
                    {analytics?.users.total ?? 0}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Administrators</span>
                  <span className="font-medium tabular-nums">
                    {analytics?.users.admins ?? 0}
                  </span>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* ---------------------------- User table ------------------- */}
          <Card className="mt-5">
            <CardHeader>
              <CardTitle className="text-sm">User accounts</CardTitle>
              <CardDescription>
                Deactivating an account revokes access immediately, including
                any tokens already issued.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-y bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <th className="px-5 py-2.5 font-medium">User</th>
                      <th className="px-5 py-2.5 font-medium">Department</th>
                      <th className="px-5 py-2.5 font-medium">Role</th>
                      <th className="px-5 py-2.5 font-medium">Status</th>
                      <th className="px-5 py-2.5 font-medium">Last sign-in</th>
                      <th className="px-5 py-2.5 text-right font-medium">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading ? (
                      [0, 1, 2].map((i) => (
                        <tr key={i} className="border-b">
                          <td colSpan={6} className="px-5 py-3">
                            <Skeleton className="h-6 w-full" />
                          </td>
                        </tr>
                      ))
                    ) : users.length === 0 ? (
                      <tr>
                        <td
                          colSpan={6}
                          className="px-5 py-10 text-center text-muted-foreground"
                        >
                          No user accounts found.
                        </td>
                      </tr>
                    ) : (
                      users.map((row) => (
                        <tr
                          key={row.id}
                          className="border-b last:border-0 hover:bg-muted/25"
                        >
                          <td className="px-5 py-3">
                            <div className="font-medium">
                              {row.full_name || row.username}
                            </div>
                            <div className="text-xs text-muted-foreground">
                              {row.username}
                            </div>
                          </td>
                          <td className="px-5 py-3">
                            <Badge variant="secondary">{row.department}</Badge>
                          </td>
                          <td className="px-5 py-3">
                            {row.role === "user" ? (
                              <span className="text-muted-foreground">User</span>
                            ) : (
                              <Badge variant="warning">
                                {row.role === "super_admin"
                                  ? "Super admin"
                                  : "Admin"}
                              </Badge>
                            )}
                          </td>
                          <td className="px-5 py-3">
                            {row.is_active ? (
                              <span className="inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                Active
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                                <ShieldOff className="h-3.5 w-3.5" />
                                Deactivated
                              </span>
                            )}
                          </td>
                          <td className="px-5 py-3 text-xs text-muted-foreground">
                            {row.last_login_at
                              ? formatDateTime(row.last_login_at)
                              : "Never"}
                          </td>
                          <td className="px-5 py-3">
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => void act("unlock", row)}
                              >
                                <Unlock />
                                Unlock
                              </Button>
                              {row.id !== user.id &&
                                (row.is_active ? (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="text-destructive hover:text-destructive"
                                    onClick={() => void act("deactivate", row)}
                                  >
                                    <Ban />
                                    Deactivate
                                  </Button>
                                ) : (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => void act("activate", row)}
                                  >
                                    <CheckCircle2 />
                                    Activate
                                  </Button>
                                ))}
                            </div>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
