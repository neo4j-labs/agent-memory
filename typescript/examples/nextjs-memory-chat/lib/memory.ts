/**
 * Server-side wiring: one `MemoryClient`, one language model, one user id.
 *
 * Everything here reads `process.env`, so it must only ever be imported from a
 * route handler, a server component, or a script — never from a `"use client"`
 * module. The route handlers in `app/api/**` are thin wrappers that call this
 * file for dependencies and `lib/handlers.ts` for behaviour; that split is what
 * lets `test/routes.test.ts` exercise the handlers with a mocked NAMS and a
 * mock model instead of booting Next.
 */

import { openai } from "@ai-sdk/openai";
import type { LanguageModelV4 } from "@ai-sdk/provider";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import type { NamsConfig } from "@neo4j-labs/nams-ai-provider";

/** Default traveller identity. Real apps read this from their session. */
export const DEFAULT_USER_ID = "traveller@example.com";

/**
 * Default model id. `gpt-5-mini` is a good price/latency fit for chat; override
 * with `OPENAI_MODEL` rather than editing this file.
 */
export const DEFAULT_MODEL = "gpt-5-mini";

/** The subset of `NamsConfig` the chat route needs to build its own provider. */
export type MemoryProviderConfig = Pick<NamsConfig, "apiKey" | "endpoint" | "workspaceId">;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} is not set. Copy .env.example to .env and fill it in — see the README.`,
    );
  }
  return value;
}

/**
 * The raw hosted-NAMS connection details, read from `process.env` on every
 * call (not cached) — on edge runtimes `process.env` is only populated inside
 * the request scope, so a module-init read comes back empty. `memoryClient()`
 * uses this to build the rail's `MemoryClient`; `handleChat` uses it to build
 * its own `createNamsProvider(...)`, a second, independent client pointed at
 * the same workspace.
 */
export function memoryProviderConfig(): MemoryProviderConfig {
  return {
    apiKey: requireEnv("MEMORY_API_KEY"),
    workspaceId: process.env.MEMORY_WORKSPACE_ID,
    ...(process.env.MEMORY_ENDPOINT ? { endpoint: process.env.MEMORY_ENDPOINT } : {}),
  };
}

let cached: MemoryClient | undefined;

/** The hosted-NAMS client, created once per server process. */
export function memoryClient(): MemoryClient {
  if (!cached) {
    cached = new MemoryClient(memoryProviderConfig());
  }
  return cached;
}

/** The chat model. Swapped for `MockLanguageModelV4` in tests. */
export function chatModel(): LanguageModelV4 {
  requireEnv("OPENAI_API_KEY");
  return openai(process.env.OPENAI_MODEL ?? DEFAULT_MODEL);
}

/** Who owns the conversations this app creates. */
export function userId(): string {
  return process.env.DEMO_USER_ID ?? DEFAULT_USER_ID;
}
