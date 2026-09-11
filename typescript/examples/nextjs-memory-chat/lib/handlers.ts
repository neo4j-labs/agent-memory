/**
 * The behaviour behind every route in `app/api/**`.
 *
 * Each handler takes its dependencies explicitly (`MemoryClient`, a language
 * model, a user id) and a plain `Request`, and returns a plain `Response`. Next
 * route handlers are exactly that signature, so `app/api/chat/route.ts` is four
 * lines and the interesting code is unit-testable without a server.
 */

import type { LanguageModelV4 } from "@ai-sdk/provider";
import type { MemoryClient } from "@neo4j-labs/agent-memory";
import { agentMemoryMiddleware } from "@neo4j-labs/agent-memory/middleware/vercel-ai";
import { convertToModelMessages, streamText, wrapLanguageModel, type UIMessage } from "ai";

import type {
  ContextPayload,
  ConversationPayload,
  ExtractionPayload,
  GraphPayload,
  TracePayload,
} from "./types.js";

/**
 * The assistant's brief. Deliberately says nothing about *how* memory works —
 * the middleware supplies the remembered constraints as prompt content, so the
 * instructions only have to tell the model to use what it is given.
 */
export const INSTRUCTIONS = [
  "You are a travel planning assistant with a long memory.",
  "Earlier turns of this conversation, and summaries of it, are supplied to you",
  "as context. Treat any constraint you find there (budget, dates, dietary needs,",
  "places already ruled out) as still binding, and say when you are relying on",
  "something the traveller told you earlier.",
  "Name concrete places, neighbourhoods and organisations — those become the",
  "entities in the traveller's memory graph.",
  "Keep answers to a few short paragraphs.",
].join(" ");

export interface ChatDeps {
  client: MemoryClient;
  model: LanguageModelV4;
  userId: string;
  instructions?: string;
}

export interface MemoryDeps {
  client: MemoryClient;
  userId: string;
}

interface ChatRequestBody {
  messages?: UIMessage[];
  conversationId?: string;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function badRequest(message: string): Response {
  return json({ error: message }, 400);
}

/** The text of a UI message, ignoring file/tool/reasoning parts. */
export function uiMessageText(message: UIMessage): string {
  return message.parts
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

/**
 * `POST /api/chat` — the one route that does the memory work.
 *
 * Two things are worth staring at:
 *
 *  1. Only the newest user turn is forwarded to the model. The history the
 *     model sees is injected by `agentMemoryMiddleware` from NAMS, so the
 *     browser is not the source of truth for the conversation — reload the tab,
 *     open the URL on another device, and the thread is still there.
 *  2. There is no memory-specific code after `wrapLanguageModel`. Persisting
 *     both sides of the turn (including the streamed one) is the middleware's
 *     job.
 */
export async function handleChat(deps: ChatDeps, request: Request): Promise<Response> {
  let body: ChatRequestBody;
  try {
    body = (await request.json()) as ChatRequestBody;
  } catch {
    return badRequest("Request body must be JSON.");
  }

  const conversationId = body.conversationId;
  if (!conversationId) return badRequest("conversationId is required.");

  const uiMessages = body.messages ?? [];
  const latest = uiMessages.filter((m) => m.role === "user").slice(-1);
  if (latest.length === 0) return badRequest("No user message to answer.");

  // Memory supplies the history; the client only has to send the new turn.
  const messages = await convertToModelMessages(latest);
  const question = uiMessageText(latest[0]!);

  const model = wrapLanguageModel({
    model: deps.model,
    middleware: agentMemoryMiddleware(deps.client, {
      conversationId,
      userId: deps.userId,
    }),
  });

  const result = streamText({
    model,
    instructions: deps.instructions ?? INSTRUCTIONS,
    messages,
    // `onEnd` is the AI SDK 7 name (`onFinish` is a deprecated alias). The
    // reasoning step is what the trace drawer reads back — one row per answered
    // turn, addressable by conversation.
    onEnd: async ({ text }) => {
      await recordTurn(deps.client, conversationId, question, text);
    },
  });

  return result.toUIMessageStreamResponse();
}

/**
 * Record the turn as a reasoning step. Best-effort: a memory write must never
 * fail the response the user already read.
 */
async function recordTurn(
  client: MemoryClient,
  conversationId: string,
  question: string,
  answer: string,
): Promise<void> {
  try {
    await client.reasoning.recordStep({
      conversationId,
      reasoning: `Answered using the context agentMemoryMiddleware injected for "${question}".`,
      actionTaken: "generate_answer",
      result: answer.slice(0, 1_000),
    });
  } catch {
    // Non-fatal.
  }
}

/** `POST /api/conversation` — mint the conversation id that lives in the URL. */
export async function handleCreateConversation(deps: MemoryDeps): Promise<Response> {
  const conversation = await deps.client.shortTerm.createConversation({
    userId: deps.userId,
    metadata: { source: "nextjs-memory-chat" },
  });
  const payload: ConversationPayload = { id: conversation.id, userId: conversation.userId };
  return json(payload);
}

/**
 * `GET /api/memory/context?conversationId=…` — the three tiers NAMS will inject
 * on the next call: reflections, observations, recent messages.
 */
export async function handleContext(deps: MemoryDeps, request: Request): Promise<Response> {
  const conversationId = new URL(request.url).searchParams.get("conversationId");
  if (!conversationId) return badRequest("conversationId is required.");

  const context = await deps.client.shortTerm.getContext(conversationId);
  const payload: ContextPayload = {
    conversationId,
    reflections: context.reflections.map((r) => ({ id: r.id, content: r.content })),
    observations: context.observations.map((o) => ({ id: o.id, content: o.content })),
    recentMessages: context.recentMessages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
    })),
  };
  return json(payload);
}

/** `GET /api/memory/graph` — the whole entity graph for the workspace. */
export async function handleGraph(deps: MemoryDeps): Promise<Response> {
  const graph = await deps.client.longTerm.getEntityGraph();
  const payload: GraphPayload = {
    nodes: graph.nodes.map((n) => ({ id: n.id, name: n.name, type: n.type })),
    edges: graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: e.type,
    })),
  };
  return json(payload);
}

/**
 * `POST /api/memory/graph` — one hop out from `nodeId`.
 *
 * `loadedIds` is every node already on the canvas. Passing it is the whole
 * point of `expandGraph`: the service returns only the delta, so a
 * double-click on a well-connected node does not re-send the subgraph the user
 * is already looking at.
 */
export async function handleExpandGraph(deps: MemoryDeps, request: Request): Promise<Response> {
  let body: { nodeId?: string; loadedIds?: string[] };
  try {
    body = (await request.json()) as { nodeId?: string; loadedIds?: string[] };
  } catch {
    return badRequest("Request body must be JSON.");
  }
  if (!body.nodeId) return badRequest("nodeId is required.");

  const expanded = await deps.client.longTerm.expandGraph(body.nodeId, body.loadedIds ?? []);
  const payload: GraphPayload = {
    nodes: expanded.nodes.map((node) => {
      const props = node.properties ?? {};
      return {
        id: node.id,
        name: String(props["name"] ?? node.id),
        type: String(props["type"] ?? node.labels?.[0] ?? "ENTITY"),
      };
    }),
    edges: expanded.edges.map((edge, index) => {
      const e = edge as Record<string, unknown>;
      return {
        id: String(e["id"] ?? `${body.nodeId}-expand-${index}`),
        source: String(e["source"] ?? e["from"] ?? e["startNodeId"] ?? ""),
        target: String(e["target"] ?? e["to"] ?? e["endNodeId"] ?? ""),
        type: String(e["type"] ?? "RELATED_TO"),
      };
    }),
  };
  return json(payload);
}

/**
 * `POST /api/memory/extraction` — wait for the entity pipeline to catch up.
 *
 * NAMS extracts entities in the background, so a turn returns before its
 * entities are searchable. The rail posts the turn's text plus the ids already
 * on the canvas and awaits `waitForExtraction` with a predicate — "resolve when
 * an entity I have not seen appears" — which is strictly better than a fixed
 * `setTimeout` before refetching the graph. Returns `settled: false` on
 * timeout rather than throwing, so a quiet turn (no new entities) degrades to
 * "nothing new" instead of an error.
 */
export async function handleExtractionStatus(
  deps: MemoryDeps,
  request: Request,
): Promise<Response> {
  let body: { query?: string; knownIds?: string[]; timeoutMs?: number };
  try {
    body = (await request.json()) as { query?: string; knownIds?: string[]; timeoutMs?: number };
  } catch {
    return badRequest("Request body must be JSON.");
  }
  const query = body.query?.trim();
  if (!query) return badRequest("query is required.");

  const known = new Set(body.knownIds ?? []);
  const settled = await deps.client.longTerm.waitForExtraction({
    query,
    limit: 25,
    timeoutMs: body.timeoutMs ?? 20_000,
    intervalMs: 1_500,
    predicate: (entities) => entities.some((entity) => !known.has(entity.id)),
  });

  const entities = await deps.client.longTerm.searchEntities(query, { limit: 25 });
  const payload: ExtractionPayload = {
    settled,
    entities: entities.map((e) => ({ id: e.id, name: e.name, type: e.type })),
  };
  return json(payload);
}

/** `GET /api/memory/trace?conversationId=…` — steps and tool calls for the drawer. */
export async function handleTrace(deps: MemoryDeps, request: Request): Promise<Response> {
  const conversationId = new URL(request.url).searchParams.get("conversationId");
  if (!conversationId) return badRequest("conversationId is required.");

  const trace = await deps.client.reasoning.getTraceByConversation(conversationId);
  const payload: TracePayload = {
    conversationId: trace.conversationId,
    steps: trace.steps.map((s) => ({
      id: s.id,
      reasoning: s.reasoning,
      actionTaken: s.actionTaken,
      result: s.result,
      createdAt: s.createdAt,
    })),
    toolCalls: trace.toolCalls.map((t) => ({
      id: t.id,
      toolName: t.toolName,
      status: t.status,
    })),
  };
  return json(payload);
}
