/**
 * Integration tests — `registerMemoryTools` on a real `McpServer`, driven over
 * an in-process transport pair by a real MCP `Client`.
 *
 * Covers what the low-level definitions cannot: that the surface survives
 * `McpServer.registerTool` (Zod input schemas), that annotations reach the
 * client, that argument validation fails as a tool result rather than a thrown
 * protocol error, and that the allow-list and audit hook work.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { MemoryClient } from "../../src/client.js";
import { createMemoryTools } from "../../src/mcp/index.js";
import {
  memoryToolShapes,
  registerMemoryTools,
  type MemoryToolCallAudit,
  type RegisterMemoryToolsOptions,
} from "../../src/mcp/register.js";

const ENDPOINT = "https://memory.test/v1";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

interface ToolListEntry {
  name: string;
  annotations?: { readOnlyHint?: boolean; idempotentHint?: boolean; destructiveHint?: boolean };
  inputSchema?: { properties?: Record<string, unknown>; required?: string[] };
}

interface CallResult {
  isError?: boolean;
  content: Array<{ type: string; text?: string }>;
}

async function connected(options?: RegisterMemoryToolsOptions): Promise<{
  mcp: Client;
  memory: MemoryClient;
  registered: string[];
  close: () => Promise<void>;
}> {
  const memory = new MemoryClient({ endpoint: ENDPOINT, apiKey: "k" });
  const mcpServer = new McpServer({ name: "test-memory-server", version: "0.0.0" });
  const registered = registerMemoryTools(mcpServer, memory, options);

  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const mcp = new Client({ name: "test-client", version: "0.0.0" });
  await Promise.all([mcpServer.connect(serverTransport), mcp.connect(clientTransport)]);

  return {
    mcp,
    memory,
    registered,
    close: async () => {
      await mcp.close();
      await mcpServer.close();
      await memory.close();
    },
  };
}

describe("registerMemoryTools", () => {
  it("exposes all 12 tools with annotations and validated input schemas", async () => {
    const { mcp, registered, close } = await connected();
    try {
      expect(registered).toHaveLength(12);
      const listed = (await mcp.listTools()).tools as unknown as ToolListEntry[];
      expect(listed.map((t) => t.name).sort()).toEqual(
        createMemoryTools()
          .map((t) => t.name)
          .sort(),
      );

      const read = listed.find((t) => t.name === "memory_get_context")!;
      expect(read.annotations?.readOnlyHint).toBe(true);
      expect(read.annotations?.idempotentHint).toBe(true);
      expect(read.annotations?.destructiveHint).toBe(false);

      const write = listed.find((t) => t.name === "memory_add_messages")!;
      expect(write.annotations?.readOnlyHint).toBe(false);

      // The Zod shape reached the wire as JSON Schema with the right required keys.
      expect(read.inputSchema?.required).toEqual(["conversation_id"]);
    } finally {
      await close();
    }
  });

  it("round-trips one call through the dispatcher", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations/c1/context`, () =>
        HttpResponse.json({
          reflections: [],
          observations: [],
          recentMessages: [{ id: "m1", role: "user", content: "hi" }],
        }),
      ),
    );

    const audits: MemoryToolCallAudit[] = [];
    const { mcp, close } = await connected({ onCall: (a) => audits.push(a) });
    try {
      const result = (await mcp.callTool({
        name: "memory_get_context",
        arguments: { conversation_id: "c1" },
      })) as unknown as CallResult;

      expect(result.isError).toBeFalsy();
      expect(result.content[0]?.text).toContain("hi");

      // The audit record names the tool and the argument keys — never values.
      expect(audits).toHaveLength(1);
      expect(audits[0]!.tool).toBe("memory_get_context");
      expect(audits[0]!.argKeys).toEqual(["conversation_id"]);
      expect(audits[0]!.ok).toBe(true);
      expect(JSON.stringify(audits[0])).not.toContain("c1");
    } finally {
      await close();
    }
  });

  it("reports a backend failure as isError instead of throwing", async () => {
    server.use(
      http.get(`${ENDPOINT}/conversations/missing/context`, () =>
        HttpResponse.json({ error: "not found" }, { status: 404 }),
      ),
    );

    const audits: MemoryToolCallAudit[] = [];
    const { mcp, close } = await connected({ onCall: (a) => audits.push(a) });
    try {
      const result = (await mcp.callTool({
        name: "memory_get_context",
        arguments: { conversation_id: "missing" },
      })) as unknown as CallResult;

      expect(result.isError).toBe(true);
      expect(audits[0]!.ok).toBe(false);
      expect(audits[0]!.error).toBeTruthy();
    } finally {
      await close();
    }
  });

  it("rejects malformed arguments before reaching the dispatcher", async () => {
    const audits: MemoryToolCallAudit[] = [];
    const { mcp, close } = await connected({ onCall: (a) => audits.push(a) });
    try {
      const result = (await mcp.callTool({
        name: "memory_get_context",
        arguments: { wrong_key: 1 },
      })) as unknown as CallResult;

      expect(result.isError).toBe(true);
      // Validation happened in the server, so the dispatcher never ran.
      expect(audits).toHaveLength(0);
    } finally {
      await close();
    }
  });

  it("honours an allow-list", async () => {
    const { mcp, registered, close } = await connected({
      only: ["memory_get_context", "memory_search_entities"],
    });
    try {
      expect(registered).toEqual(["memory_get_context", "memory_search_entities"]);
      const listed = (await mcp.listTools()).tools as unknown as ToolListEntry[];
      expect(listed.map((t) => t.name).sort()).toEqual([
        "memory_get_context",
        "memory_search_entities",
      ]);
    } finally {
      await close();
    }
  });

  it("keeps the Zod shapes and the JSON Schema definitions in step", () => {
    const shapes = memoryToolShapes();
    for (const def of createMemoryTools()) {
      const shape = shapes[def.name];
      expect(shape, `missing Zod shape for ${def.name}`).toBeDefined();
      expect(Object.keys(shape!).sort()).toEqual(Object.keys(def.inputSchema.properties).sort());
      // Every JSON-Schema-required key is non-optional in the Zod shape.
      for (const required of def.inputSchema.required ?? []) {
        expect(shape![required]!.safeParse(undefined).success).toBe(false);
      }
    }
  });
});
