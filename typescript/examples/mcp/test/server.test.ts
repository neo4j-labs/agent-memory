/**
 * In-process round-trip test: a real MCP `Client` linked to the example's
 * server, with the `MemoryClient` backed by a fake transport.
 *
 * No API key, no network, no Neo4j — which is why this runs in CI.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import type { Transport } from "@neo4j-labs/agent-memory";
import { describe, expect, it } from "vitest";

import { CYPHER_TOOL_NAME, createMemoryMcpServer } from "../src/server.js";

const MEMORY_TOOLS = [
  "memory_create_conversation",
  "memory_add_messages",
  "memory_get_context",
  "memory_search_messages",
  "memory_search_entities",
  "memory_get_entity",
  "memory_add_entity",
  "memory_get_entity_history",
  "memory_record_step",
  "memory_record_tool_call",
  "memory_get_trace",
  "memory_explain_decision",
];

/** Records every wire call and replays canned responses. */
function fakeTransport(responses: Record<string, unknown> = {}): Transport & {
  calls: Array<{ method: string; params: Record<string, unknown> }>;
} {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  return {
    calls,
    async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
      calls.push({ method, params });
      if (method in responses) return responses[method] as T;
      throw new Error(`fakeTransport: no canned response for ${method}`);
    },
    async connect() {},
    async close() {},
  };
}

async function connect(
  memory: MemoryClient,
  options?: Parameters<typeof createMemoryMcpServer>[1],
): Promise<{ client: Client; toolNames: string[] }> {
  const { server, toolNames } = createMemoryMcpServer(memory, {
    // Swallow the audit lines instead of writing them to the test output.
    onCall: () => {},
    ...options,
  });
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "test-client", version: "0.0.0" });
  await Promise.all([server.connect(serverSide), client.connect(clientSide)]);
  return { client, toolNames };
}

describe("example MCP server", () => {
  it("advertises the 12 memory tools plus memory_cypher", async () => {
    const { client, toolNames } = await connect(new MemoryClient(fakeTransport()));

    const { tools } = await client.listTools();
    const names = tools.map((t) => t.name).sort();

    expect(names).toEqual([...MEMORY_TOOLS, CYPHER_TOOL_NAME].sort());
    expect(toolNames.sort()).toEqual(names);
    await client.close();
  });

  it("marks read tools read-only and idempotent, writes neither", async () => {
    const { client } = await connect(new MemoryClient(fakeTransport()));
    const { tools } = await client.listTools();
    const byName = new Map(tools.map((t) => [t.name, t.annotations]));

    expect(byName.get("memory_get_context")).toMatchObject({
      readOnlyHint: true,
      idempotentHint: true,
      destructiveHint: false,
    });
    expect(byName.get("memory_add_entity")).toMatchObject({
      readOnlyHint: false,
      idempotentHint: false,
      destructiveHint: false,
    });
    await client.close();
  });

  it("round-trips a tool call through to the memory client", async () => {
    const transport = fakeTransport({
      create_conversation: { id: "conv-1", user_id: "alice" },
    });
    const { client } = await connect(new MemoryClient(transport));

    const result = await client.callTool({
      name: "memory_create_conversation",
      arguments: { user_id: "alice" },
    });

    expect(result.isError).toBeFalsy();
    const content = result.content as Array<{ type: string; text: string }>;
    expect(JSON.parse(content[0]!.text)).toMatchObject({ id: "conv-1" });
    expect(transport.calls[0]?.method).toBe("create_conversation");
    await client.close();
  });

  it("returns isError instead of throwing when the backend fails", async () => {
    // No canned response, so the fake transport throws.
    const { client } = await connect(new MemoryClient(fakeTransport()));

    const result = await client.callTool({
      name: "memory_get_context",
      arguments: { conversation_id: "conv-1" },
    });

    expect(result.isError).toBe(true);
    await client.close();
  });

  it("rejects malformed arguments before the dispatcher sees them", async () => {
    const transport = fakeTransport({ get_context: {} });
    const { client } = await connect(new MemoryClient(transport));

    const result = await client.callTool({
      name: "memory_get_context",
      arguments: { conversation_id: 42 },
    });

    // The server's own Zod validation answers, as a tool result the model can
    // read and retry from — the dispatcher's casts never run.
    expect(result.isError).toBe(true);
    const content = result.content as Array<{ type: string; text: string }>;
    expect(content[0]!.text).toContain("conversation_id");
    expect(transport.calls).toEqual([]);
    await client.close();
  });

  it("exposes only the allow-listed tools", async () => {
    const { client } = await connect(new MemoryClient(fakeTransport()), {
      only: ["memory_get_context", "memory_search_entities"],
    });

    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name).sort()).toEqual([
      "memory_get_context",
      "memory_search_entities",
    ]);
    await client.close();
  });

  it("can drop the cypher tool", async () => {
    const { client } = await connect(new MemoryClient(fakeTransport()), {
      includeCypherTool: false,
    });
    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name)).not.toContain(CYPHER_TOOL_NAME);
    await client.close();
  });

  it("runs memory_cypher through client.query.cypher", async () => {
    const transport = fakeTransport({
      cypher_query: { columns: ["n"], rows: [["Alice"]] },
    });
    const { client } = await connect(new MemoryClient(transport));

    const result = await client.callTool({
      name: CYPHER_TOOL_NAME,
      arguments: { cypher: "MATCH (e:Entity) RETURN e.name AS n LIMIT 1" },
    });

    expect(result.isError).toBeFalsy();
    expect(transport.calls[0]?.method).toBe("cypher_query");
    await client.close();
  });

  it("emits one audit record per call carrying argument keys but no values", async () => {
    const audits: Array<{ tool: string; argKeys: string[]; ok: boolean }> = [];
    const { client } = await connect(
      new MemoryClient(fakeTransport({ create_conversation: { conversation_id: "c" } })),
      { onCall: (a) => audits.push({ tool: a.tool, argKeys: a.argKeys, ok: a.ok }) },
    );

    await client.callTool({
      name: "memory_create_conversation",
      arguments: { user_id: "alice" },
    });

    expect(audits).toHaveLength(1);
    expect(audits[0]).toMatchObject({ tool: "memory_create_conversation", ok: true });
    expect(audits[0]!.argKeys).toContain("user_id");
    expect(JSON.stringify(audits[0])).not.toContain("alice");
    await client.close();
  });
});
