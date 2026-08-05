import { memo, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  Check,
  Copy,
  Info,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

import { AerolloyMark } from "@/components/Brand";
import { CitationList, CitationMarker } from "@/components/chat/Citations";
import { StageIndicator, TelemetryBar } from "@/components/chat/Telemetry";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn, initials, isRefusal } from "@/lib/utils";
import type { ChatTurn, Citation } from "@/types";

interface MessageBubbleProps {
  turn: ChatTurn;
  username: string;
  onRate?: (messageId: string, rating: 1 | -1) => void;
}

/**
 * Rewrites `[1]` / `[2][3]` inside rendered markdown text into interactive
 * citation chips.
 *
 * This runs on the *rendered* text nodes rather than the raw markdown so it
 * cannot corrupt link syntax, code spans, or table pipes.
 */
function withCitationMarkers(
  children: ReactNode,
  citations: Citation[],
): ReactNode {
  if (citations.length === 0) return children;

  const transform = (node: ReactNode, key: number): ReactNode => {
    if (typeof node !== "string") return node;
    if (!node.includes("[")) return node;

    const parts: ReactNode[] = [];
    const pattern = /\[(\d{1,2})\]/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = pattern.exec(node)) !== null) {
      const index = Number(match[1]);
      const citation = citations.find(
        (c) => c.marker === index || citations.indexOf(c) + 1 === index,
      );
      if (!citation) continue;

      if (match.index > lastIndex) {
        parts.push(node.slice(lastIndex, match.index));
      }
      parts.push(
        <CitationMarker
          key={`${key}-${match.index}`}
          index={index}
          citation={citation}
        />,
      );
      lastIndex = match.index + match[0].length;
    }

    if (parts.length === 0) return node;
    if (lastIndex < node.length) parts.push(node.slice(lastIndex));
    return parts;
  };

  if (Array.isArray(children)) {
    return children.map((child, index) => transform(child, index));
  }
  return transform(children, 0);
}

export const MessageBubble = memo(function MessageBubble({
  turn,
  username,
  onRate,
}: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);
  const citations = turn.citations ?? [];

  const markdownComponents = useMemo(
    () => ({
      p: ({ children }: { children?: ReactNode }) => (
        <p>{withCitationMarkers(children, citations)}</p>
      ),
      li: ({ children }: { children?: ReactNode }) => (
        <li>{withCitationMarkers(children, citations)}</li>
      ),
      td: ({ children }: { children?: ReactNode }) => (
        <td>{withCitationMarkers(children, citations)}</td>
      ),
    }),
    [citations],
  );

  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText(turn.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked - nothing useful to do */
    }
  }

  /* ----------------------------- User turn ----------------------------- */
  if (turn.role === "user") {
    return (
      <div className="flex justify-end gap-3 animate-slide-in">
        <div className="max-w-[min(42rem,85%)] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-primary-foreground shadow-sm">
          <p className="whitespace-pre-wrap text-[0.9375rem] leading-relaxed">
            {turn.content}
          </p>
        </div>
        <Avatar className="mt-0.5 h-8 w-8 shrink-0">
          <AvatarFallback className="bg-secondary text-secondary-foreground">
            {initials(username)}
          </AvatarFallback>
        </Avatar>
      </div>
    );
  }

  /* -------------------------- Assistant turn --------------------------- */
  const refused = isRefusal(turn.answerSource);

  return (
    <div className="flex gap-3 animate-slide-in">
      <div
        className={cn(
          "mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border bg-card",
          turn.isStreaming && "border-primary/40",
        )}
      >
        <AerolloyMark className="h-5 w-5 text-foreground" />
      </div>

      <div className="min-w-0 max-w-[min(52rem,calc(100%-2.75rem))] flex-1">
        {/* Error */}
        {turn.error && (
          <div className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/10 px-3.5 py-3 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="leading-relaxed">{turn.error}</span>
          </div>
        )}

        {/* Streaming, nothing rendered yet */}
        {!turn.error && turn.isStreaming && !turn.content && (
          <StageIndicator label={turn.stage ?? "Thinking"} />
        )}

        {/* Body */}
        {!turn.error && turn.content && (
          <>
            <div
              className={cn(
                "answer-prose",
                refused && "text-muted-foreground",
                turn.isStreaming && "stream-caret",
              )}
            >
              {refused ? (
                <div className="flex items-start gap-2.5 rounded-lg border border-ember-500/25 bg-ember-500/[0.07] px-3.5 py-3">
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-ember-500" />
                  <p className="leading-relaxed">{turn.content}</p>
                </div>
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={markdownComponents}
                >
                  {turn.content}
                </ReactMarkdown>
              )}
            </div>

            {!turn.isStreaming && (
              <>
                <CitationList citations={citations} />

                <TelemetryBar
                  timings={turn.timings}
                  confidence={turn.confidence}
                  model={turn.model}
                  grounded={turn.grounded ?? true}
                />

                <div className="mt-2 flex items-center gap-0.5">
                  <Tooltip delayDuration={300}>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={copyAnswer}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        {copied ? (
                          <Check className="text-emerald-500" />
                        ) : (
                          <Copy />
                        )}
                        <span className="sr-only">Copy answer</span>
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      {copied ? "Copied" : "Copy answer"}
                    </TooltipContent>
                  </Tooltip>

                  {onRate && !refused && (
                    <>
                      <Tooltip delayDuration={300}>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => onRate(turn.id, 1)}
                            className={cn(
                              "text-muted-foreground hover:text-foreground",
                              turn.feedback === 1 &&
                                "text-emerald-500 hover:text-emerald-500",
                            )}
                          >
                            <ThumbsUp />
                            <span className="sr-only">Helpful</span>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Helpful</TooltipContent>
                      </Tooltip>

                      <Tooltip delayDuration={300}>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => onRate(turn.id, -1)}
                            className={cn(
                              "text-muted-foreground hover:text-foreground",
                              turn.feedback === -1 &&
                                "text-destructive hover:text-destructive",
                            )}
                          >
                            <ThumbsDown />
                            <span className="sr-only">Not helpful</span>
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Not helpful</TooltipContent>
                      </Tooltip>
                    </>
                  )}

                  {turn.rewrittenQuery &&
                    turn.rewrittenQuery !== turn.content && (
                      <Tooltip delayDuration={300}>
                        <TooltipTrigger asChild>
                          <span className="ml-2 cursor-help truncate text-[0.7rem] text-muted-foreground/70">
                            searched: {turn.rewrittenQuery}
                          </span>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="max-w-sm">
                          Your question was rewritten into documentation
                          terminology before searching, so informal wording
                          still finds the right passage.
                        </TooltipContent>
                      </Tooltip>
                    )}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
});
