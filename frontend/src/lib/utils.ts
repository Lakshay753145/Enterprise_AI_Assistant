import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 850 -> "850 ms"; 4200 -> "4.2 s"; 65000 -> "1m 5s" */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "-";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / 1024 ** index;
  return `${index === 0 ? value : value.toFixed(1)} ${units[index]}`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((Date.now() - then) / 1000);

  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;

  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
  });
}

/** Bucket conversations into Today / Yesterday / etc. for the sidebar. */
export function groupByRecency<T extends { updated_at: string }>(
  items: T[],
): { label: string; items: T[] }[] {
  const buckets: Record<string, T[]> = {
    Today: [],
    Yesterday: [],
    "Previous 7 days": [],
    "Previous 30 days": [],
    Older: [],
  };

  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const dayMs = 86_400_000;

  for (const item of items) {
    const updated = new Date(item.updated_at).getTime();
    const daysAgo = Math.floor((startOfToday.getTime() - updated) / dayMs);

    if (updated >= startOfToday.getTime()) buckets.Today.push(item);
    else if (daysAgo < 1) buckets.Yesterday.push(item);
    else if (daysAgo < 7) buckets["Previous 7 days"].push(item);
    else if (daysAgo < 30) buckets["Previous 30 days"].push(item);
    else buckets.Older.push(item);
  }

  return Object.entries(buckets)
    .filter(([, group]) => group.length > 0)
    .map(([label, group]) => ({ label, items: group }));
}

export function initials(name: string): string {
  const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Confidence -> display band. Thresholds mirror MIN_CONFIDENCE_THRESHOLD. */
export function confidenceBand(
  score: number | null | undefined,
): { label: string; tone: "high" | "medium" | "low" } {
  if (score === null || score === undefined)
    return { label: "Unrated", tone: "low" };
  if (score >= 0.7) return { label: "High confidence", tone: "high" };
  if (score >= 0.45) return { label: "Moderate confidence", tone: "medium" };
  return { label: "Low confidence", tone: "low" };
}

export function isRefusal(source: string | null | undefined): boolean {
  return source === "refused_no_evidence" || source === "refused_out_of_scope";
}
