/**
 * The server itself — no transport, no `process.exit`, no env reading.
 *
 * Kept separate from `index.ts` so the same factory can be linked to an
 * in-process MCP client in a test (see `test/server.test.ts`) as well as
 * spoken to over stdio or Streamable HTTP.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { MemoryClient, VERSION } from "@neo4j-labs/agent-memory";
import { READ_ONLY_MEMORY_TOOLS } from "@neo4j-labs/agent-memory/mcp";
import {
  registerMemoryTools,
  type MemoryToolCallAudit,
} from "@neo4j-labs/agent-memory/mcp/register";
import { z } from "zod";

/** The extra tool this server adds on top of the SDK's 12. */
export const CYPHER_TOOL_NAME = "memory_cypher";

export interface MemoryMcpServerOptions {
  /**
   * Restrict the exposed surface to these tool names. Unset means "everything".
   *
   * This is one of the two reasons to self-host: a client that should only be
   * able to read context gets `MCP_TOOLS=memory_get_context,memory_search_entities`
   * and nothing else is even advertised in `tools/list`.
   */
  only?: readonly string[];

  /**
   * Called once per tool call, successful or not. Defaults to
   * {@link auditToStderr}.
   *
   * This is the other reason to self-host: every call is observable before it
   * reaches the service.
   */
  onCall?: (audit: MemoryToolCallAudit) => void;

  /** Register {@link CYPHER_TOOL_NAME}. Default: true. */
  includeCypherTool?: boolean;
}

/**
 * One audit line per tool call on **stderr**.
 *
 * Argument *keys* only — values can carry user content, and with the stdio
 * transport anything on stdout corrupts the protocol channel.
 */
export function auditToStderr(audit: MemoryToolCallAudit): void {
  const status = audit.ok ? "ok" : `error=${JSON.stringify(audit.error ?? "")}`;
  console.error(
    `[mcp] ${audit.tool} keys=[${audit.argKeys.join(",")}] ${audit.durationMs}ms ${status}`,
  );
}

/**
 * Build an `McpServer` exposing the memory surface, plus the names it registered.
 *
 * The SDK owns the tool definitions, the Zod input schemas, the read/write
 * annotations and the dispatch; this function owns only policy — what to expose,
 * what to log, and what to add.
 */
export function createMemoryMcpServer(
  memory: MemoryClient,
  options: MemoryMcpServerOptions = {},
): { server: McpServer; toolNames: string[] } {
  const { only, onCall = auditToStderr, includeCypherTool = true } = options;
  const allowed = only ? new Set(only) : null;

  const server = new McpServer({
    name: "neo4j-agent-memory-mcp",
    // Report the SDK version rather than a hand-maintained literal, so a client
    // can tell which memory surface it is talking to.
    version: VERSION,
  });

  // The 12 standard tools: Zod-validated inputs, read/write annotations, and
  // `isError: true` (not a thrown protocol error) when a dispatch fails.
  const toolNames = registerMemoryTools(server, memory, {
    ...(only ? { only } : {}),
    onCall,
  });

  if (includeCypherTool && (!allowed || allowed.has(CYPHER_TOOL_NAME))) {
    registerCypherTool(server, memory, onCall);
    toolNames.push(CYPHER_TOOL_NAME);
  }

  return { server, toolNames };
}

/**
 * A 13th tool the SDK does not ship: read-only Cypher against the memory graph.
 *
 * Hosted NAMS only — `client.query.cypher` rejects writes with HTTP 400 and is
 * not available on a bridge transport. It is the clearest illustration of why
 * you would self-host: you can expose graph access that the standard surface
 * deliberately leaves out, under your own policy.
 *
 * Treat it as privileged. Anything a model can write here runs against your
 * graph, so gate it the way you would gate a SQL console: `MCP_ENABLE_CYPHER=0`
 * turns it off, and `MCP_TOOLS` can leave it out per client.
 */
function registerCypherTool(
  server: McpServer,
  memory: MemoryClient,
  onCall: (audit: MemoryToolCallAudit) => void,
): void {
  server.registerTool(
    CYPHER_TOOL_NAME,
    {
      description:
        "Run a read-only Cypher query against the memory graph. Hosted NAMS only; " +
        "writes are rejected. Use for aggregate questions the standard memory " +
        "tools cannot answer, e.g. counting entities by type.",
      inputSchema: {
        cypher: z.string().describe("A read-only Cypher query."),
        params: z.record(z.string(), z.unknown()).optional().describe("Query parameters."),
      },
      annotations: {
        readOnlyHint: true,
        idempotentHint: true,
        destructiveHint: false,
      },
    },
    async ({ cypher, params }) => {
      const started = Date.now();
      const argKeys = params === undefined ? ["cypher"] : ["cypher", "params"];
      try {
        const result = await memory.query.cypher({ cypher, params });
        onCall({
          tool: CYPHER_TOOL_NAME,
          argKeys,
          durationMs: Date.now() - started,
          ok: true,
        });
        return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        onCall({
          tool: CYPHER_TOOL_NAME,
          argKeys,
          durationMs: Date.now() - started,
          ok: false,
          error: message,
        });
        return { content: [{ type: "text" as const, text: message }], isError: true };
      }
    },
  );
}

/**
 * Re-exported so a reader can see that the read/write split used for
 * {@link CYPHER_TOOL_NAME} is the same one the SDK applies to its own tools.
 */
export { READ_ONLY_MEMORY_TOOLS };
