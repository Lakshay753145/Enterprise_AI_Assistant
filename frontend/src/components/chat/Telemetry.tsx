import {
  Brain,
  CheckCircle2,
  Clock,
  Cpu,
  Layers,
  Search,
  ShieldAlert,
  Sparkles,
  Wand2,
  Zap,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn, confidenceBand, formatDuration } from "@/lib/utils";
import type { Timings } from "@/types";

const STAGE_META: {
  key: keyof Timings;
  label: string;
  icon: typeof Search;
  detail: string;
}[] = [
  {
    key: "gate_ms",
    label: "Scope check",
    icon: ShieldAlert,
    detail: "Confirmed the question belongs to your department",
  },
  {
    key: "rewrite_ms",
    label: "Query rewrite",
    icon: Wand2,
    detail: "Translated your wording into documentation terminology",
  },
  {
    key: "route_ms",
    label: "Routing",
    icon: Layers,
    detail: "Chose between knowledge base and document records",
  },
  {
    key: "retrieve_ms",
    label: "Hybrid search",
    icon: Search,
    detail: "Semantic vector search combined with keyword search",
  },
  {
    key: "rerank_ms",
    label: "Reranking",
    icon: Sparkles,
    detail: "Cross-encoder reordered passages by true relevance",
  },
  {
    key: "generate_ms",
    label: "Generation",
    icon: Brain,
    detail: "Composed the answer from the retrieved passages",
  },
  {
    key: "verify_ms",
    label: "Verification",
    icon: CheckCircle2,
    detail: "Fact-checked every claim against the sources",
  },
  {
    key: "sql_agent_ms",
    label: "SQL agent",
    icon: Cpu,
    detail: "Queried the document records database",
  },
];

/**
 * The per-answer performance strip.
 *
 * Total latency is always shown. Time-to-first-token is shown alongside it
 * because that is the number a user actually experiences as "speed" - a
 * 9-second answer that starts rendering in 800 ms feels fast.
 */
export function TelemetryBar({
  timings,
  confidence,
  model,
  grounded = true,
  className,
}: {
  timings?: Timings;
  confidence?: number | null;
  model?: string | null;
  grounded?: boolean;
  className?: string;
}) {
  if (!timings || Object.keys(timings).length === 0) return null;

  const stages = STAGE_META.filter(
    (stage) => typeof timings[stage.key] === "number" && timings[stage.key]! > 0,
  );
  const total = timings.total_ms ?? 0;
  const band = confidenceBand(confidence);

  return (
    <div
      className={cn(
        "mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-[0.7rem] text-muted-foreground",
        className,
      )}
    >
      {/* Total */}
      <Tooltip delayDuration={200}>
        <TooltipTrigger asChild>
          <span className="inline-flex cursor-help items-center gap-1.5 font-medium text-foreground/80">
            <Clock className="h-3 w-3" />
            {formatDuration(total)}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="p-3">
          <div className="mb-2 font-semibold">Where the time went</div>
          <ul className="space-y-1">
            {stages.map((stage) => (
              <li key={stage.key} className="flex items-center gap-2">
                <stage.icon className="h-3 w-3 shrink-0 opacity-70" />
                <span className="flex-1">{stage.label}</span>
                <span className="font-mono tabular-nums text-muted-foreground">
                  {formatDuration(timings[stage.key] as number)}
                </span>
              </li>
            ))}
            <li className="mt-1 flex items-center gap-2 border-t pt-1.5 font-semibold">
              <span className="flex-1">Total</span>
              <span className="font-mono tabular-nums">
                {formatDuration(total)}
              </span>
            </li>
          </ul>
        </TooltipContent>
      </Tooltip>

      {/* First token */}
      {typeof timings.first_token_ms === "number" && (
        <Tooltip delayDuration={200}>
          <TooltipTrigger asChild>
            <span className="inline-flex cursor-help items-center gap-1.5">
              <Zap className="h-3 w-3" />
              {formatDuration(timings.first_token_ms)} to first word
            </span>
          </TooltipTrigger>
          <TooltipContent side="top">
            How long before the answer started appearing.
          </TooltipContent>
        </Tooltip>
      )}

      {/* Confidence */}
      {typeof confidence === "number" && (
        <Tooltip delayDuration={200}>
          <TooltipTrigger asChild>
            <Badge
              variant={
                band.tone === "high"
                  ? "success"
                  : band.tone === "medium"
                    ? "warning"
                    : "outline"
              }
              className="cursor-help"
            >
              {band.label}
            </Badge>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">
            The reranker scored the best supporting passage at{" "}
            {(confidence * 100).toFixed(0)}%. Below the configured threshold the
            assistant refuses rather than guessing.
          </TooltipContent>
        </Tooltip>
      )}

      {/* Grounding failure */}
      {!grounded && (
        <Tooltip delayDuration={200}>
          <TooltipTrigger asChild>
            <Badge variant="destructive" className="cursor-help">
              <ShieldAlert className="h-3 w-3" />
              Unverified claims
            </Badge>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">
            The fact-checking pass could not trace every statement back to a
            source passage. Verify against the cited documents before acting on
            this answer.
          </TooltipContent>
        </Tooltip>
      )}

      {model && (
        <span className="hidden font-mono opacity-60 sm:inline">{model}</span>
      )}
    </div>
  );
}

/** Live "what is it doing right now" indicator, shown while streaming. */
export function StageIndicator({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2.5 text-sm text-muted-foreground animate-fade-in">
      <span className="flex gap-1" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse-dot"
            style={{ animationDelay: `${i * 180}ms` }}
          />
        ))}
      </span>
      <span>{label}</span>
    </div>
  );
}
