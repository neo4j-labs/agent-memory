/**
 * BridgeTransport — TCK bridge protocol transport.
 *
 * Speaks the bridge wire format (POST {endpoint}/{snake_case_method}) used by
 * conformance servers and the local reference adapter. Compatible with every
 * fetch-capable runtime (Node 20+, Bun, Deno, Workers, Edge).
 */

import { AuthenticationError, ConnectionError, NotFoundError, TransportError } from "../errors.js";
import {
  defaultUserAgent,
  extractRequestId,
  supportsUserAgentHeader,
  type Logger,
} from "../observability.js";
import type { Transport } from "./index.js";

/** Strip trailing `/` from a URL without using a polynomial regex. */
function trimTrailingSlashes(s: string): string {
  let end = s.length;
  while (end > 0 && s.charCodeAt(end - 1) === 47) end--;
  return s.slice(0, end);
}

function nowMs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now();
  }
  return Date.now();
}

export interface BridgeTransportOptions {
  /** Base URL of the bridge endpoint (no trailing /v1). */
  endpoint: string;

  /** API key for Bearer auth. Optional for local bridge servers. */
  apiKey?: string;

  /** Request timeout in milliseconds. Default: 30000. */
  timeout?: number;

  /** Additional headers to include in every request. */
  headers?: Record<string, string>;

  /** Per-request logger. */
  logger?: Logger;
}

export class BridgeTransport implements Transport {
  private readonly endpoint: string;
  private readonly apiKey?: string;
  private readonly timeout: number;
  private readonly headers: Record<string, string>;
  private readonly logger?: Logger;

  constructor(options: BridgeTransportOptions) {
    this.endpoint = trimTrailingSlashes(options.endpoint);
    this.apiKey = options.apiKey;
    this.timeout = options.timeout ?? 30_000;
    this.headers = options.headers ?? {};
    this.logger = options.logger;
  }

  async connect(): Promise<void> {
    const url = `${this.endpoint}/setup`;
    const start = nowMs();
    this.emit({ kind: "request", method: "connect", url, httpMethod: "POST" });
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: this.buildHeaders(),
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
        `Authentication failed: ${response.status} ${response.statusText}`,
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
    const url = `${this.endpoint}/${method}`;

    const body: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        body[key] = value;
      }
    }

    const start = nowMs();
    this.emit({ kind: "request", method, url, httpMethod: "POST" });
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: this.buildHeaders(),
        body: JSON.stringify(body),
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
      const errorMessage =
        typeof errorBody === "object" && errorBody !== null && "error" in errorBody
          ? String((errorBody as Record<string, unknown>)["error"])
          : `HTTP ${response.status}`;

      if (response.status === 404) {
        const notFoundErr = new NotFoundError(errorMessage, { requestId });
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
        `${method} failed: ${errorMessage}`,
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
    return JSON.parse(text) as T;
  }

  private emit(event: Parameters<Logger>[0]): void {
    if (!this.logger) return;
    try {
      this.logger(event);
    } catch {
      // Logger errors must never propagate.
    }
  }

  private buildHeaders(): Record<string, string> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const canSendUserAgent = supportsUserAgentHeader();
    for (const [key, value] of Object.entries(this.headers)) {
      if (key.toLowerCase() === "user-agent" && !canSendUserAgent) continue;
      headers[key] = value;
    }
    if (canSendUserAgent && !Object.keys(headers).some((key) => key.toLowerCase() === "user-agent")) {
      headers["User-Agent"] = defaultUserAgent();
    }
    if (this.apiKey) {
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }
    return headers;
  }
}
