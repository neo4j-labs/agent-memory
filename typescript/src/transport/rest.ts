/**
 * RestTransport — talks to the hosted Neo4j Agent Memory Service REST API.
 *
 * Endpoint should be the v1 root, e.g. `https://memory.neo4jlabs.com/v1`.
 * Routes the bridge-style `request(method, params)` calls to the appropriate
 * REST endpoints with snake_case ↔ camelCase translation on the wire.
 *
 * Hosted-native methods (added in Volume 5 of the spec) are routed natively.
 * Legacy bridge-only methods (add_preference, add_fact, etc.) throw
 * NotSupportedError because the hosted service has no equivalent.
 */

import {
  AuthenticationError,
  ConnectionError,
  NotFoundError,
  NotSupportedError,
  TransportError,
} from "../errors.js";
import {
  defaultUserAgent,
  extractRequestId,
  supportsUserAgentHeader,
  type Logger,
} from "../observability.js";
import { camelToSnake, snakeToCamel } from "./casing.js";
import type { Transport } from "./index.js";

/** Strip trailing `/` from a URL without using a polynomial regex. */
function trimTrailingSlashes(s: string): string {
  let end = s.length;
  while (end > 0 && s.charCodeAt(end - 1) === 47) end--;
  return s.slice(0, end);
}

/** Monotonic-ish timestamp for duration measurement. Works on all runtimes. */
function nowMs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

/** snake_case → camelCase, single-key form (no recursion into objects). */
function snakeToCamelKey(s: string): string {
  return s.replace(/_([a-z0-9])/g, (_, c) => (c as string).toUpperCase());
}

export type TokenProvider = () => string | Promise<string>;

export interface RestTransportOptions {
  /** Base URL — should end in /v1, e.g. https://memory.neo4jlabs.com/v1 */
  endpoint: string;

  /** Static `nams_*` API key. */
  apiKey?: string;

  /** Token provider (overrides apiKey if both supplied) — for OAuth refresh flows. */
  tokenProvider?: TokenProvider;

  /** Request timeout in milliseconds. Default: 30000. */
  timeout?: number;

  /** Additional headers to include in every request. */
  headers?: Record<string, string>;

  /** Per-request logger; see {@link Logger}. */
  logger?: Logger;
}

type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

interface RestCall {
  method: HttpMethod;
  /** Path template; tokens like `{conversationId}` are replaced from params (camel-cased). */
  path: string;
  /** Param names that go in the URL path (camelCase) — stripped from the body. */
  pathParams?: string[];
  /** Param names that become query string parameters (camelCase). */
  queryParams?: string[];
  /** GET/DELETE → no body. */
  hasBody?: boolean;
  /**
   * Send the verbatim object passed as the `body` param as the request body,
   * bypassing the snake→camel key conversion. Required for the ontology
   * sub-API, which (unlike the rest of the hosted REST surface) speaks
   * snake_case for request bodies (`entity_types`, `version_id`, …).
   */
  snakeBody?: boolean;
  /** Optional response shaper for endpoints whose payload doesn't match bridge wire. */
  shape?: (raw: unknown, camelParams: Record<string, unknown>) => unknown;
}

/**
 * Bridge-method-name → REST-call mapping.
 *
 * Keys are snake_case bridge method names. Values describe how to dispatch.
 */
const ROUTES: Record<string, RestCall | "noop" | "unsupported"> = {
  // Lifecycle ----------------------------------------------------------------
  setup: "noop",
  teardown: "noop",
  // Hosted has no global clear; we delete every conversation owned by the API
  // key. This is best-effort — see clearAllData() for the implementation.
  clear_all_data: "noop",

  // Short-Term — legacy bridge methods (mapped where a clean REST equivalent
  // exists; bridge sessionId is treated as the conversationId UUID).
  add_message: {
    method: "POST",
    path: "/conversations/{sessionId}/messages",
    pathParams: ["sessionId"],
    hasBody: true,
  },
  get_conversation: {
    method: "GET",
    path: "/conversations/{sessionId}/messages",
    pathParams: ["sessionId"],
    queryParams: ["limit"],
    shape: (raw, p) => {
      const messages = (raw as { messages?: unknown[] })?.messages ?? raw ?? [];
      return {
        id: p["sessionId"],
        session_id: p["sessionId"],
        messages,
        created_at: null,
      };
    },
  },
  list_sessions: {
    method: "GET",
    path: "/conversations",
    queryParams: ["limit"],
    shape: (raw) => {
      const conversations = (raw as { conversations?: unknown[] })?.conversations ?? [];
      return conversations.map((c) => {
        const conv = c as Record<string, unknown>;
        return {
          session_id: conv["id"],
          message_count: conv["messageCount"] ?? 0,
          created_at: conv["createdAt"],
          updated_at: conv["updatedAt"],
        };
      });
    },
  },
  search_messages: {
    method: "POST",
    path: "/conversations/{sessionId}/search",
    pathParams: ["sessionId"],
    hasBody: true,
    shape: (raw) => (raw as { messages?: unknown[] })?.messages ?? [],
  },
  clear_session: {
    method: "DELETE",
    path: "/conversations/{sessionId}",
    pathParams: ["sessionId"],
  },
  delete_message: "unsupported",

  // Long-Term — legacy mapped methods
  add_entity: {
    method: "POST",
    path: "/entities",
    hasBody: true,
  },
  search_entities: {
    method: "POST",
    path: "/entities/search",
    hasBody: true,
    shape: (raw) => (raw as { entities?: unknown[] })?.entities ?? [],
  },
  add_preference: "unsupported",
  add_fact: "unsupported",
  search_preferences: "unsupported",
  get_entity_by_name: "unsupported",
  get_related_entities: "unsupported",
  add_relationship: "unsupported",
  merge_duplicate_entities: "unsupported",

  // Reasoning — legacy not directly representable in REST
  start_trace: "unsupported",
  add_step: "unsupported",
  record_tool_call: {
    method: "POST",
    path: "/reasoning/tool-calls",
    hasBody: true,
  },
  complete_trace: "unsupported",
  get_trace_with_steps: "unsupported",
  list_traces: "unsupported",
  get_tool_stats: "unsupported",
  get_similar_traces: "unsupported",

  // ---- Hosted-native methods (Volume 5 / Platinum tier) --------------------
  create_conversation: {
    method: "POST",
    path: "/conversations",
    hasBody: true,
  },
  list_conversations: {
    method: "GET",
    path: "/conversations",
    queryParams: ["limit", "user_id"],
    shape: (raw) => (raw as { conversations?: unknown[] })?.conversations ?? raw,
  },
  get_conversation_metadata: {
    method: "GET",
    path: "/conversations/{conversationId}",
    pathParams: ["conversationId"],
  },
  delete_conversation: {
    method: "DELETE",
    path: "/conversations/{conversationId}",
    pathParams: ["conversationId"],
  },
  get_context: {
    method: "GET",
    path: "/conversations/{conversationId}/context",
    pathParams: ["conversationId"],
  },
  bulk_add_messages: {
    method: "POST",
    path: "/conversations/{conversationId}/messages/bulk",
    pathParams: ["conversationId"],
    hasBody: true,
    shape: (raw) => (raw as { messages?: unknown[] })?.messages ?? raw,
  },
  get_observations: {
    method: "GET",
    path: "/conversations/{conversationId}/observations",
    pathParams: ["conversationId"],
    queryParams: ["limit"],
    shape: (raw) => (raw as { observations?: unknown[] })?.observations ?? raw,
  },
  get_reflections: {
    method: "GET",
    path: "/conversations/{conversationId}/reflections",
    pathParams: ["conversationId"],
    shape: (raw) => (raw as { reflections?: unknown[] })?.reflections ?? raw,
  },
  list_entities: {
    method: "GET",
    path: "/entities",
    queryParams: ["type", "limit"],
    shape: (raw) => (raw as { entities?: unknown[] })?.entities ?? raw,
  },
  get_entity: {
    method: "GET",
    path: "/entities/{entityId}",
    pathParams: ["entityId"],
  },
  update_entity: {
    method: "PUT",
    path: "/entities/{entityId}",
    pathParams: ["entityId"],
    hasBody: true,
  },
  delete_entity: {
    method: "DELETE",
    path: "/entities/{entityId}",
    pathParams: ["entityId"],
  },
  set_entity_feedback: {
    method: "PUT",
    path: "/entities/{entityId}/feedback",
    pathParams: ["entityId"],
    hasBody: true,
  },
  get_entity_history: {
    method: "GET",
    path: "/entities/{entityId}/history",
    pathParams: ["entityId"],
  },
  merge_entities: {
    method: "POST",
    path: "/entities/{sourceId}/merge",
    pathParams: ["sourceId"],
    hasBody: true,
  },
  get_entity_graph: {
    method: "GET",
    path: "/entities/graph",
  },
  expand_graph: {
    method: "POST",
    path: "/graph/expand",
    hasBody: true,
  },
  explain_step: {
    method: "GET",
    path: "/reasoning/explain/{stepId}",
    pathParams: ["stepId"],
  },
  get_trace_by_conversation: {
    method: "GET",
    path: "/reasoning/trace/{conversationId}",
    pathParams: ["conversationId"],
  },
  get_entity_provenance: {
    method: "GET",
    path: "/reasoning/provenance/{entityId}",
    pathParams: ["entityId"],
  },
  record_step: {
    method: "POST",
    path: "/reasoning/steps",
    hasBody: true,
  },
  list_steps: {
    method: "GET",
    path: "/reasoning/steps",
    queryParams: ["conversation_id"],
    shape: (raw) => (raw as { steps?: unknown[] })?.steps ?? raw,
  },
  cypher_query: {
    method: "POST",
    path: "/query",
    hasBody: true,
  },

  // Auth
  list_api_keys: {
    method: "GET",
    path: "/auth/api-keys",
    queryParams: ["workspace_id"],
    shape: (raw) => {
      const r = raw as { keys?: unknown[]; api_keys?: unknown[] };
      return r?.keys ?? r?.api_keys ?? raw;
    },
  },
  create_api_key: {
    method: "POST",
    path: "/auth/api-keys",
    hasBody: true,
  },
  revoke_api_key: {
    method: "DELETE",
    path: "/auth/api-keys/{keyId}",
    pathParams: ["keyId"],
  },
  reveal_api_key: {
    method: "GET",
    path: "/auth/api-keys/{keyId}/reveal",
    pathParams: ["keyId"],
    queryParams: ["workspace_id"],
  },
  rotate_api_key: {
    method: "POST",
    path: "/auth/api-keys/{keyId}/rotate",
    pathParams: ["keyId"],
  },
  refresh_access_token: {
    method: "POST",
    path: "/auth/refresh",
    hasBody: true,
  },

  // Ontologies — snake_case sub-API (verified against staging; absent from the
  // OpenAPI spec). Bodies are sent verbatim via `snakeBody`.
  list_ontologies: { method: "GET", path: "/ontologies" },
  get_ontology: { method: "GET", path: "/ontologies/{id}", pathParams: ["id"] },
  get_active_ontology: { method: "GET", path: "/ontologies/active" },
  clone_ontology: {
    method: "POST",
    path: "/ontologies/{name}/clone",
    pathParams: ["name"],
  },
  create_ontology: { method: "POST", path: "/ontologies", hasBody: true, snakeBody: true },
  update_ontology: {
    method: "PUT",
    path: "/ontologies/{id}",
    pathParams: ["id"],
    hasBody: true,
    snakeBody: true,
  },
  activate_ontology: {
    method: "POST",
    path: "/ontologies/active",
    hasBody: true,
    snakeBody: true,
  },
  delete_ontology: { method: "DELETE", path: "/ontologies/{id}", pathParams: ["id"] },
  import_ontology: { method: "POST", path: "/ontologies/import", hasBody: true, snakeBody: true },
  diff_ontology: {
    method: "GET",
    path: "/ontologies/{id}/diff",
    pathParams: ["id"],
    queryParams: ["from", "to"],
  },
  migrate_ontology: {
    method: "POST",
    path: "/ontologies/{id}/migrate",
    pathParams: ["id"],
    hasBody: true,
    snakeBody: true,
  },
  get_ontology_migration: {
    method: "GET",
    path: "/ontologies/migrations/{jobId}",
    pathParams: ["jobId"],
  },
};

export class RestTransport implements Transport {
  private readonly endpoint: string;
  private readonly apiKey?: string;
  private readonly tokenProvider?: TokenProvider;
  private readonly timeout: number;
  private readonly headers: Record<string, string>;
  private readonly logger?: Logger;

  constructor(options: RestTransportOptions) {
    this.endpoint = trimTrailingSlashes(options.endpoint);
    this.apiKey = options.apiKey;
    this.tokenProvider = options.tokenProvider;
    this.timeout = options.timeout ?? 30_000;
    this.headers = options.headers ?? {};
    this.logger = options.logger;
  }

  async connect(): Promise<void> {
    const url = `${this.endpoint}/conversations?limit=1`;
    const start = nowMs();
    this.emit({ kind: "request", method: "connect", url, httpMethod: "GET" });
    let response: Response;
    try {
      response = await fetch(url, {
        method: "GET",
        headers: await this.buildHeaders(),
        signal: AbortSignal.timeout(this.timeout),
      });
    } catch (error) {
      const durationMs = nowMs() - start;
      if (error instanceof TypeError) {
        const err = new ConnectionError(
          `Failed to connect to ${this.endpoint}: ${(error as Error).message}`,
          { cause: error },
        );
        this.emit({ kind: "error", method: "connect", url, durationMs, message: err.message });
        throw err;
      }
      if (error instanceof DOMException && error.name === "TimeoutError") {
        const err = new ConnectionError(
          `Connection to ${this.endpoint} timed out after ${this.timeout}ms`,
          { cause: error },
        );
        this.emit({ kind: "error", method: "connect", url, durationMs, message: err.message });
        throw err;
      }
      throw error;
    }
    const durationMs = nowMs() - start;
    const requestId = extractRequestId(response.headers);
    if (response.status === 401 || response.status === 403) {
      const err = new AuthenticationError(
        `Authentication failed against ${this.endpoint}: ${response.status} ${response.statusText}`,
        { requestId },
      );
      this.emit({
        kind: "error",
        method: "connect",
        url,
        status: response.status,
        requestId,
        durationMs,
        message: err.message,
      });
      throw err;
    }
    if (!response.ok && response.status >= 500) {
      const err = new ConnectionError(
        `Server error from ${this.endpoint}: ${response.status} ${response.statusText}`,
        { requestId },
      );
      this.emit({
        kind: "error",
        method: "connect",
        url,
        status: response.status,
        requestId,
        durationMs,
        message: err.message,
      });
      throw err;
    }
    this.emit({
      kind: "response",
      method: "connect",
      url,
      status: response.status,
      requestId,
      durationMs,
    });
  }

  async close(): Promise<void> {}

  async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    const route = ROUTES[method];
    if (!route) {
      throw new NotSupportedError(
        `Method '${method}' is not implemented by RestTransport. ` +
          `Use BridgeTransport for full TCK conformance, or call a hosted-native method.`,
      );
    }
    if (route === "noop") return undefined as T;
    if (route === "unsupported") {
      throw new NotSupportedError(
        `Method '${method}' has no equivalent in the hosted Neo4j Agent Memory REST API. ` +
          `It is supported by BridgeTransport only.`,
      );
    }

    const original = params ?? {};
    const camelParams = snakeToCamel<Record<string, unknown>>(original);

    // Substitute path params (placeholders match camelCase route literals).
    let path = route.path;
    const consumed = new Set<string>();
    for (const name of route.pathParams ?? []) {
      const v = camelParams[name];
      if (v === undefined || v === null || v === "") {
        throw new TransportError(
          `Missing required path parameter '${name}' for method '${method}'`,
          400,
          camelParams,
        );
      }
      path = path.replace(`{${name}}`, encodeURIComponent(String(v)));
      consumed.add(name);
    }

    // Build query string. The hosted REST API uses snake_case for query
    // params (conversation_id, workspace_id) — NOT camelCase. Look up by
    // the snake_case name from the original params; fall back to the
    // camelCase form if the caller already passed it that way.
    const queryEntries: [string, string][] = [];
    for (const name of route.queryParams ?? []) {
      let v: unknown = original[name];
      if (v === undefined || v === null) {
        const camel = snakeToCamelKey(name);
        v = camelParams[camel];
      }
      if (v !== undefined && v !== null) {
        queryEntries.push([name, String(v)]);
        consumed.add(name);
        consumed.add(snakeToCamelKey(name));
      }
    }
    const query = queryEntries.length
      ? "?" + queryEntries.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&")
      : "";

    // Build body (anything not consumed)
    let body: string | undefined;
    if (route.hasBody) {
      if (route.snakeBody) {
        // Send the caller-supplied `body` object verbatim (snake_case).
        body = JSON.stringify((original as { body?: unknown }).body ?? {});
      } else {
        const bodyObj: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(camelParams)) {
          if (!consumed.has(k) && v !== undefined && v !== null) {
            bodyObj[k] = v;
          }
        }
        body = JSON.stringify(bodyObj);
      }
    }

    const url = `${this.endpoint}${path}${query}`;
    const start = nowMs();
    this.emit({ kind: "request", method, url, httpMethod: route.method });
    let response: Response;
    try {
      response = await fetch(url, {
        method: route.method,
        headers: await this.buildHeaders(route.hasBody),
        body,
        signal: AbortSignal.timeout(this.timeout),
      });
    } catch (error) {
      const durationMs = nowMs() - start;
      if (error instanceof TypeError) {
        const err = new ConnectionError(
          `Request to ${url} failed: ${(error as Error).message}`,
          { cause: error },
        );
        this.emit({ kind: "error", method, url, durationMs, message: err.message });
        throw err;
      }
      throw error;
    }

    const requestId = extractRequestId(response.headers);
    const durationMs = nowMs() - start;

    if (response.status === 401 || response.status === 403) {
      const err = new AuthenticationError(
        `Authentication failed: ${response.status} ${response.statusText}`,
        { requestId },
      );
      this.emit({
        kind: "error",
        method,
        url,
        status: response.status,
        requestId,
        durationMs,
        message: err.message,
      });
      throw err;
    }

    if (response.status === 204) {
      this.emit({ kind: "response", method, url, status: 204, requestId, durationMs });
      return undefined as T;
    }

    const text = await response.text();

    if (!response.ok) {
      let errorBody: unknown;
      try {
        errorBody = JSON.parse(text);
      } catch {
        errorBody = text;
      }
      const errMsg =
        typeof errorBody === "object" && errorBody !== null && "error" in errorBody
          ? String((errorBody as Record<string, unknown>)["error"])
          : `HTTP ${response.status}`;

      if (response.status === 404) {
        const notFoundErr = new NotFoundError(errMsg, { requestId });
        this.emit({
          kind: "error",
          method,
          url,
          status: response.status,
          requestId,
          durationMs,
          message: notFoundErr.message,
        });
        throw notFoundErr;
      }

      const err = new TransportError(
        `${method} failed: ${errMsg}`,
        response.status,
        errorBody,
        { requestId },
      );
      this.emit({
        kind: "error",
        method,
        url,
        status: response.status,
        requestId,
        durationMs,
        message: err.message,
      });
      throw err;
    }

    this.emit({ kind: "response", method, url, status: response.status, requestId, durationMs });

    if (!text) return undefined as T;
    let parsed: unknown = JSON.parse(text);
    if (route.shape) parsed = route.shape(parsed, camelParams);
    return camelToSnake<T>(parsed);
  }

  private emit(event: Parameters<Logger>[0]): void {
    if (!this.logger) return;
    try {
      this.logger(event);
    } catch {
      // Logger errors must never propagate.
    }
  }

  private async buildHeaders(includeContentType = false): Promise<Record<string, string>> {
    const headers: Record<string, string> = {};
    const canSendUserAgent = supportsUserAgentHeader();
    for (const [key, value] of Object.entries(this.headers)) {
      if (key.toLowerCase() === "user-agent" && !canSendUserAgent) continue;
      headers[key] = value;
    }
    if (canSendUserAgent && !Object.keys(headers).some((key) => key.toLowerCase() === "user-agent")) {
      headers["User-Agent"] = defaultUserAgent();
    }
    if (includeContentType) headers["Content-Type"] = "application/json";
    const token = this.tokenProvider ? await this.tokenProvider() : this.apiKey;
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  }
}
