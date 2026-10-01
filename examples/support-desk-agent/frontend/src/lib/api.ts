/**
 * API client for the support-desk backend.
 *
 * One function per endpoint of the contract in `./types`, plus `streamChat`,
 * an async generator over the `POST /api/chat` server-sent events.
 *
 * `NEXT_PUBLIC_API_URL` is the backend *origin* (default
 * `http://localhost:8000`); every route lives under `/api`. A trailing `/api`
 * on the variable is tolerated, so a value copied from the
 * full-stack-chat-agent example (which includes it) still works.
 */

import type {
  ActivateResponse,
  ChatEvent,
  GraphData,
  Health,
  MemoryContext,
  OkResponse,
  OntologyDiff,
  OntologyOverview,
  RenameRequest,
  RenameResponse,
  SimilarTrace,
  ThreadDetail,
  ThreadSummary,
  ToolStat,
  TraceDetail,
  TraceSummary,
} from "./types";

export const API_ORIGIN = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
)
  .replace(/\/+$/, "")
  .replace(/\/api$/, "");

export const API_BASE = `${API_ORIGIN}/api`;

/** Thrown for any non-2xx response, so callers can branch on `status`. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** FastAPI errors arrive as `{"detail": ...}`; surface the detail text. */
function errorText(body: string, status: number): string {
  if (!body) return `HTTP ${status}`;
  try {
    const parsed: unknown = JSON.parse(body);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    }
  } catch {
    // Not JSON: fall through to the raw body.
  }
  return body;
}

async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const { json, ...rest } = init ?? {};
  const response = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      Accept: "application/json",
      ...rest.headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(errorText(body, response.status), response.status);
  }
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

type Opts = { signal?: AbortSignal };

export const api = {
  health: (opts?: Opts) => request<Health>("/health", opts),

  threads: {
    list: (opts?: Opts) => request<ThreadSummary[]>("/threads", opts),
    create: (title?: string) =>
      request<ThreadSummary>("/threads", {
        method: "POST",
        json: title ? { title } : {},
      }),
    get: (id: string, opts?: Opts) =>
      request<ThreadDetail>(`/threads/${encodeURIComponent(id)}`, opts),
  },

  memory: {
    context: (threadId: string | null, opts?: Opts) =>
      request<MemoryContext>(
        `/memory/context${query({ thread_id: threadId })}`,
        opts,
      ),
    reviewDuplicate: (sourceId: string, targetId: string, confirm: boolean) =>
      request<OkResponse>("/memory/duplicates/review", {
        method: "POST",
        json: { source_id: sourceId, target_id: targetId, confirm },
      }),
  },

  graph: {
    get: (threadId: string | null, opts?: Opts) =>
      request<GraphData>(`/graph${query({ thread_id: threadId })}`, opts),
    neighbors: (nodeId: string, opts?: Opts) =>
      request<GraphData>(
        `/graph/neighbors/${encodeURIComponent(nodeId)}`,
        opts,
      ),
  },

  ontology: {
    overview: (opts?: Opts) => request<OntologyOverview>("/ontology", opts),
    diff: (fromRevision: number, toRevision: number, opts?: Opts) =>
      request<OntologyDiff>(
        `/ontology/diff${query({
          from_revision: fromRevision,
          to_revision: toRevision,
        })}`,
        opts,
      ),
    rename: (body: RenameRequest) =>
      request<RenameResponse>("/ontology/rename", {
        method: "POST",
        json: body,
      }),
    activate: (versionId: string) =>
      request<ActivateResponse>("/ontology/activate", {
        method: "POST",
        json: { version_id: versionId },
      }),
  },

  traces: {
    list: (threadId: string | null, opts?: Opts) =>
      request<TraceSummary[]>(`/traces${query({ thread_id: threadId })}`, opts),
    get: (id: string, opts?: Opts) =>
      request<TraceDetail>(`/traces/${encodeURIComponent(id)}`, opts),
    similar: (task: string, limit = 5, opts?: Opts) =>
      request<SimilarTrace[]>(`/traces/similar${query({ task, limit })}`, opts),
  },

  toolStats: (opts?: Opts) => request<ToolStat[]>("/tool-stats", opts),
};

/**
 * Parse one SSE event block (the text between blank lines) into its `data`.
 *
 * Handles `data:` with or without the optional space, multi-line data, CRLF
 * line endings (sse-starlette's default separator) and comment/keep-alive
 * lines (`: ping`), which carry no data and are skipped.
 */
function parseEventBlock(block: string): ChatEvent | null {
  const data: string[] = [];
  for (const rawLine of block.split(/\r?\n|\r/)) {
    if (!rawLine.startsWith("data:")) continue;
    const value = rawLine.slice(5);
    data.push(value.startsWith(" ") ? value.slice(1) : value);
  }
  if (data.length === 0) return null;
  try {
    const parsed: unknown = JSON.parse(data.join("\n"));
    if (parsed && typeof parsed === "object" && "type" in parsed) {
      return parsed as ChatEvent;
    }
  } catch {
    // A malformed event is dropped rather than ending the stream.
  }
  return null;
}

/**
 * `POST /api/chat` as an async generator of typed events.
 *
 * `EventSource` cannot POST a body, so the stream is read off a plain `fetch`
 * response. Aborting `signal` (the Stop button, or switching threads) cancels
 * the underlying request.
 */
export async function* streamChat(
  threadId: string,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ thread_id: threadId, message }),
    signal,
  });

  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(errorText(body, response.status), response.status);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("The chat response has no body");

  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Events are separated by a blank line, in any of the three line-ending
      // conventions the SSE spec allows.
      const blocks = buffer.split(/\r\n\r\n|\n\n|\r\r/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const event = parseEventBlock(block);
        if (event) yield event;
      }
    }
    buffer += decoder.decode();
    const tail = parseEventBlock(buffer);
    if (tail) yield tail;
  } finally {
    await reader.cancel().catch(() => {});
  }
}

/** Human-readable message for anything caught from the client. */
export function errorMessage(err: unknown, fallback = "Request failed") {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message || fallback;
  return fallback;
}
