/**
 * Typed client for the retail assistant backend (see ../../backend/main.py).
 *
 * Every response shape the backend returns is declared here, so a drift
 * between the FastAPI projection and the UI shows up as a type error in
 * `npm run typecheck` rather than as an empty card in the browser.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * The demo shoppers the header switcher offers.
 *
 * `user_id` reaches the backend on every chat turn, where it upserts a
 * `(:User {identifier})` node (`client.users.upsert_user`) and resolves the
 * shopper's session id. Each shopper therefore gets their own short-term
 * memory and their own slice of the memory graph.
 */
export const DEMO_USERS = [
  { id: "guest", label: "Guest" },
  { id: "ada", label: "Ada" },
  { id: "kenji", label: "Kenji" },
] as const;

export type DemoUserId = (typeof DEMO_USERS)[number]["id"];

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp?: string;
}

export interface Product {
  id: string;
  name: string;
  description: string;
  price: number;
  category: string;
  brand: string;
  in_stock: boolean;
  inventory?: number;
  image_url?: string;
  attributes?: Record<string, string>;
  relevance_score?: number;
  /** Alias the backend adds for the UI; same value as `relevance_score`. */
  score?: number;
}

/**
 * `/products/{id}/related` projects a subset of `Product` plus the
 * relationship types that connected the two — it is not a `Product`
 * (no `in_stock`, no `attributes`).
 */
export type RelatedProduct = Pick<
  Product,
  "id" | "name" | "description" | "price" | "category" | "brand" | "image_url"
> & {
  /** Relationship types on the path, e.g. `["SIMILAR_TO", "MADE_BY"]`. */
  relationships?: string[];
  relevance_score?: number;
};

export interface MemoryMessage {
  id: string;
  role: string;
  content: string;
  timestamp?: string | null;
}

export interface MemoryEntity {
  id: string;
  name: string;
  type: string;
  description?: string | null;
}

export interface MemoryPreference {
  id: string;
  category: string;
  preference: string;
  context?: string | null;
  confidence?: number;
}

export interface MemoryTrace {
  id: string;
  task: string;
  outcome: string;
  steps: number;
}

/** The three memory layers, exactly as `/memory/context` returns them. */
export interface MemoryContext {
  short_term: MemoryMessage[];
  long_term: {
    entities: MemoryEntity[];
    preferences: MemoryPreference[];
  };
  reasoning: MemoryTrace[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  properties?: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/**
 * The SSE frames `/chat` emits, as a discriminated union.
 *
 * Switching on `event.event` then gives an exhaustively checked handler in
 * `ChatInterface` — the previous client guessed the frame type by sniffing
 * which keys the payload happened to carry.
 */
export type ChatStreamEvent =
  | { event: "token"; data: { content: string } }
  | {
      event: "tool_call";
      data: {
        call_id?: string;
        name: string;
        arguments?: string | Record<string, unknown>;
      };
    }
  | {
      event: "tool_result";
      data: { call_id?: string; name: string; result: string };
    }
  | { event: "done"; data: { session_id: string } }
  | { event: "error"; data: { error: string } };

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

/**
 * Validate one `event:`/`data:` pair into the union above.
 *
 * Returns `null` for anything unrecognised (for example sse-starlette's
 * keep-alive comments) so the caller can skip it without a cast.
 */
function toChatStreamEvent(
  name: string,
  payload: unknown
): ChatStreamEvent | null {
  const data = asRecord(payload);
  if (!data) return null;

  switch (name) {
    case "token": {
      const content = asString(data.content);
      return content === undefined ? null : { event: "token", data: { content } };
    }
    case "tool_call": {
      const toolName = asString(data.name);
      if (!toolName) return null;
      const args = data.arguments;
      return {
        event: "tool_call",
        data: {
          call_id: asString(data.call_id),
          name: toolName,
          arguments:
            typeof args === "string" ? args : (asRecord(args) ?? undefined),
        },
      };
    }
    case "tool_result": {
      const toolName = asString(data.name);
      if (!toolName) return null;
      return {
        event: "tool_result",
        data: {
          call_id: asString(data.call_id),
          name: toolName,
          result: asString(data.result) ?? "",
        },
      };
    }
    case "done": {
      return { event: "done", data: { session_id: asString(data.session_id) ?? "" } };
    }
    case "error": {
      return { event: "error", data: { error: asString(data.error) ?? "Unknown error" } };
    }
    default:
      return null;
  }
}

/** Tool arguments arrive as a JSON string (or, rarely, an object). */
export function parseToolArguments(
  args: string | Record<string, unknown> | undefined
): Record<string, unknown> {
  if (!args) return {};
  if (typeof args !== "string") return args;
  try {
    const parsed: unknown = JSON.parse(args);
    return asRecord(parsed) ?? { value: args };
  } catch {
    return { value: args };
  }
}

/**
 * Stream a chat turn.
 *
 * `POST` rules out `EventSource` (which is GET-only), so this reads the
 * response body and parses SSE frames itself: `event:` names are carried
 * across lines and a frame is dispatched on the blank line that ends it.
 */
export async function* streamChat(
  message: string,
  sessionId?: string,
  userId?: string
): AsyncGenerator<ChatStreamEvent> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      user_id: userId,
    }),
  });

  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.statusText}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("No response body");
  }

  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];

  function flush(): ChatStreamEvent | null {
    if (dataLines.length === 0) {
      eventName = "message";
      return null;
    }
    const raw = dataLines.join("\n");
    const name = eventName;
    dataLines = [];
    eventName = "message";
    try {
      return toChatStreamEvent(name, JSON.parse(raw));
    } catch {
      return null; // malformed JSON: skip the frame, keep the stream
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (line === "") {
          const event = flush();
          if (event) yield event;
        } else if (line.startsWith(":")) {
          continue; // keep-alive comment
        } else if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
    }

    // A stream cut short without its terminating blank line still has a frame.
    const trailing = flush();
    if (trailing) yield trailing;
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

/** Send a chat turn without streaming. */
export async function sendChat(
  message: string,
  sessionId?: string,
  userId?: string
): Promise<{ response: string; session_id: string }> {
  const response = await fetch(`${API_BASE}/chat/sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      user_id: userId,
    }),
  });

  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.statusText}`);
  }

  return response.json();
}

/** Read all three memory layers for a session. */
export async function getMemoryContext(
  sessionId: string,
  query?: string
): Promise<MemoryContext> {
  const params = new URLSearchParams({ session_id: sessionId });
  if (query) params.append("query", query);

  const response = await fetch(`${API_BASE}/memory/context?${params}`);

  if (!response.ok) {
    throw new Error(`Failed to get memory context: ${response.statusText}`);
  }

  return response.json();
}

/** Read the session subgraph (or a neighbourhood around one entity). */
export async function getMemoryGraph(
  sessionId: string,
  centerEntity?: string,
  maxHops?: number
): Promise<GraphData> {
  const params = new URLSearchParams({ session_id: sessionId });
  if (centerEntity) params.append("center_entity", centerEntity);
  if (maxHops) params.append("max_hops", maxHops.toString());

  const response = await fetch(`${API_BASE}/memory/graph?${params}`);

  if (!response.ok) {
    throw new Error(`Failed to get memory graph: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Read learned preferences.
 *
 * Note: the backend's preference store is global in this demo — the
 * `session_id` is accepted but `search_preferences` is not tenant-scoped, so
 * every shopper sees the same list. The UI labels it as global for that
 * reason; see the frontend README.
 */
export async function getPreferences(
  sessionId: string,
  category?: string
): Promise<{ preferences: MemoryPreference[] }> {
  const params = new URLSearchParams({ session_id: sessionId });
  if (category) params.append("category", category);

  const response = await fetch(`${API_BASE}/memory/preferences?${params}`);

  if (!response.ok) {
    throw new Error(`Failed to get preferences: ${response.statusText}`);
  }

  return response.json();
}

/**
 * Record a preference explicitly (the deterministic counterpart to the
 * agent's own `remember_preference` tool — no LLM call involved).
 */
export async function addPreference(input: {
  category: string;
  preference: string;
  context?: string;
  sessionId?: string;
  userId?: string;
}): Promise<{
  id: string;
  category: string;
  preference: string;
  session_id: string;
}> {
  const response = await fetch(`${API_BASE}/memory/preferences`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      category: input.category,
      preference: input.preference,
      context: input.context || null,
      session_id: input.sessionId,
      user_id: input.userId,
    }),
  });

  if (!response.ok) {
    throw new Error(`Failed to add preference: ${response.statusText}`);
  }

  return response.json();
}

/** Search the product catalog (the same call the agent's tool makes). */
export async function searchProducts(
  query: string,
  options?: {
    category?: string;
    brand?: string;
    maxPrice?: number;
    limit?: number;
  }
): Promise<{ products: Product[]; total: number; search_mode?: string }> {
  const params = new URLSearchParams({ query });
  if (options?.category) params.append("category", options.category);
  if (options?.brand) params.append("brand", options.brand);
  if (options?.maxPrice)
    params.append("max_price", options.maxPrice.toString());
  if (options?.limit) params.append("limit", options.limit.toString());

  const response = await fetch(`${API_BASE}/products/search?${params}`);

  if (!response.ok) {
    throw new Error(`Product search failed: ${response.statusText}`);
  }

  return response.json();
}

/** Fetch one product by id. */
export async function getProduct(productId: string): Promise<Product> {
  const response = await fetch(`${API_BASE}/products/${productId}`);

  if (!response.ok) {
    throw new Error(`Failed to get product: ${response.statusText}`);
  }

  return response.json();
}

/** Relationship filters `/products/{id}/related` accepts. */
export type RelationshipKind =
  | "similar"
  | "bought_together"
  | "category"
  | "brand"
  | "attribute";

/** Fetch graph neighbours of a product. */
export async function getRelatedProducts(
  productId: string,
  options?: { limit?: number; relationshipType?: RelationshipKind }
): Promise<{ source_product_id: string; related_products: RelatedProduct[] }> {
  const params = new URLSearchParams();
  if (options?.limit) params.append("limit", options.limit.toString());
  if (options?.relationshipType)
    params.append("relationship_type", options.relationshipType);

  const query = params.toString();
  const response = await fetch(
    `${API_BASE}/products/${productId}/related${query ? `?${query}` : ""}`
  );

  if (!response.ok) {
    throw new Error(`Failed to get related products: ${response.statusText}`);
  }

  return response.json();
}

/** Backend liveness plus whether vector search is configured. */
export async function checkHealth(): Promise<{
  status: string;
  database: string;
  vector_search?: string;
}> {
  const response = await fetch(`${API_BASE}/health`);
  return response.json();
}
