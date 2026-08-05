import { useState } from "react";
import { ChevronDown, FileText, Hash, Quote } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { Citation } from "@/types";

/**
 * Inline [n] markers inside the answer text.
 *
 * The marker is a live element rather than plain text: hovering shows the
 * quoted passage, so a user can verify a claim without leaving their place in
 * the answer. That verification loop is the whole point of citing.
 */
export function CitationMarker({
  index,
  citation,
}: {
  index: number;
  citation?: Citation;
}) {
  if (!citation) {
    return <sup className="citation-chip">{index}</sup>;
  }

  return (
    <Tooltip delayDuration={120}>
      <TooltipTrigger asChild>
        <sup className="citation-chip cursor-help">{index}</sup>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-sm p-3">
        <div className="mb-1.5 flex items-center gap-1.5 font-semibold">
          <FileText className="h-3 w-3 shrink-0" />
          <span className="truncate">{citation.document}</span>
        </div>
        {(citation.page || citation.heading) && (
          <div className="mb-1.5 text-[0.7rem] text-muted-foreground">
            {citation.page ? `Page ${citation.page}` : null}
            {citation.page && citation.heading ? " · " : null}
            {citation.heading}
          </div>
        )}
        <p className="line-clamp-5 leading-relaxed text-muted-foreground">
          {citation.snippet}
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

/** The collapsible source list shown beneath a completed answer. */
export function CitationList({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState(false);

  if (citations.length === 0) return null;

  return (
    <div className="mt-3.5 overflow-hidden rounded-lg border bg-muted/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-xs font-medium transition-colors hover:bg-muted/60"
        aria-expanded={open}
      >
        <Quote className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span>
          {citations.length} source{citations.length === 1 ? "" : "s"} from your
          department&rsquo;s documentation
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <ul className="space-y-2 border-t p-2.5 animate-fade-in">
          {citations.map((citation) => (
            <li
              key={citation.chunk_id}
              className="rounded-md border bg-card p-3 text-xs"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-2">
                  {citation.marker !== undefined && (
                    <span className="citation-chip mt-px shrink-0">
                      {citation.marker}
                    </span>
                  )}
                  <div className="min-w-0">
                    <div className="truncate font-medium text-foreground">
                      {citation.document}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.7rem] text-muted-foreground">
                      {citation.page && (
                        <span className="inline-flex items-center gap-1">
                          <Hash className="h-2.5 w-2.5" />
                          Page {citation.page}
                        </span>
                      )}
                      {citation.section && (
                        <span className="truncate">{citation.section}</span>
                      )}
                    </div>
                  </div>
                </div>

                <Badge variant="outline" className="shrink-0 font-mono">
                  {(citation.score * 100).toFixed(0)}%
                </Badge>
              </div>

              <p className="mt-2 border-l-2 border-primary/30 pl-2.5 leading-relaxed text-muted-foreground">
                {citation.snippet}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
