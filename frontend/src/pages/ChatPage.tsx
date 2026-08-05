import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { Composer } from "@/components/chat/Composer";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { Welcome } from "@/components/chat/Welcome";
import { AppShell } from "@/components/layout/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/context/AuthContext";
import { useChat } from "@/hooks/useChat";

export default function ChatPage() {
  const { user } = useAuth();
  const chat = useChat();

  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);

  // Track whether the user has scrolled up to read something. If they have, we
  // must not yank them back down on every streamed token.
  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight;
    setPinnedToBottom(distanceFromBottom < 120);
  }

  // useLayoutEffect so the scroll happens in the same frame as the paint,
  // which avoids a visible jump while tokens stream in.
  useLayoutEffect(() => {
    if (pinnedToBottom) {
      bottomRef.current?.scrollIntoView({ block: "end" });
    }
  }, [chat.turns, pinnedToBottom]);

  // Re-pin when a new conversation is opened.
  useEffect(() => {
    setPinnedToBottom(true);
  }, [chat.activeId]);

  if (!user) return null;

  const activeTitle =
    chat.conversations.find((c) => c.uuid === chat.activeId)?.title ??
    "New chat";

  return (
    <AppShell
      title={chat.activeId ? activeTitle : `${user.department} Assistant`}
      subtitle={
        chat.activeId
          ? `${user.department} knowledge base`
          : "Ask anything in your department's documentation"
      }
      conversations={chat.conversations}
      activeId={chat.activeId}
      onNew={chat.startNewConversation}
      onOpen={chat.openConversation}
      onDelete={chat.deleteConversation}
      onRename={chat.renameConversation}
    >
      <div className="flex h-full flex-col">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="scrollbar-slim min-h-0 flex-1 overflow-y-auto"
        >
          {chat.isLoadingHistory ? (
            <div className="mx-auto w-full max-w-4xl space-y-6 px-4 py-8 sm:px-6">
              {[0, 1, 2].map((i) => (
                <div key={i} className="space-y-2">
                  <Skeleton className="ml-auto h-10 w-1/2" />
                  <Skeleton className="h-24 w-4/5" />
                </div>
              ))}
            </div>
          ) : chat.turns.length === 0 ? (
            <Welcome
              department={user.department}
              displayName={user.full_name || user.username}
              onPick={chat.send}
            />
          ) : (
            <div className="mx-auto w-full max-w-4xl space-y-7 px-4 py-6 sm:px-6 sm:py-8">
              {chat.turns.map((turn) => (
                <MessageBubble
                  key={turn.id}
                  turn={turn}
                  username={user.full_name || user.username}
                  onRate={chat.rate}
                />
              ))}
              <div ref={bottomRef} className="h-1" />
            </div>
          )}
        </div>

        <Composer
          onSend={chat.send}
          onStop={chat.stop}
          isStreaming={chat.isStreaming}
          department={user.department}
        />
      </div>
    </AppShell>
  );
}
