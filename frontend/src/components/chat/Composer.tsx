import { useEffect, useRef, useState } from "react";
import { ArrowUp, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const MAX_LENGTH = 2000;

interface ComposerProps {
  onSend: (question: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  department: string;
  disabled?: boolean;
}

export function Composer({
  onSend,
  onStop,
  isStreaming,
  department,
  disabled,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Grow with content up to a cap, then scroll internally.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  // Refocus once an answer finishes so the next question can be typed straight
  // away without reaching for the mouse.
  useEffect(() => {
    if (!isStreaming) textareaRef.current?.focus();
  }, [isStreaming]);

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || isStreaming || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter inserts a newline.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  const nearLimit = value.length > MAX_LENGTH * 0.9;

  return (
    <div className="border-t bg-background/85 backdrop-blur-xl">
      <div className="mx-auto w-full max-w-4xl px-4 py-3.5 sm:px-6">
        <div
          className={cn(
            "relative flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-sm transition-all",
            "focus-within:border-primary/50 focus-within:shadow-md focus-within:ring-1 focus-within:ring-primary/20",
            disabled && "opacity-60",
          )}
        >
          <textarea
            ref={textareaRef}
            rows={1}
            value={value}
            maxLength={MAX_LENGTH}
            disabled={disabled}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Ask about ${department} documentation...`}
            aria-label="Your question"
            className={cn(
              "max-h-[200px] min-h-[2.5rem] flex-1 resize-none bg-transparent px-2.5 py-2 text-[0.9375rem] leading-relaxed",
              "placeholder:text-muted-foreground/70 focus:outline-none disabled:cursor-not-allowed",
              "scrollbar-slim",
            )}
          />

          {isStreaming ? (
            <Button
              type="button"
              size="icon"
              variant="secondary"
              onClick={onStop}
              className="mb-0.5 shrink-0 rounded-xl"
              aria-label="Stop generating"
            >
              <Square className="fill-current" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              onClick={submit}
              disabled={!value.trim() || disabled}
              className="mb-0.5 shrink-0 rounded-xl"
              aria-label="Send question"
            >
              <ArrowUp />
            </Button>
          )}
        </div>

        <div className="mt-2 flex items-center justify-between px-1 text-[0.7rem] text-muted-foreground">
          <span>
            Answers come only from {department} documentation and are always
            cited. Verify anything safety-critical against the source.
          </span>
          {nearLimit && (
            <span
              className={cn(
                "shrink-0 tabular-nums",
                value.length >= MAX_LENGTH && "text-destructive",
              )}
            >
              {value.length} / {MAX_LENGTH}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
