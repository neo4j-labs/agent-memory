/**
 * Error hierarchy for the neo4j-agent-memory TypeScript client.
 *
 * SDK errors constructed from HTTP responses carry a `requestId` when captured
 * from the response. Raw fetch, parsing, and caller-provided errors may propagate.
 */

/** Options accepted by every MemoryError subclass. @inline */
export interface MemoryErrorOptions extends ErrorOptions {
  /** Server-generated correlation id (x-request-id or equivalent). */
  requestId?: string;
}

export class MemoryError extends Error {
  /** Server-generated correlation id, when available. */
  public readonly requestId?: string;

  constructor(message: string, options?: MemoryErrorOptions) {
    super(message, options);
    this.name = "MemoryError";
    this.requestId = options?.requestId;
  }

  override toString(): string {
    const base = super.toString();
    return this.requestId ? `${base} [requestId=${this.requestId}]` : base;
  }
}

export class ConnectionError extends MemoryError {
  constructor(message: string, options?: MemoryErrorOptions) {
    super(message, options);
    this.name = "ConnectionError";
  }
}

export class AuthenticationError extends MemoryError {
  constructor(message: string, options?: MemoryErrorOptions) {
    super(message, options);
    this.name = "AuthenticationError";
  }
}

export class NotFoundError extends MemoryError {
  constructor(message: string, options?: MemoryErrorOptions) {
    super(message, options);
    this.name = "NotFoundError";
  }
}

export class ValidationError extends MemoryError {
  constructor(message: string, options?: MemoryErrorOptions) {
    super(message, options);
    this.name = "ValidationError";
  }
}

export class TransportError extends MemoryError {
  public readonly statusCode?: number;
  public readonly responseBody?: unknown;

  constructor(
    message: string,
    statusCode?: number,
    responseBody?: unknown,
    options?: MemoryErrorOptions,
  ) {
    super(message, options);
    this.name = "TransportError";
    this.statusCode = statusCode;
    this.responseBody = responseBody;
  }
}

/** Raised when a transport cannot fulfil a method (e.g. REST has no equivalent). */
export class NotSupportedError extends MemoryError {
  constructor(message: string, options?: MemoryErrorOptions) {
    super(message, options);
    this.name = "NotSupportedError";
  }
}
