/**
 * The Worker's behaviour, with every dependency injected.
 *
 * `src/index.ts` builds the dependencies from `env` and routes to these
 * functions; the test suite builds the same dependencies from a fake NAMS and a
 * mock model. Keeping the split means the tests exercise the real handlers —
 * including the real `MemoryClient` and its REST transport — rather than a
 * parallel implementation.
 *
 * Nothing here imports `node:` anything. That is the point of the example: on
 * the edge there is no driver, no filesystem, no socket — memory is `fetch`.
 */

import { MemoryClient } from "@neo4j-labs/agent-memory";
import { agentMemoryMiddleware } from "@neo4j-labs/agent-memory/middleware/vercel-ai";
import type { LanguageModelV4 } from "@ai-sdk/provider";
import { streamText, wrapLanguageModel } from "ai";

/** Stamped on conversations this Worker creates, so they are attributable. */
export const SOURCE = "cloudflare-agents-edge";

const INSTRUCTIONS = [
  "You are a travel assistant running at the edge.",
  "Earlier turns and any recalled notes are supplied to you as memory —",
  "use them, and never ask the user to repeat something memory already holds.",
  "Keep answers to two short paragraphs.",
].join(" ");

export interface MemoryDeps {
  client: MemoryClient;
  model: LanguageModelV4;
  userId: string;
  /**
   * `ExecutionContext.waitUntil`. Required, not optional: on Workers a promise
   * that is not handed to `waitUntil` may be cancelled the moment the response
   * finishes, which is how memory writes silently go missing at the edge.
   */
  waitUntil: (promise: Promise<unknown>) => void;
  /** Counts NAMS round-trips for the `Server-Timing` header. */
  nams: NamsTimings;
}

/**
 * Sums what the hosted service cost this request, from the SDK's own logger
 * events. This is where the README's latency table comes from — not a guess.
 */
export class NamsTimings {
  requests = 0;
  durationMs = 0;

  /** Pass as `logger` to `new MemoryClient({ … })`. */
  readonly logger = (event: { kind: string; durationMs?: number }): void => {
    if (event.kind === "request") this.requests += 1;
    if (typeof event.durationMs === "number") this.durationMs += event.durationMs;
  };

  /** `Server-Timing` value, e.g. `nams;dur=41;desc="3 requests"`. */
  header(): string {
    return `nams;dur=${Math.round(this.durationMs)};desc="${this.requests} requests"`;
  }
}

interface ChatBody {
  message?: unknown;
  conversationId?: unknown;
}

function json(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body, null, 2), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
}

/** A 400 that names the field, so `curl` users are not left guessing. */
function badRequest(message: string): Response {
  return json({ error: message }, { status: 400 });
}

/**
 * `POST /chat` — one memory-augmented turn, streamed.
 *
 * Body: `{ "message": "…", "conversationId": "…" }` (omit `conversationId` on
 * the first turn and read it back from the `X-Conversation-Id` response header).
 *
 * The memory wiring is three things:
 *
 *  1. `agentMemoryMiddleware` prepends this conversation's three-tier context
 *     (reflections + observations + recent messages) and persists the user turn.
 *     Nothing is held in the Worker — isolates do not survive between requests,
 *     so the graph *is* the session store.
 *  2. The assistant turn is persisted in `onEnd` and that promise goes to
 *     `waitUntil`. The middleware would do it fire-and-forget, which is correct
 *     on a long-lived Node process and a coin flip on Workers; `persistResponses:
 *     false` hands the write to us so it can be awaited properly.
 *  3. A reasoning step plus its tool call record *why* the answer looked the way
 *     it did — recoverable later through `GET /memory`.
 */
export async function handleChat(deps: MemoryDeps, request: Request): Promise<Response> {
  let body: ChatBody;
  try {
    body = (await request.json()) as ChatBody;
  } catch {
    return badRequest("Body must be JSON: { message, conversationId? }");
  }
  if (typeof body.message !== "string" || body.message.trim() === "") {
    return badRequest("message is required and must be a non-empty string");
  }
  if (body.conversationId !== undefined && typeof body.conversationId !== "string") {
    return badRequest("conversationId must be a string when supplied");
  }
  const message = body.message;

  const conversationId =
    body.conversationId ??
    (
      await deps.client.shortTerm.createConversation({
        userId: deps.userId,
        metadata: { source: SOURCE },
      })
    ).id;

  const model = wrapLanguageModel({
    model: deps.model,
    middleware: agentMemoryMiddleware(deps.client, {
      conversationId,
      userId: deps.userId,
      // See (2) above — the assistant turn is ours to await.
      persistResponses: false,
    }),
  });

  const startedAt = Date.now();
  let settle!: (error?: unknown) => void;
  const persisted = new Promise<void>((resolve, reject) => {
    settle = (error?: unknown) => (error === undefined ? resolve() : reject(error));
  });

  const result = streamText({
    model,
    instructions: INSTRUCTIONS,
    prompt: message,
    onEnd: async ({ text, usage }) => {
      try {
        await recordTurn(deps, {
          conversationId,
          message,
          answer: text,
          durationMs: Date.now() - startedAt,
          outputTokens: usage?.outputTokens,
        });
        settle();
      } catch (error) {
        settle(error);
      }
    },
    // A client that disconnects mid-stream, or a provider error, must still
    // release `waitUntil` — otherwise the Worker sits out its whole budget.
    onAbort: () => settle(),
    onError: ({ error }) => settle(error),
  });

  // `waitUntil` keeps the isolate alive until the memory write lands. Without
  // it the write races the response and is dropped often enough to matter.
  deps.waitUntil(
    persisted.catch((error: unknown) => {
      console.error("memory write failed", error);
    }),
  );

  return result.toTextStreamResponse({
    headers: {
      "X-Conversation-Id": conversationId,
      "Server-Timing": deps.nams.header(),
    },
  });
}

/**
 * Persist the assistant turn and the reasoning behind it.
 *
 * `reasoning.recordStep` is the hosted (REST) reasoning model: steps hang off
 * the conversation, and `recordToolCall` hangs off a step. The bolt-only
 * `startTrace`/`addStep`/`completeTrace` trio is not part of the hosted REST
 * surface — calling it here would throw `NotSupportedError`.
 */
async function recordTurn(
  deps: MemoryDeps,
  turn: {
    conversationId: string;
    message: string;
    answer: string;
    durationMs: number;
    outputTokens?: number;
  },
): Promise<void> {
  await deps.client.shortTerm.addMessage(turn.conversationId, "assistant", turn.answer);

  const step = await deps.client.reasoning.recordStep({
    conversationId: turn.conversationId,
    reasoning:
      "Answered from the conversation's stored context rather than a prompt " +
      "the client had to re-send.",
    actionTaken: "generate_reply",
    result: turn.answer.slice(0, 500),
  });

  await deps.client.reasoning.recordToolCall(
    step.id,
    "generate_reply",
    { message: turn.message, outputTokens: turn.outputTokens ?? null },
    { result: turn.answer.slice(0, 500), status: "success", durationMs: turn.durationMs },
  );
}

/**
 * `GET /memory?conversationId=…` — what the next turn will see, and why the
 * last one said what it said.
 *
 * Two NAMS reads: the three-tier context, and the conversation's reasoning
 * trace. Both are conversation-scoped, which is what makes this the useful
 * debugging view for an edge deployment you cannot attach to.
 */
export async function handleMemory(deps: MemoryDeps, url: URL): Promise<Response> {
  const conversationId = url.searchParams.get("conversationId");
  if (!conversationId) return badRequest("conversationId query parameter is required");

  const [context, trace] = await Promise.all([
    deps.client.shortTerm.getContext(conversationId),
    deps.client.reasoning.getTraceByConversation(conversationId),
  ]);

  return json(
    {
      conversationId,
      willBeInjectedNextTurn: {
        reflections: context.reflections.map((r) => r.content),
        observations: context.observations.map((o) => o.content),
        recentMessages: context.recentMessages.map((m) => ({
          role: m.role,
          content: m.content,
        })),
      },
      reasoning: {
        steps: trace.steps.map((s) => ({
          id: s.id,
          reasoning: s.reasoning,
          actionTaken: s.actionTaken,
        })),
        toolCalls: trace.toolCalls.map((t) => ({
          toolName: t.toolName,
          status: t.status,
          durationMs: t.durationMs,
        })),
      },
    },
    { headers: { "Server-Timing": deps.nams.header() } },
  );
}

/**
 * `GET /graph` — the entity graph NAMS extracted in the background.
 *
 * With no query parameter this returns the workspace graph
 * (`longTerm.getEntityGraph`). With `?entity=<name>` it resolves the name and
 * returns that entity's 1-hop neighbourhood via `longTerm.expandGraph`, which is
 * the delta-shaped call a visualisation uses when the user expands a node.
 *
 * Note the scope: the hosted graph endpoints are *workspace*-scoped, not
 * conversation-scoped. To ask "which conversations mentioned this entity", use
 * `longTerm.getEntityHistory(entityId)`.
 */
export async function handleGraph(deps: MemoryDeps, url: URL): Promise<Response> {
  const name = url.searchParams.get("entity");

  if (name) {
    const [match] = await deps.client.longTerm.searchEntities(name, { limit: 1 });
    if (!match) {
      return json(
        { entity: name, found: false, nodes: [], edges: [] },
        { status: 404, headers: { "Server-Timing": deps.nams.header() } },
      );
    }
    const expanded = await deps.client.longTerm.expandGraph(match.id);
    return json(
      {
        entity: { id: match.id, name: match.name, type: match.type },
        found: true,
        scope: "one-hop",
        nodes: expanded.nodes,
        edges: expanded.edges,
      },
      { headers: { "Server-Timing": deps.nams.header() } },
    );
  }

  const graph = await deps.client.longTerm.getEntityGraph();
  return json(
    { scope: "workspace", nodes: graph.nodes, edges: graph.edges },
    { headers: { "Server-Timing": deps.nams.header() } },
  );
}

/** `GET /` — enough of a usage note that `wrangler dev` is not a 404. */
export function handleIndex(): Response {
  return new Response(
    [
      "neo4j-agent-memory on Cloudflare Workers",
      "",
      'POST /chat          {"message": "...", "conversationId": "..."}  → streamed text',
      "GET  /memory        ?conversationId=...                         → context + reasoning",
      "GET  /graph         [?entity=Name]                              → entity graph",
      "",
      "The conversation id comes back on the X-Conversation-Id response header.",
      "",
    ].join("\n"),
    { headers: { "Content-Type": "text/plain; charset=utf-8" } },
  );
}
