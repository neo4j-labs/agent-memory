/**
 * A memory-augmented chat agent that runs entirely on Cloudflare Workers.
 *
 * There is no Neo4j driver here, no bolt socket, and no `node:` import — an
 * edge isolate cannot open a TCP connection to a database, so the whole memory
 * layer is the hosted Neo4j Agent Memory Service reached over `fetch`. That is
 * the claim `docs/how-to/typescript/edge-runtime.adoc` makes, and this Worker is
 * the test of it: `npm run build` bundles for `workerd`, and `npm test` runs the
 * suite *inside* `workerd`. A stray Node built-in fails both.
 *
 *   POST /chat    { message, conversationId? }   → streamed text
 *   GET  /memory  ?conversationId=…              → context + reasoning trace
 *   GET  /graph   [?entity=Name]                 → extracted entity graph
 *
 * Everything request-scoped is built per request from `env`, because on Workers
 * that is the only scope where bindings exist.
 */

import { createOpenAI } from "@ai-sdk/openai";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { DEFAULT_MODEL, DEFAULT_USER_ID, requireBinding, type Env } from "./env.js";
import {
  NamsTimings,
  handleChat,
  handleGraph,
  handleIndex,
  handleMemory,
  type MemoryDeps,
} from "./handlers.js";

export { type Env };

/**
 * Build the per-request dependencies.
 *
 * `apiKey` and the OpenAI key are passed explicitly. The zero-config
 * `new MemoryClient()` form reads `MEMORY_API_KEY` from `process.env`, which is
 * empty at module scope on Workers — the single most common edge mistake.
 */
function deps(env: Env, ctx: ExecutionContext): MemoryDeps {
  const nams = new NamsTimings();
  const client = new MemoryClient({
    apiKey: requireBinding(env, "MEMORY_API_KEY"),
    ...(env.MEMORY_WORKSPACE_ID ? { workspaceId: env.MEMORY_WORKSPACE_ID } : {}),
    ...(env.MEMORY_ENDPOINT ? { endpoint: env.MEMORY_ENDPOINT } : {}),
    logger: nams.logger,
  });

  // The default `openai(...)` helper reads OPENAI_API_KEY from the environment,
  // which edge runtimes do not populate at module scope either — so build a
  // provider from the binding instead.
  const provider = createOpenAI({ apiKey: requireBinding(env, "OPENAI_API_KEY") });

  return {
    client,
    model: provider(env.OPENAI_MODEL ?? DEFAULT_MODEL),
    userId: env.DEMO_USER_ID ?? DEFAULT_USER_ID,
    waitUntil: (promise) => ctx.waitUntil(promise),
    nams,
  };
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    try {
      if (url.pathname === "/" && request.method === "GET") return handleIndex();
      if (url.pathname === "/chat" && request.method === "POST") {
        return await handleChat(deps(env, ctx), request);
      }
      if (url.pathname === "/memory" && request.method === "GET") {
        return await handleMemory(deps(env, ctx), url);
      }
      if (url.pathname === "/graph" && request.method === "GET") {
        return await handleGraph(deps(env, ctx), url);
      }
      return new Response("Not found", { status: 404 });
    } catch (error) {
      // The diagnosis goes to the console (`wrangler tail` shows it); the
      // client gets a generic body so error text never leaks upstream details.
      console.error("request failed", error);
      return new Response(JSON.stringify({ error: "request failed" }, null, 2), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      });
    }
  },
} satisfies ExportedHandler<Env>;
