/**
 * Vercel AI SDK middleware for automatic memory integration.
 *
 * Implements the AI SDK **specification v4** language-model middleware
 * (`LanguageModelV4Middleware` from `@ai-sdk/provider` 4.x, the provider
 * specification that ships with `ai` 7.x). The middleware automatically:
 *
 *   - Injects three-tier conversational context (reflections + observations +
 *     recent messages) ahead of every model call when a `conversationId` is
 *     supplied — falls back to flat history for bridge transports.
 *   - Persists the user's input message before generation.
 *   - Persists the assistant's response after `generateText` **and** after
 *     `streamText` (the streamed text deltas are accumulated and written once
 *     the stream finishes).
 *   - Lazily creates a conversation on first call when the caller didn't
 *     pre-create one (only available with RestTransport).
 *
 * Reflections and observations are injected as a single `system` message;
 * recent messages are replayed as real `user`/`assistant` turns with
 * `{ type: "text" }` content parts, which is the shape the provider
 * specification requires.
 *
 * @example
 * ```ts
 * import { generateText, wrapLanguageModel } from "ai";
 * import { openai } from "@ai-sdk/openai";
 * import { MemoryClient } from "@neo4j-labs/agent-memory";
 * import { agentMemoryMiddleware } from "@neo4j-labs/agent-memory/middleware/vercel-ai";
 *
 * const client = new MemoryClient({
 *   endpoint: "https://memory.neo4jlabs.com/v1",
 *   apiKey: process.env.MEMORY_API_KEY!,
 * });
 *
 * const model = wrapLanguageModel({
 *   model: openai(process.env.OPENAI_MODEL ?? "gpt-5-mini"),
 *   middleware: agentMemoryMiddleware(client, {
 *     conversationId: "conv-uuid",    // or sessionId for bridge transport
 *     userId: "alice@example.com",    // used if a conversation is created
 *     includeContext: true,
 *   }),
 * });
 *
 * const result = await generateText({
 *   model,
 *   prompt: "Hello!",
 * });
 * ```
 */

import type {
  LanguageModelV4CallOptions,
  LanguageModelV4Content,
  LanguageModelV4GenerateResult,
  LanguageModelV4Middleware,
  LanguageModelV4Prompt,
  LanguageModelV4StreamPart,
  LanguageModelV4StreamResult,
} from "@ai-sdk/provider";

import type { MemoryClient } from "../client.js";
import { NotSupportedError } from "../errors.js";
import type { Message } from "../types.js";

export interface AgentMemoryMiddlewareOptions {
  /**
   * Conversation id (REST transport) or session id (bridge transport).
   * Can be a string or a function that returns one.
   */
  conversationId?: string | (() => string);

  /** @deprecated Use `conversationId`. Kept for backwards compatibility. */
  sessionId?: string | (() => string);

  /**
   * User id used when lazily creating a conversation. Only consulted if
   * `conversationId` is not supplied and the transport is REST.
   */
  userId?: string;

  /**
   * Include three-tier context (reflections + observations + recent messages).
   * If false, falls back to flat history. Default: true on REST, false on
   * bridge (where context endpoints aren't implemented).
   */
  includeContext?: boolean;

  /** Maximum messages to include from flat history (bridge fallback). */
  historyLimit?: number;

  /** Persist user input before generation. Default: true. */
  persistInput?: boolean;

  /** Persist assistant response after generation. Default: true. */
  persistResponses?: boolean;
}

/**
 * The middleware type this module produces — the AI SDK's specification-v4
 * language-model middleware.
 *
 * Kept as a named alias so existing imports keep compiling; prefer
 * `LanguageModelV4Middleware` from `@ai-sdk/provider` in new code.
 */
export type AgentMemoryLanguageModelMiddleware = LanguageModelV4Middleware;

function resolve(value?: string | (() => string)): string | undefined {
  if (typeof value === "function") return value();
  return value;
}

/**
 * Flatten a prompt message's content into plain text.
 *
 * `system` messages carry a string; `user`/`assistant`/`tool` messages carry an
 * array of content parts, of which only `text` parts are persistable as
 * message content.
 */
export function promptContentToText(content: LanguageModelV4Prompt[number]["content"]): string {
  if (typeof content === "string") return content;
  return content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

/** Extract the generated text from a specification-v4 generate result. */
function generatedText(content: Array<LanguageModelV4Content>): string {
  return content
    .filter((part): part is Extract<LanguageModelV4Content, { type: "text" }> =>
      part.type === "text",
    )
    .map((part) => part.text)
    .join("");
}

/** Build a specification-v4 prompt message carrying a single text part. */
function textMessage(role: "user" | "assistant", text: string): LanguageModelV4Prompt[number] {
  return { role, content: [{ type: "text", text }] };
}

/** Memory-augmented AI SDK (specification v4) language-model middleware. */
export function agentMemoryMiddleware(
  client: MemoryClient,
  options?: AgentMemoryMiddlewareOptions,
): AgentMemoryLanguageModelMiddleware {
  const persistResponses = options?.persistResponses ?? true;
  const persistInput = options?.persistInput ?? true;
  const includeContext = options?.includeContext ?? true;
  let resolvedId: string | undefined =
    resolve(options?.conversationId) ?? resolve(options?.sessionId);
  /** Text of the last user turn written, so a tool loop cannot re-persist it. */
  let lastPersistedUserText: string | undefined;

  // Lazy-create a conversation on REST transports if none was supplied.
  async function ensureConversationId(): Promise<string> {
    if (resolvedId) return resolvedId;
    try {
      const conv = await client.shortTerm.createConversation({
        userId: options?.userId ?? "anonymous",
      });
      resolvedId = conv.id;
      return resolvedId;
    } catch (err) {
      if (err instanceof NotSupportedError) {
        // Bridge transport — synthesize a session ID
        resolvedId = `session-${cryptoRandom()}`;
        return resolvedId;
      }
      throw err;
    }
  }

  /** Fetch the memory prefix to prepend to the prompt. */
  async function memoryPrefix(id: string): Promise<LanguageModelV4Prompt> {
    const notes: string[] = [];
    let history: LanguageModelV4Prompt = [];

    if (includeContext) {
      try {
        const ctx = await client.shortTerm.getContext(id);
        for (const r of ctx.reflections) notes.push(`[reflection] ${r.content}`);
        for (const o of ctx.observations) notes.push(`[observation] ${o.content}`);
        history = ctx.recentMessages.map((m) =>
          m.role === "system"
            ? ({ role: "system", content: m.content } as const)
            : textMessage(m.role, m.content),
        );
      } catch (err) {
        if (!(err instanceof NotSupportedError)) {
          // Non-fatal — fall back to flat history below.
        }
        notes.length = 0;
        history = [];
      }
    }

    // Bridge fallback or empty REST context → use flat conversation history.
    if (notes.length === 0 && history.length === 0) {
      try {
        const conv = await client.shortTerm.getConversation(id, {
          limit: options?.historyLimit,
        });
        history = conv.messages.map((msg: Message) =>
          msg.role === "system"
            ? ({ role: "system", content: msg.content } as const)
            : textMessage(msg.role, msg.content),
        );
      } catch {
        // No history — proceed without.
      }
    }

    if (notes.length === 0) return history;
    return [
      {
        role: "system",
        content: `Relevant memory for this conversation:\n${notes.join("\n")}`,
      },
      ...history,
    ];
  }

  /** Persist the assistant's text, swallowing transport failures. */
  async function persistAssistant(text: string): Promise<void> {
    if (!persistResponses || text.length === 0) return;
    const id = await ensureConversationId();
    try {
      await client.shortTerm.addMessage(id, "assistant", text);
    } catch {
      // Non-fatal.
    }
  }

  return {
    specificationVersion: "v4",

    transformParams: async ({ params }): Promise<LanguageModelV4CallOptions> => {
      const id = await ensureConversationId();
      const prefix = await memoryPrefix(id);
      const incoming: LanguageModelV4Prompt = params.prompt ?? [];

      // Best-effort: persist the user's input message.
      if (persistInput) {
        const lastUser = [...incoming].reverse().find((m) => m.role === "user");
        if (lastUser) {
          const text = promptContentToText(lastUser.content);
          // A tool-calling loop calls the model once per step with the same
          // user turn still last in the prompt, so without this guard every
          // step would re-persist it.
          if (text.length > 0 && text !== lastPersistedUserText) {
            lastPersistedUserText = text;
            try {
              await client.shortTerm.addMessage(id, "user", text);
            } catch {
              // Let the next call retry rather than swallowing the turn.
              lastPersistedUserText = undefined;
            }
          }
        }
      }

      if (prefix.length === 0) return params;
      return { ...params, prompt: [...prefix, ...incoming] };
    },

    wrapGenerate: async ({ doGenerate }): Promise<LanguageModelV4GenerateResult> => {
      const result = await doGenerate();
      await persistAssistant(generatedText(result.content));
      return result;
    },

    wrapStream: async ({ doStream }): Promise<LanguageModelV4StreamResult> => {
      const { stream, ...rest } = await doStream();
      let accumulated = "";
      let flushed = false;

      const flush = (): void => {
        if (flushed) return;
        flushed = true;
        // Fire-and-forget: the consumer must not wait on a memory write.
        void persistAssistant(accumulated);
      };

      const transform = new TransformStream<
        LanguageModelV4StreamPart,
        LanguageModelV4StreamPart
      >({
        transform(part, controller) {
          if (part.type === "text-delta") accumulated += part.delta;
          if (part.type === "finish") flush();
          controller.enqueue(part);
        },
        flush,
        // `flush` runs when the source closes; `cancel` runs when the consumer
        // aborts early. Both must persist whatever text arrived. `cancel` is in
        // the streams spec but not in every `lib.dom`/`@types/node` vintage, so
        // it is spread in to bypass the excess-property check.
        ...{ cancel: flush },
      });

      return { ...rest, stream: stream.pipeThrough(transform) };
    },
  };
}

function cryptoRandom(): string {
  // Every supported runtime (Node 22+, Bun, Deno, Workers, modern browsers)
  // exposes crypto.randomUUID. We fall back to crypto.getRandomValues with
  // base36 encoding for older or stripped-down environments — both APIs use
  // the platform CSPRNG. Math.random would be cryptographically insecure.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const buf = new Uint8Array(16);
    crypto.getRandomValues(buf);
    return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  throw new Error(
    "Secure randomness is unavailable in this runtime; supply an explicit conversationId.",
  );
}
