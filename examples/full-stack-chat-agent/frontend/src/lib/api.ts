/**
 * API client for the chat backend.
 *
 * Every function returns a typed result, and `streamChat` is an async
 * generator over the backend's SSE contract (see `SSEEvent` in `./types`).
 */

import type {
  ApiChatMessage,
  ApiThreadWithMessages,
  Entity,
  Health,
  MemoryContext,
  MemoryGraph,
  Message,
  Preference,
  SSEEvent,
  Thread,
  ThreadWithMessages,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

// `/health` is mounted at the application root, not under the `/api` prefix.
const SERVER_BASE = API_BASE.replace(/\/api\/?$/, "");

/** Thrown by `fetchAPI` so callers can tell "missing" apart from "broken". */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Generic fetch wrapper with error handling.
 */
async function fetchAPI<T>(
  endpoint: string,
  options?: RequestInit,
  base: string = API_BASE,
): Promise<T> {
  const response = await fetch(`${base}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(body || `HTTP ${response.status}`, response.status);
  }

  return response.json();
}

/** The backend serialises tool calls as `tool_calls`; the UI uses `toolCalls`. */
function toMessage(message: ApiChatMessage): Message {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    timestamp: message.timestamp,
    toolCalls: message.tool_calls ?? [],
  };
}

// Health API (root-mounted, reports whether memory is actually connected)
export const health = {
  get: () => fetchAPI<Health>("/health", undefined, SERVER_BASE),
};

// Thread API
export const threads = {
  list: () => fetchAPI<Thread[]>("/threads"),

  create: (title?: string) =>
    fetchAPI<Thread>("/threads", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),

  get: async (id: string): Promise<ThreadWithMessages> => {
    const thread = await fetchAPI<ApiThreadWithMessages>(`/threads/${id}`);
    return { ...thread, messages: (thread.messages ?? []).map(toMessage) };
  },

  delete: (id: string) =>
    fetchAPI<{ status: string }>(`/threads/${id}`, { method: "DELETE" }),

  update: (id: string, title: string) =>
    fetchAPI<Thread>(`/threads/${id}?title=${encodeURIComponent(title)}`, {
      method: "PATCH",
    }),
};

// Preferences API
export const preferences = {
  list: (category?: string) =>
    fetchAPI<Preference[]>(
      `/preferences${category ? `?category=${encodeURIComponent(category)}` : ""}`,
    ),

  add: (category: string, preference: string, context?: string) =>
    fetchAPI<Preference>("/preferences", {
      method: "POST",
      body: JSON.stringify({ category, preference, context }),
    }),

  delete: (id: string) =>
    fetchAPI<{ status: string }>(`/preferences/${id}`, { method: "DELETE" }),
};

// Entities API
export const entities = {
  list: (type?: string, query?: string) => {
    const params = new URLSearchParams();
    if (type) params.set("type", type);
    if (query) params.set("query", query);
    const queryStr = params.toString();
    return fetchAPI<Entity[]>(`/entities${queryStr ? `?${queryStr}` : ""}`);
  },
};

// Memory API
export const memory = {
  getContext: (threadId?: string, query?: string) => {
    const params = new URLSearchParams();
    if (threadId) params.set("thread_id", threadId);
    if (query) params.set("query", query);
    const queryStr = params.toString();
    return fetchAPI<MemoryContext>(
      `/memory/context${queryStr ? `?${queryStr}` : ""}`,
    );
  },

  getGraph: (threadId?: string) => {
    const params = new URLSearchParams();
    if (threadId) {
      params.set("session_id", threadId);
    }
    const query = params.toString();
    return fetchAPI<MemoryGraph>(`/memory/graph${query ? `?${query}` : ""}`);
  },

  getNodeNeighbors: (nodeId: string, depth: number = 1, limit: number = 50) => {
    const params = new URLSearchParams({
      depth: String(depth),
      limit: String(limit),
    });
    return fetchAPI<MemoryGraph>(
      `/memory/graph/neighbors/${encodeURIComponent(nodeId)}?${params}`,
    );
  },
};

/**
 * Chat API with SSE streaming.
 *
 * `EventSource` cannot POST a JSON body, so the stream is read off a plain
 * `fetch` response. `signal` is what makes a turn cancellable: the Stop
 * button and the thread-change cleanup in `useChat` both abort through it.
 */
export async function* streamChat(
  threadId: string,
  message: string,
  memoryEnabled: boolean = true,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      thread_id: threadId,
      message,
      memory_enabled: memoryEnabled,
    }),
    signal,
  });

  if (!response.ok) {
    throw new ApiError(`HTTP ${response.status}`, response.status);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("No response body");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE messages
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6);
          if (data) {
            try {
              const event: SSEEvent = JSON.parse(data);
              yield event;
            } catch {
              // Skip invalid JSON
            }
          }
        }
      }
    }
  } finally {
    // Cancel rather than only release: on an abort or an early `break` from
    // the consumer this also tears down the underlying HTTP request.
    await reader.cancel().catch(() => {});
  }
}

export const api = {
  health,
  threads,
  preferences,
  entities,
  memory,
  streamChat,
};
