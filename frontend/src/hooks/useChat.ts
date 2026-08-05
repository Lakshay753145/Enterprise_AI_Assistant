import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, chatApi } from "@/lib/api";
import type {
  ChatTurn,
  ConversationSummary,
  Message,
} from "@/types";

function toTurn(message: Message): ChatTurn {
  return {
    id: message.uuid,
    role: message.role === "assistant" ? "assistant" : "user",
    content: message.content,
    citations: message.citations,
    timings: message.timings,
    answerSource: message.answer_source ?? undefined,
    confidence: message.confidence,
    rewrittenQuery: message.rewritten_query,
    model: message.model,
    feedback: message.feedback,
    createdAt: message.created_at,
  };
}

export function useChat() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  const abortRef = useRef<(() => void) | null>(null);
  // The active conversation as seen by the *stream callbacks*, which close over
  // their creation-time scope. A ref keeps them looking at current state.
  const activeIdRef = useRef<string | null>(null);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  /* --------------------------------------------------------------------- */
  /* Conversations                                                          */
  /* --------------------------------------------------------------------- */

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await chatApi.listConversations());
    } catch {
      /* the sidebar is non-critical; leave what is already shown */
    }
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  const openConversation = useCallback(async (uuid: string) => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
    setIsLoadingHistory(true);
    setActiveId(uuid);

    try {
      const detail = await chatApi.getConversation(uuid);
      setTurns(detail.messages.map(toTurn));
    } catch {
      setTurns([]);
    } finally {
      setIsLoadingHistory(false);
    }
  }, []);

  const startNewConversation = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
    setActiveId(null);
    setTurns([]);
  }, []);

  const deleteConversation = useCallback(
    async (uuid: string) => {
      await chatApi.deleteConversation(uuid);
      setConversations((prev) => prev.filter((c) => c.uuid !== uuid));
      if (activeIdRef.current === uuid) {
        setActiveId(null);
        setTurns([]);
      }
    },
    [],
  );

  const renameConversation = useCallback(
    async (uuid: string, title: string) => {
      const updated = await chatApi.renameConversation(uuid, title);
      setConversations((prev) =>
        prev.map((c) => (c.uuid === uuid ? updated : c)),
      );
    },
    [],
  );

  /* --------------------------------------------------------------------- */
  /* Sending                                                                */
  /* --------------------------------------------------------------------- */

  const send = useCallback(
    (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || isStreaming) return;

      const now = new Date().toISOString();
      const pendingId = `pending-${Date.now()}`;

      setTurns((prev) => [
        ...prev,
        {
          id: `user-${Date.now()}`,
          role: "user",
          content: trimmed,
          createdAt: now,
        },
        {
          id: pendingId,
          role: "assistant",
          content: "",
          createdAt: now,
          isStreaming: true,
          stage: null,
          citations: [],
        },
      ]);
      setIsStreaming(true);

      const patchPending = (patch: Partial<ChatTurn>) =>
        setTurns((prev) =>
          prev.map((turn) =>
            turn.id === pendingId ? { ...turn, ...patch } : turn,
          ),
        );

      abortRef.current = chatApi.stream(trimmed, activeIdRef.current, {
        onStart: (data) => {
          if (!activeIdRef.current) {
            activeIdRef.current = data.conversation_id;
            setActiveId(data.conversation_id);
          }
        },

        onStage: (data) => patchPending({ stage: data.label }),

        // Append rather than replace: this fires once per token.
        onToken: (text) =>
          setTurns((prev) =>
            prev.map((turn) =>
              turn.id === pendingId
                ? { ...turn, content: turn.content + text, stage: null }
                : turn,
            ),
          ),

        onCitations: (citations) => patchPending({ citations }),

        onDone: (data) => {
          setTurns((prev) =>
            prev.map((turn) =>
              turn.id === pendingId
                ? {
                    ...turn,
                    id: data.message_id,
                    isStreaming: false,
                    stage: null,
                    timings: data.timings,
                    answerSource: data.answer_source,
                    confidence: data.confidence,
                    grounded: data.grounded,
                    rewrittenQuery: data.rewritten_query,
                    model: data.model,
                  }
                : turn,
            ),
          );
          setIsStreaming(false);
          abortRef.current = null;
          void loadConversations();
        },

        onError: (data) => {
          patchPending({
            isStreaming: false,
            stage: null,
            error: data.message,
            content: "",
          });
          setIsStreaming(false);
          abortRef.current = null;
        },
      });
    },
    [isStreaming, loadConversations],
  );

  const stop = useCallback(() => {
    abortRef.current?.();
    abortRef.current = null;
    setIsStreaming(false);
    setTurns((prev) =>
      prev.map((turn) =>
        turn.isStreaming
          ? {
              ...turn,
              isStreaming: false,
              stage: null,
              content: turn.content || "",
              error: turn.content ? null : "Stopped before any answer arrived.",
            }
          : turn,
      ),
    );
  }, []);

  const rate = useCallback(async (messageId: string, rating: 1 | -1) => {
    // Optimistic: the thumb should respond instantly.
    setTurns((prev) =>
      prev.map((turn) =>
        turn.id === messageId ? { ...turn, feedback: rating } : turn,
      ),
    );
    try {
      await chatApi.submitFeedback(messageId, rating);
    } catch (error) {
      if (error instanceof ApiError) {
        setTurns((prev) =>
          prev.map((turn) =>
            turn.id === messageId ? { ...turn, feedback: null } : turn,
          ),
        );
      }
    }
  }, []);

  // Abort any in-flight stream if the component unmounts mid-answer.
  useEffect(() => () => abortRef.current?.(), []);

  return {
    turns,
    conversations,
    activeId,
    isStreaming,
    isLoadingHistory,
    send,
    stop,
    rate,
    openConversation,
    startNewConversation,
    deleteConversation,
    renameConversation,
    reloadConversations: loadConversations,
  };
}
