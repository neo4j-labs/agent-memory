/**
 * A self-hosted MCP server over the neo4j-agent-memory TypeScript client.
 *
 * Most people should not run this. The hosted NAMS MCP server at
 * `mcp.memory.neo4jlabs.com` exposes a much larger, scope-gated surface
 * (ontology, Skills, administration) and supports OAuth — point your client
 * straight at it and write no code at all.
 *
 * Self-host when you need something the hosted host cannot give you:
 *
 *   - wrap, log or audit every tool call before it reaches the service,
 *   - expose a restricted subset to a particular client (`MCP_TOOLS=...`),
 *   - add tools of your own alongside the memory surface (`memory_cypher` here),
 *   - run inside a boundary that cannot reach `memory.neo4jlabs.com` directly.
 *
 * This entrypoint owns only process concerns — env, transport, shutdown. The
 * server itself is in `server.ts`.
 *
 *   MEMORY_API_KEY      required; a workspace-scoped NAMS data-plane key
 *   MCP_TRANSPORT       "stdio" (default) | "http" (Streamable HTTP)
 *   MCP_PORT            HTTP port, default 3000
 *   MCP_TOOLS           comma-separated allow-list, default: everything
 *   MCP_ENABLE_CYPHER   "0" / "false" to drop the memory_cypher tool
 */

import { randomUUID } from "node:crypto";
import { createServer as createHttpServer, type IncomingMessage, type ServerResponse } from "node:http";

import type { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import type { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { MemoryClient } from "@neo4j-labs/agent-memory";

import { createMemoryMcpServer } from "./server.js";

type Transport = StdioServerTransport | StreamableHTTPServerTransport;

async function main(): Promise<void> {
  // Fail fast and loudly on stderr: with the stdio transport an exception
  // thrown later would look to the client like a server that never responds.
  if (!process.env["MEMORY_API_KEY"]) {
    console.error(
      "MEMORY_API_KEY is not set. Copy .env.example to .env and add your key " +
        "from https://memory.neo4jlabs.com, or export it in the environment.",
    );
    process.exit(1);
  }

  const memory = new MemoryClient();

  const { server, toolNames } = createMemoryMcpServer(memory, {
    ...(process.env["MCP_TOOLS"]
      ? { only: process.env["MCP_TOOLS"].split(",").map((n) => n.trim()) }
      : {}),
    includeCypherTool: !isFalsy(process.env["MCP_ENABLE_CYPHER"]),
  });

  const mode = process.env["MCP_TRANSPORT"] === "http" ? "http" : "stdio";
  const transport = mode === "http" ? await serveHttp(server) : await serveStdio(server);

  console.error(
    `[mcp] neo4j-agent-memory-mcp ready on ${mode} with ${toolNames.length} tools: ${toolNames.join(", ")}`,
  );

  installShutdownHandlers(async () => {
    await transport.close();
    await server.close();
    await memory.close();
  });
}

/** stdio: stdout is the protocol channel, so every diagnostic goes to stderr. */
async function serveStdio(server: { connect(t: Transport): Promise<void> }): Promise<Transport> {
  const { StdioServerTransport } = await import("@modelcontextprotocol/sdk/server/stdio.js");
  const transport = new StdioServerTransport();
  await server.connect(transport);
  return transport;
}

/**
 * Streamable HTTP: one session-aware transport behind a plain `node:http`
 * listener, which is the shape you want when the client is not co-located —
 * a sidecar, a container, the other side of a network boundary.
 *
 * The transport does not authenticate callers. Put your own auth in front of
 * the route before exposing it beyond localhost.
 */
async function serveHttp(server: { connect(t: Transport): Promise<void> }): Promise<Transport> {
  const { StreamableHTTPServerTransport } = await import(
    "@modelcontextprotocol/sdk/server/streamableHttp.js"
  );
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: () => randomUUID(),
  });
  await server.connect(transport);

  const port = Number(process.env["MCP_PORT"] ?? 3000);
  const http = createHttpServer((req, res) => {
    if (new URL(req.url ?? "/", "http://localhost").pathname !== "/mcp") {
      res.writeHead(404).end();
      return;
    }
    void handleMcpRequest(transport, req, res);
  });
  await new Promise<void>((resolve) => http.listen(port, resolve));
  console.error(`[mcp] POST/GET/DELETE http://localhost:${port}/mcp`);

  const originalClose = transport.close.bind(transport);
  transport.close = async () => {
    await new Promise<void>((resolve) => http.close(() => resolve()));
    await originalClose();
  };
  return transport;
}

async function handleMcpRequest(
  transport: StreamableHTTPServerTransport,
  req: IncomingMessage,
  res: ServerResponse,
): Promise<void> {
  try {
    // `handleRequest` wants the JSON-RPC body already parsed for POSTs; GET
    // (the SSE stream) and DELETE (session teardown) carry no body.
    const body = req.method === "POST" ? await readJsonBody(req) : undefined;
    await transport.handleRequest(req, res, body);
  } catch (err) {
    console.error("[mcp] request failed:", err);
    if (!res.headersSent) res.writeHead(400).end();
  }
}

async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw ? JSON.parse(raw) : undefined;
}

/**
 * Close the server, the transport and the client on a signal.
 *
 * Without this, Ctrl+C leaves the MCP client waiting on a half-open channel and
 * any in-flight NAMS request un-awaited.
 */
function installShutdownHandlers(close: () => Promise<void>): void {
  let closing = false;
  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.on(signal, () => {
      if (closing) return;
      closing = true;
      void close()
        .catch((err) => console.error("[mcp] shutdown error:", err))
        .finally(() => process.exit(0));
    });
  }
}

function isFalsy(value: string | undefined): boolean {
  return value === "0" || value?.toLowerCase() === "false";
}

main().catch((err: unknown) => {
  console.error(err);
  process.exit(1);
});
