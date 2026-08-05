import { FileSearch, Quote, ShieldCheck, Sparkles } from "lucide-react";

import { AerolloyMark } from "@/components/Brand";
import { cn } from "@/lib/utils";
import type { Department } from "@/types";

/** Starter prompts, written in the voice each department actually uses. */
const SUGGESTIONS: Record<Department, string[]> = {
  Finance: [
    "What is the approval limit for a capital expenditure request?",
    "How do I claim reimbursement for official travel?",
    "What are the standard vendor payment terms?",
    "Which documents are needed to process a supplier invoice?",
  ],
  HR: [
    "How many days of earned leave do I get in a year?",
    "What is the process for applying for leave?",
    "What does the probation confirmation process involve?",
    "What safety training is mandatory for shop-floor staff?",
  ],
  IT: [
    "How do I request access to a new application?",
    "What is the password policy?",
    "How often is data backed up and how do I restore a file?",
    "What is the procedure for reporting a security incident?",
  ],
  Production: [
    "What is the procedure for handling a non-conforming part?",
    "What are the heat treatment parameters for this alloy?",
    "How is a first article inspection carried out?",
    "What does the AS9100 requirement say about process control?",
  ],
  Purchase: [
    "What is the vendor registration and approval process?",
    "When is a competitive tender required?",
    "What checks are done on material receipt?",
    "How is a purchase requisition converted into a purchase order?",
  ],
};

const CAPABILITIES = [
  {
    icon: FileSearch,
    title: "Hybrid retrieval",
    body: "Meaning-based and exact keyword search run together, then a reranker picks the passages that truly answer your question.",
  },
  {
    icon: Quote,
    title: "Every fact cited",
    body: "Each statement carries the document and page it came from, so you can verify it in seconds.",
  },
  {
    icon: ShieldCheck,
    title: "Your department only",
    body: "The assistant can only read documentation your department owns. Other departments' data is unreachable.",
  },
];

export function Welcome({
  department,
  displayName,
  onPick,
}: {
  department: Department;
  displayName: string;
  onPick: (question: string) => void;
}) {
  const suggestions = SUGGESTIONS[department] ?? [];
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 sm:py-14">
      <div className="flex flex-col items-center text-center">
        <div className="relative">
          <div className="absolute inset-0 -z-10 blur-2xl">
            <div className="mx-auto h-16 w-16 rounded-full bg-primary/25" />
          </div>
          <AerolloyMark className="h-14 w-14 text-foreground" />
        </div>

        <h1 className="mt-6 text-balance text-2xl font-semibold tracking-tight sm:text-3xl">
          {greeting}, {displayName.split(/[\s.]/)[0]}
        </h1>
        <p className="mt-2.5 max-w-lg text-pretty leading-relaxed text-muted-foreground">
          Ask anything documented in the{" "}
          <span className="font-medium text-foreground">{department}</span>{" "}
          knowledge base. Answers are drawn only from approved documentation,
          with sources shown.
        </p>
      </div>

      {suggestions.length > 0 && (
        <div className="mt-9">
          <div className="mb-3 flex items-center gap-1.5 px-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" />
            Try asking
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {suggestions.map((question) => (
              <button
                key={question}
                type="button"
                onClick={() => onPick(question)}
                className={cn(
                  "group rounded-xl border bg-card p-3.5 text-left text-sm leading-relaxed shadow-sm transition-all",
                  "hover:-translate-y-px hover:border-primary/40 hover:shadow-md",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                )}
              >
                <span className="text-foreground/90 group-hover:text-foreground">
                  {question}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-10 grid gap-3 sm:grid-cols-3">
        {CAPABILITIES.map((item) => (
          <div key={item.title} className="rounded-xl border bg-muted/25 p-4">
            <item.icon className="h-4 w-4 text-primary" />
            <div className="mt-2.5 text-sm font-medium">{item.title}</div>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {item.body}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
