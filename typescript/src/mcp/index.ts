/**
 * MCP (Model Context Protocol) tool definitions for neo4j-agent-memory.
 *
 * A 12-tool memory surface you can register on your own MCP server, plus a
 * dispatcher that routes a tool call to a `MemoryClient`. Use it to:
 *
 *   - stand up a self-hosted MCP server that wraps, logs or filters memory
 *     calls, or runs inside a boundary that cannot reach the hosted service, or
 *   - programmatically dispatch tool calls to a `MemoryClient`.
 *
 * This is **not** the hosted surface. The hosted NAMS MCP server at
 * `mcp.memory.neo4jlabs.com` exposes a much larger, scope-gated surface
 * (ontology, Skills, admin) and supports OAuth; see
 * `reference/nams-mcp.adoc`. What follows is the 12-tool subset that maps
 * one-to-one onto this client's methods:
 * `memory_create_conversation`, `memory_add_messages`, `memory_get_context`,
 * `memory_search_messages`, `memory_search_entities`, `memory_get_entity`,
 * `memory_add_entity`, `memory_get_entity_history`, `memory_record_step`,
 * `memory_record_tool_call`, `memory_get_trace`, `memory_explain_decision`.
 *
 * `inputSchema` here is plain JSON Schema, which is what the low-level
 * `Server` + `ListToolsRequestSchema` API wants. For the high-level
 * `McpServer.registerTool` API — which requires Zod — import
 * {@link registerMemoryTools} from `@neo4j-labs/agent-memory/mcp/register`.
 *
 * @example
 * ```ts
 * import { MemoryClient } from "@neo4j-labs/agent-memory";
 * import { createMemoryTools, handleMemoryToolCall } from "@neo4j-labs/agent-memory/mcp";
 *
 * const client = new MemoryClient({
 *   endpoint: "https://memory.neo4jlabs.com/v1",
 *   apiKey: process.env.MEMORY_API_KEY!,
 * });
 *
 * const tools = createMemoryTools();           // 12 tool definitions
 * await handleMemoryToolCall(client, "memory_get_context", { conversation_id });
 * ```
 */

import type { MemoryClient } from "../client.js";

export interface McpToolDefinition {
  name: string;
  description: string;
  inputSchema: {
    type: "object";
    properties: Record<string, unknown>;
    required?: string[];
  };
  /**
   * MCP tool annotations — behavioural hints clients use to decide whether a
   * call needs confirmation. Mirrors what the Python MCP server in this repo
   * sets: reads are `readOnlyHint`/`idempotentHint`, writes are neither, and
   * no memory tool is destructive.
   */
  annotations: McpToolAnnotations;
}

/** Behavioural hints attached to every tool in {@link createMemoryTools}. */
export interface McpToolAnnotations {
  readOnlyHint: boolean;
  idempotentHint: boolean;
  destructiveHint: false;
}

/**
 * The tools that only read. Everything else in the 12-tool surface writes.
 *
 * Exported so a self-hosted server can apply the same split to tools it adds.
 */
export const READ_ONLY_MEMORY_TOOLS: ReadonlySet<string> = new Set([
  "memory_get_context",
  "memory_search_messages",
  "memory_search_entities",
  "memory_get_entity",
  "memory_get_entity_history",
  "memory_get_trace",
  "memory_explain_decision",
]);

/** Annotations for one tool name, derived from {@link READ_ONLY_MEMORY_TOOLS}. */
export function memoryToolAnnotations(name: string): McpToolAnnotations {
  const readOnly = READ_ONLY_MEMORY_TOOLS.has(name);
  return { readOnlyHint: readOnly, idempotentHint: readOnly, destructiveHint: false };
}

/**
 * Build the 12-tool MCP surface, each entry carrying JSON Schema input and
 * read/write annotations.
 */
export function createMemoryTools(): McpToolDefinition[] {
  return withAnnotations([
    // ---- Short-Term -----------------------------------------------------
    {
      name: "memory_create_conversation",
      description: "Create a new conversation session for a user.",
      inputSchema: {
        type: "object",
        properties: {
          user_id: { type: "string", description: "User identifier" },
          metadata: { type: "object", additionalProperties: true },
        },
        required: ["user_id"],
      },
    },
    {
      name: "memory_add_messages",
      description: "Append one or more messages to a conversation.",
      inputSchema: {
        type: "object",
        properties: {
          conversation_id: { type: "string" },
          messages: {
            type: "array",
            items: {
              type: "object",
              properties: {
                role: { type: "string", enum: ["user", "assistant", "system"] },
                content: { type: "string" },
                metadata: { type: "object", additionalProperties: true },
              },
              required: ["role", "content"],
            },
          },
        },
        required: ["conversation_id", "messages"],
      },
    },
    {
      name: "memory_get_context",
      description:
        "Three-tier context (reflections + observations + recent messages) for a conversation.",
      inputSchema: {
        type: "object",
        properties: {
          conversation_id: { type: "string" },
        },
        required: ["conversation_id"],
      },
    },
    {
      name: "memory_search_messages",
      description: "Search messages within a conversation by similarity or keywords.",
      inputSchema: {
        type: "object",
        properties: {
          conversation_id: { type: "string" },
          query: { type: "string" },
          limit: { type: "number" },
        },
        required: ["conversation_id", "query"],
      },
    },
    // ---- Long-Term ------------------------------------------------------
    {
      name: "memory_search_entities",
      description: "Search the knowledge graph for entities by name or concept.",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string" },
          type: { type: "string" },
          limit: { type: "number" },
        },
        required: ["query"],
      },
    },
    {
      name: "memory_get_entity",
      description: "Fetch one entity (with its relationships) by id.",
      inputSchema: {
        type: "object",
        properties: { entity_id: { type: "string" } },
        required: ["entity_id"],
      },
    },
    {
      name: "memory_add_entity",
      description: "Manually create an entity.",
      inputSchema: {
        type: "object",
        properties: {
          name: { type: "string" },
          type: { type: "string" },
          description: { type: "string" },
        },
        required: ["name", "type"],
      },
    },
    {
      name: "memory_get_entity_history",
      description: "All conversations that mentioned this entity.",
      inputSchema: {
        type: "object",
        properties: { entity_id: { type: "string" } },
        required: ["entity_id"],
      },
    },
    // ---- Reasoning ------------------------------------------------------
    {
      name: "memory_record_step",
      description: "Log a reasoning step under a conversation.",
      inputSchema: {
        type: "object",
        properties: {
          conversation_id: { type: "string" },
          reasoning: { type: "string" },
          action_taken: { type: "string" },
          result: { type: "string" },
        },
        required: ["conversation_id", "reasoning", "action_taken"],
      },
    },
    {
      name: "memory_record_tool_call",
      description: "Log a tool invocation tied to a reasoning step.",
      inputSchema: {
        type: "object",
        properties: {
          step_id: { type: "string" },
          tool_name: { type: "string" },
          input: { type: "string" },
          output: { type: "string" },
          status: { type: "string", enum: ["success", "error", "timeout"] },
          duration_ms: { type: "number" },
        },
        required: ["tool_name", "status"],
      },
    },
    {
      name: "memory_get_trace",
      description: "Full reasoning trace for a conversation (steps + tool calls).",
      inputSchema: {
        type: "object",
        properties: { conversation_id: { type: "string" } },
        required: ["conversation_id"],
      },
    },
    {
      name: "memory_explain_decision",
      description:
        "Detailed explanation of one reasoning step — tool calls + entities it influenced.",
      inputSchema: {
        type: "object",
        properties: { step_id: { type: "string" } },
        required: ["step_id"],
      },
    },
  ]);
}

/** Attach {@link memoryToolAnnotations} to each definition. */
function withAnnotations(
  defs: Array<Omit<McpToolDefinition, "annotations">>,
): McpToolDefinition[] {
  return defs.map((def) => ({ ...def, annotations: memoryToolAnnotations(def.name) }));
}

/**
 * Dispatch one of the 12 standard MCP tool calls to a `MemoryClient`.
 *
 * Old `memory.<verb>` names from v0.1 are kept as deprecated aliases for one
 * minor version; they emit a console warning and forward to the new tool.
 */
export async function handleMemoryToolCall(
  client: MemoryClient,
  toolName: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const aliased = LEGACY_ALIASES[toolName];
  if (aliased) {
    if (typeof console !== "undefined") {
      console.warn(
        `[neo4j-agent-memory] MCP tool '${toolName}' is deprecated; use '${aliased}'.`,
      );
    }
    toolName = aliased;
  }

  switch (toolName) {
    case "memory_create_conversation":
      return client.shortTerm.createConversation({
        userId: args["user_id"] as string,
        metadata: args["metadata"] as Record<string, unknown> | undefined,
      });

    case "memory_add_messages": {
      const conversationId = args["conversation_id"] as string;
      const msgs = args["messages"] as
        | Array<{ role: string; content: string; metadata?: Record<string, unknown> }>
        | undefined;
      if (!msgs || msgs.length === 0) return [];
      if (msgs.length === 1) {
        const m = msgs[0]!;
        return [
          await client.shortTerm.addMessage(
            conversationId,
            m.role as "user" | "assistant" | "system",
            m.content,
            { metadata: m.metadata },
          ),
        ];
      }
      return client.shortTerm.bulkAddMessages(
        conversationId,
        msgs.map((m) => ({
          role: m.role as "user" | "assistant" | "system",
          content: m.content,
          metadata: m.metadata,
        })),
      );
    }

    case "memory_get_context":
      return client.shortTerm.getContext(args["conversation_id"] as string);

    case "memory_search_messages":
      return client.shortTerm.searchMessages(args["query"] as string, {
        sessionId: args["conversation_id"] as string,
        limit: args["limit"] as number | undefined,
      });

    case "memory_search_entities":
      return client.longTerm.searchEntities(args["query"] as string, {
        type: args["type"] as string | undefined,
        limit: args["limit"] as number | undefined,
      });

    case "memory_get_entity":
      return client.longTerm.getEntity(args["entity_id"] as string);

    case "memory_add_entity":
      return client.longTerm.addEntity(args["name"] as string, args["type"] as string, {
        description: args["description"] as string | undefined,
      });

    case "memory_get_entity_history":
      return client.longTerm.getEntityHistory(args["entity_id"] as string);

    case "memory_record_step":
      return client.reasoning.recordStep({
        conversationId: args["conversation_id"] as string,
        reasoning: args["reasoning"] as string,
        actionTaken: args["action_taken"] as string,
        result: args["result"] as string | undefined,
      });

    case "memory_record_tool_call":
      return client.reasoning.recordToolCall(
        (args["step_id"] as string) ?? "",
        args["tool_name"] as string,
        typeof args["input"] === "object" && args["input"] !== null
          ? (args["input"] as Record<string, unknown>)
          : { input: args["input"] },
        {
          result: args["output"],
          status: (args["status"] as "success" | "error" | "timeout") ?? "success",
          durationMs: args["duration_ms"] as number | undefined,
        },
      );

    case "memory_get_trace":
      return client.reasoning.getTraceByConversation(args["conversation_id"] as string);

    case "memory_explain_decision":
      return client.reasoning.explainStep(args["step_id"] as string);

    default:
      throw new Error(`Unknown memory tool: ${toolName}`);
  }
}

/**
 * v0.1 → v0.2 deprecated tool aliases, still resolved for older clients.
 *
 * Scheduled for removal in **v1.0** (the original "removed in v0.3" note
 * lapsed — the aliases shipped through 0.4.x). Keep `mcp.adoc`'s tool table in
 * step with this list.
 */
const LEGACY_ALIASES: Record<string, string> = {
  "memory.addMessage": "memory_add_messages",
  "memory.getConversation": "memory_get_context",
  "memory.searchMessages": "memory_search_messages",
  "memory.addEntity": "memory_add_entity",
  "memory.searchEntities": "memory_search_entities",
};
