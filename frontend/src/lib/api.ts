/**
 * API client.
 *
 * Two things here are worth knowing:
 *
 * 1. **Token refresh is single-flight.** When several requests 401 at once
 *    (typical on a page with a sidebar + a document list), they all await the
 *    same refresh promise instead of each firing their own, which would
 *    invalidate one another.
 *
 * 2. **Streaming uses fetch, not EventSource.** EventSource cannot send an
 *    Authorization header and cannot POST, so the SSE frames are parsed by
 *    hand out of the response body stream.
 */

import type {
  Analytics,
  ApiErrorShape,
  ConversationDetail,
  ConversationSummary,
  DepartmentInfo,
  DocumentList,
  DocumentRecord,
  IngestionSummary,
  Message,
  StreamHandlers,
  TokenResponse,
  User,
} from "@/types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const API = `${BASE_URL}/api/v1`;

const ACCESS_TOKEN_KEY = "aerolloy.access_token";
const REFRESH_TOKEN_KEY = "aerolloy.refresh_token";
const USER_KEY = "aerolloy.user";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, body: Partial<ApiErrorShape>) {
    super(body.message || "Request failed.");
    this.name = "ApiError";
    this.status = status;
    this.code = body.error || "unknown_error";
    this.details = body.details;
  }

  /** True when the user must sign in again. */
  get isAuthError() {
    return this.status === 401;
  }
}

/* -------------------------------------------------------------------------
 * Token storage
 * ---------------------------------------------------------------------- */

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_TOKEN_KEY);
  },
  get user(): User | null {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as User;
    } catch {
      return null;
    }
  },
  save(tokens: TokenResponse) {
    localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
    localStorage.setItem(USER_KEY, JSON.stringify(tokens.user));
  },
  saveUser(user: User) {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear() {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  },
};

/** Called when refresh fails - AuthContext hooks this up to force a logout. */
let onSessionExpired: (() => void) | null = null;
export function setSessionExpiredHandler(handler: () => void) {
  onSessionExpired = handler;
}

/* -------------------------------------------------------------------------
 * Core request
 * ---------------------------------------------------------------------- */

let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const refresh = tokenStore.refresh;
    if (!refresh) return null;

    try {
      const response = await fetch(`${API}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) return null;

      const tokens = (await response.json()) as TokenResponse;
      tokenStore.save(tokens);
      return tokens.access_token;
    } catch {
      return null;
    } finally {
      // Cleared on the next tick so concurrent callers all observe the result.
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();

  return refreshInFlight;
}

function authHeaders(token = tokenStore.access): HeadersInit {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function parseError(response: Response): Promise<ApiError> {
  let body: Partial<ApiErrorShape> = {};
  try {
    body = await response.json();
  } catch {
    body = { message: response.statusText || "Request failed." };
  }
  return new ApiError(response.status, body);
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  retryOn401 = true,
): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...authHeaders(),
      ...init.headers,
    },
  });

  if (response.status === 401 && retryOn401) {
    const token = await refreshAccessToken();
    if (token) return request<T>(path, init, false);

    tokenStore.clear();
    onSessionExpired?.();
    throw await parseError(response);
  }

  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;

  return (await response.json()) as T;
}

/* -------------------------------------------------------------------------
 * Auth
 * ---------------------------------------------------------------------- */

export const authApi = {
  listDepartments: () => request<DepartmentInfo[]>("/auth/departments"),

  login: (username: string, password: string, department: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, department }),
    }),

  me: () => request<User>("/auth/me"),

  changePassword: (current_password: string, new_password: string) =>
    request<void>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),
};

/* -------------------------------------------------------------------------
 * Chat
 * ---------------------------------------------------------------------- */

export const chatApi = {
  listConversations: () =>
    request<ConversationSummary[]>("/chat/conversations"),

  getConversation: (uuid: string) =>
    request<ConversationDetail>(`/chat/conversations/${uuid}`),

  renameConversation: (uuid: string, title: string) =>
    request<ConversationSummary>(`/chat/conversations/${uuid}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

  deleteConversation: (uuid: string) =>
    request<void>(`/chat/conversations/${uuid}`, { method: "DELETE" }),

  submitFeedback: (messageUuid: string, rating: 1 | -1) =>
    request<Message>(`/chat/messages/${messageUuid}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    }),

  /**
   * Stream an answer. Returns an abort function.
   *
   * Frames arrive as `event: <name>\ndata: <json>\n\n`. We buffer partial
   * chunks because a token can straddle a network read boundary.
   */
  stream(
    question: string,
    conversationId: string | null,
    handlers: StreamHandlers,
  ): () => void {
    const controller = new AbortController();

    void (async () => {
      let token = tokenStore.access;

      const open = async (bearer: string | null) =>
        fetch(`${API}/chat/stream`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            ...authHeaders(bearer),
          },
          body: JSON.stringify({
            question,
            conversation_id: conversationId,
          }),
          signal: controller.signal,
        });

      try {
        let response = await open(token);

        if (response.status === 401) {
          token = await refreshAccessToken();
          if (!token) {
            tokenStore.clear();
            onSessionExpired?.();
            handlers.onError?.({
              message: "Your session has expired. Please sign in again.",
              code: "invalid_token",
            });
            return;
          }
          response = await open(token);
        }

        if (!response.ok || !response.body) {
          const error = await parseError(response);
          handlers.onError?.({ message: error.message, code: error.code });
          return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          buffer = buffer.replace(/\r/g, "");

          // Frames are separated by a blank line.
          let boundary: number;
          while ((boundary = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            dispatchFrame(frame, handlers);
          }
        }

        if (buffer.trim()) {
          dispatchFrame(buffer, handlers);
        }
      } catch (error) {
        if ((error as Error).name === "AbortError") return; // user stopped it
        handlers.onError?.({
          message:
            "Lost connection to the assistant. Please check your network and try again.",
          code: "network_error",
        });
      }
    })();

    return () => controller.abort();
  },
};

function dispatchFrame(frame: string, handlers: StreamHandlers) {
  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    // ":" comment lines are keep-alive pings - ignore them.
  }

  if (dataLines.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }

  switch (eventName) {
    case "start":
      handlers.onStart?.(payload as never);
      break;
    case "stage":
      handlers.onStage?.(payload as never);
      break;
    case "token":
      handlers.onToken?.((payload as { text: string }).text);
      break;
    case "citations":
      handlers.onCitations?.((payload as { citations: never }).citations);
      break;
    case "done":
      handlers.onDone?.(payload as never);
      break;
    case "error":
      handlers.onError?.(payload as never);
      break;
  }
}

/* -------------------------------------------------------------------------
 * Documents
 * ---------------------------------------------------------------------- */

export const documentsApi = {
  list: () => request<DocumentList>("/documents"),

  summary: () => request<IngestionSummary>("/documents/summary"),

  get: (uuid: string) => request<DocumentRecord>(`/documents/${uuid}`),

  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ document: DocumentRecord; message: string }>(
      "/documents/upload",
      { method: "POST", body: form },
    );
  },

  reprocess: (uuid: string) =>
    request<{ document: DocumentRecord; message: string }>(
      `/documents/${uuid}/reprocess`,
      { method: "POST" },
    ),

  remove: (uuid: string) =>
    request<void>(`/documents/${uuid}`, { method: "DELETE" }),

  downloadUrl: (uuid: string) => `${API}/documents/${uuid}/download`,
};

/* -------------------------------------------------------------------------
 * Admin
 * ---------------------------------------------------------------------- */

export const adminApi = {
  analytics: () => request<Analytics>("/admin/analytics"),
  listUsers: () => request<User[]>("/admin/users"),
  deactivateUser: (id: number) =>
    request<User>(`/admin/users/${id}/deactivate`, { method: "POST" }),
  activateUser: (id: number) =>
    request<User>(`/admin/users/${id}/activate`, { method: "POST" }),
  unlockUser: (id: number) =>
    request<User>(`/admin/users/${id}/unlock`, { method: "POST" }),
};
