/**
 * Register the memory tools on a high-level `McpServer`.
 *
 * `McpServer.registerTool` validates arguments, so its `inputSchema` must be a
 * Zod raw shape — not the JSON Schema that {@link createMemoryTools} emits for
 * the low-level `Server` + `ListToolsRequestSchema` API. This module carries the
 * Zod half of that one surface and does the registration for you:
 *
 * ```ts
 * import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
 * import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
 * import { MemoryClient, VERSION } from "@neo4j-labs/agent-memory";
 * import { registerMemoryTools } from "@neo4j-labs/agent-memory/mcp/register";
 *
 * const memory = new MemoryClient({ apiKey: process.env.MEMORY_API_KEY! });
 * const server = new McpServer({ name: "my-memory-server", version: VERSION });
 *
 * registerMemoryTools(server, memory, {
 *   // optional: expose a subset
 *   only: process.env.MCP_TOOLS?.split(","),
 *   // optional: one audit line per call — names and argument *keys* only
 *   onCall: ({ tool, argKeys, durationMs, ok }) =>
 *     console.error(`[mcp] ${tool} keys=${argKeys.join(",")} ${durationMs}ms ok=${ok}`),
 * });
 *
 * await server.connect(new StdioServerTransport());
 * ```
 *
 * Streamable HTTP works the same way — swap the transport:
 *
 * ```ts
 * import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
 *
 * const transport = new StreamableHTTPServerTransport({
 *   sessionIdGenerator: () => crypto.randomUUID(),
 * });
 * await server.connect(transport);
 * // then hand every POST/GET/DELETE on your route to
 * // transport.handleRequest(req, res, body)
 * ```
 *
 * Both `zod` and `@modelcontextprotocol/sdk` are optional peers of this
 * package; importing this module requires them (the MCP SDK already depends on
 * `zod`, so an MCP server has both).
 *
 * Tool results are returned as one JSON `text` block. No `outputSchema` is
 * declared: several tools resolve to arrays, which `structuredContent` cannot
 * carry without re-shaping the payload, and pinning the rest would duplicate
 * types that already live in `src/types.ts`.
 */

import { z } from "zod";

import type { MemoryClient } from "../client.js";
import { handleMemoryToolCall, memoryToolAnnotations, createMemoryTools } from "./index.js";

/** A Zod raw shape — the form `McpServer.registerTool` wants for `inputSchema`. */
export type MemoryToolShape = Record<string, z.ZodTypeAny>;

const messageInput = z.object({
  role: z.enum(["user", "assistant", "system"]),
  content: z.string(),
  metadata: z.record(z.string(), z.unknown()).optional(),
});

/**
 * Zod input shapes for the 12 memory tools, keyed by tool name.
 *
 * Kept in step with the JSON Schema in {@link createMemoryTools} by
 * `test/unit/mcp-register.test.ts`, which compares the two key-by-key.
 */
export function memoryToolShapes(): Record<string, MemoryToolShape> {
  return {
    memory_create_conversation: {
      user_id: z.string().describe("User identifier"),
      metadata: z.record(z.string(), z.unknown()).optional(),
    },
    memory_add_messages: {
      conversation_id: z.string(),
      messages: z.array(messageInput),
    },
    memory_get_context: {
      conversation_id: z.string(),
    },
    memory_search_messages: {
      conversation_id: z.string(),
      query: z.string(),
      limit: z.number().optional(),
    },
    memory_search_entities: {
      query: z.string(),
      type: z.string().optional(),
      limit: z.number().optional(),
    },
    memory_get_entity: {
      entity_id: z.string(),
    },
    memory_add_entity: {
      name: z.string(),
      type: z.string(),
      description: z.string().optional(),
    },
    memory_get_entity_history: {
      entity_id: z.string(),
    },
    memory_record_step: {
      conversation_id: z.string(),
      reasoning: z.string(),
      action_taken: z.string(),
      result: z.string().optional(),
    },
    memory_record_tool_call: {
      step_id: z.string().optional(),
      tool_name: z.string(),
      input: z.string().optional(),
      output: z.string().optional(),
      status: z.enum(["success", "error", "timeout"]),
      duration_ms: z.number().optional(),
    },
    memory_get_trace: {
      conversation_id: z.string(),
    },
    memory_explain_decision: {
      step_id: z.string(),
    },
  };
}

/** One audit record, handed to {@link RegisterMemoryToolsOptions.onCall}. */
export interface MemoryToolCallAudit {
  tool: string;
  /** Argument **keys** only — never values, which may carry user content. */
  argKeys: string[];
  durationMs: number;
  ok: boolean;
  error?: string;
}

export interface RegisterMemoryToolsOptions {
  /** Restrict the exposed surface to these tool names. Default: all 12. */
  only?: readonly string[];
  /** Called after every dispatch, successful or not. */
  onCall?: (audit: MemoryToolCallAudit) => void;
}

/**
 * The subset of `McpServer` this module needs.
 *
 * Declared structurally so the published `.d.ts` does not force
 * `@modelcontextprotocol/sdk` into every consumer's type graph; a real
 * `McpServer` satisfies it.
 */
export interface RegisterToolCapable {
  registerTool(
    name: string,
    config: {
      title?: string;
      description?: string;
      inputSchema?: MemoryToolShape;
      annotations?: {
        readOnlyHint?: boolean;
        idempotentHint?: boolean;
        destructiveHint?: boolean;
      };
    },
    cb: (args: Record<string, unknown>) => Promise<{
      content: Array<{ type: "text"; text: string }>;
      isError?: boolean;
    }>,
  ): unknown;
}

/**
 * Register the memory tools on an `McpServer`, returning the names registered.
 *
 * A dispatch failure comes back as `isError: true` with the message as text —
 * the MCP convention — rather than as a thrown protocol error, so the model can
 * see what went wrong and retry.
 */
export function registerMemoryTools(
  server: RegisterToolCapable,
  client: MemoryClient,
  options?: RegisterMemoryToolsOptions,
): string[] {
  const shapes = memoryToolShapes();
  const allowed = options?.only ? new Set(options.only) : null;
  const registered: string[] = [];

  for (const def of createMemoryTools()) {
    if (allowed && !allowed.has(def.name)) continue;
    const shape = shapes[def.name];
    if (!shape) continue;

    server.registerTool(
      def.name,
      {
        description: def.description,
        inputSchema: shape,
        annotations: memoryToolAnnotations(def.name),
      },
      async (args: Record<string, unknown>) => {
        const started = Date.now();
        const argKeys = Object.keys(args ?? {});
        try {
          const result = await handleMemoryToolCall(client, def.name, args ?? {});
          options?.onCall?.({
            tool: def.name,
            argKeys,
            durationMs: Date.now() - started,
            ok: true,
          });
          return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          options?.onCall?.({
            tool: def.name,
            argKeys,
            durationMs: Date.now() - started,
            ok: false,
            error: message,
          });
          return { content: [{ type: "text" as const, text: message }], isError: true };
        }
      },
    );
    registered.push(def.name);
  }

  return registered;
}
