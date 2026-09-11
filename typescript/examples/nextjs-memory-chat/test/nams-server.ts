/**
 * A mock Neo4j Agent Memory Service, served by msw.
 *
 * The tests drive the real `MemoryClient` over its real `RestTransport` — only
 * the network is faked — so a wrong URL, a wrong HTTP verb or a wrong body shape
 * fails the suite. That is the point: the route handlers are asserted against
 * the service contract, not against a hand-written stub of the SDK.
 *
 * Only the endpoints this example calls are implemented. Anything else answers
 * 501, so an example that grows a new call fails loudly instead of silently
 * passing.
 */

import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

export const ENDPOINT = "http://nams.test/v1";
export const API_KEY = "nams_test";

export interface StoredMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface StoredStep {
  id: string;
  conversation_id: string;
  reasoning: string;
  action_taken: string;
  result?: string;
  created_at: string;
}

export interface StoredEntity {
  id: string;
  name: string;
  type: string;
}

/** Everything the fake service holds, plus a log of what was asked of it. */
export class NamsState {
  readonly conversations = new Map<string, { id: string; user_id: string; messages: StoredMessage[] }>();
  readonly steps: StoredStep[] = [];
  readonly calls: Array<{ method: string; path: string; body?: unknown }> = [];

  /** Entities returned by /entities/search, in order of appearance. */
  entities: StoredEntity[] = [];

  /**
   * Entities that appear only on the Nth search and later — how the fake models
   * a background extraction pipeline that has not caught up yet.
   */
  pendingEntity: { afterSearches: number; entity: StoredEntity } | null = null;

  searches = 0;

  graph: { nodes: StoredEntity[]; edges: Array<{ id: string; source: string; target: string; type: string }> } = {
    nodes: [],
    edges: [],
  };

  /** 1-hop expansions, keyed by node id. */
  neighbours = new Map<
    string,
    { nodes: StoredEntity[]; edges: Array<{ id: string; source: string; target: string; type: string }> }
  >();

  private counter = 0;

  nextId(prefix: string): string {
    this.counter += 1;
    return `${prefix}-${this.counter}`;
  }

  conversation(id: string) {
    const conv = this.conversations.get(id);
    if (!conv) throw new Error(`unknown conversation ${id}`);
    return conv;
  }

  /** Pre-create a conversation with history, as if an earlier session wrote it. */
  seedConversation(userId: string, messages: Array<{ role: string; content: string }>): string {
    const id = this.nextId("conv");
    this.conversations.set(id, {
      id,
      user_id: userId,
      messages: messages.map((m) => ({
        id: this.nextId("msg"),
        role: m.role,
        content: m.content,
        created_at: new Date().toISOString(),
      })),
    });
    return id;
  }

  rolesOf(conversationId: string): string[] {
    return this.conversation(conversationId).messages.map((m) => m.role);
  }

  contentsOf(conversationId: string): string[] {
    return this.conversation(conversationId).messages.map((m) => m.content);
  }

  callsTo(path: string): Array<{ method: string; path: string; body?: unknown }> {
    return this.calls.filter((call) => call.path === path);
  }
}

export function createNamsServer(state: NamsState) {
  const url = (path: string) => `${ENDPOINT}${path}`;

  async function record(request: Request, path: string): Promise<unknown> {
    let body: unknown;
    if (request.method !== "GET" && request.method !== "DELETE") {
      try {
        body = await request.clone().json();
      } catch {
        body = undefined;
      }
    }
    state.calls.push({ method: request.method, path, body });
    return body;
  }

  return setupServer(
    http.post(url("/conversations"), async ({ request }) => {
      const body = (await record(request, "/conversations")) as { userId?: string } | undefined;
      const id = state.nextId("conv");
      state.conversations.set(id, { id, user_id: body?.userId ?? "anonymous", messages: [] });
      return HttpResponse.json({ id, user_id: body?.userId ?? "anonymous", created_at: new Date().toISOString() });
    }),

    http.post(url("/conversations/:id/messages/bulk"), async ({ request, params }) => {
      const body = (await record(request, "/conversations/:id/messages/bulk")) as {
        messages?: Array<{ role: string; content: string }>;
      };
      const conv = state.conversation(String(params.id));
      const written = (body.messages ?? []).map((m) => ({
        id: state.nextId("msg"),
        role: m.role,
        content: m.content,
        created_at: new Date().toISOString(),
      }));
      conv.messages.push(...written);
      return HttpResponse.json({ messages: written });
    }),

    http.post(url("/conversations/:id/messages"), async ({ request, params }) => {
      const body = (await record(request, "/conversations/:id/messages")) as {
        role: string;
        content: string;
      };
      const conv = state.conversation(String(params.id));
      const message: StoredMessage = {
        id: state.nextId("msg"),
        role: body.role,
        content: body.content,
        created_at: new Date().toISOString(),
      };
      conv.messages.push(message);
      return HttpResponse.json(message);
    }),

    http.get(url("/conversations/:id/messages"), async ({ request, params }) => {
      await record(request, "/conversations/:id/messages");
      return HttpResponse.json({ messages: state.conversation(String(params.id)).messages });
    }),

    http.get(url("/conversations/:id/context"), async ({ request, params }) => {
      await record(request, "/conversations/:id/context");
      const conv = state.conversation(String(params.id));
      return HttpResponse.json({
        reflections: [],
        // An observation appears once the conversation has a few turns, the way
        // the hosted summariser behaves.
        observations:
          conv.messages.length >= 4
            ? [
                {
                  id: "obs-1",
                  conversation_id: conv.id,
                  content: "Traveller is vegetarian, on a £3,000 budget, and avoids buses.",
                  created_at: new Date().toISOString(),
                },
              ]
            : [],
        recent_messages: conv.messages,
      });
    }),

    http.post(url("/entities/search"), async ({ request }) => {
      await record(request, "/entities/search");
      state.searches += 1;
      const pending = state.pendingEntity;
      if (pending && state.searches >= pending.afterSearches) {
        if (!state.entities.some((e) => e.id === pending.entity.id)) {
          state.entities = [...state.entities, pending.entity];
        }
      }
      return HttpResponse.json({ entities: state.entities });
    }),

    http.post(url("/entities"), async ({ request }) => {
      const body = (await record(request, "/entities")) as { name: string; type?: string };
      const entity: StoredEntity = {
        id: state.nextId("ent"),
        name: body.name,
        type: body.type ?? "OBJECT",
      };
      state.entities = [...state.entities, entity];
      return HttpResponse.json({ ...entity, created_at: new Date().toISOString() });
    }),

    http.get(url("/entities/graph"), async ({ request }) => {
      await record(request, "/entities/graph");
      return HttpResponse.json(state.graph);
    }),

    http.post(url("/graph/expand"), async ({ request }) => {
      const body = (await record(request, "/graph/expand")) as {
        nodeId?: string;
        loadedIds?: string[];
      };
      const hop = state.neighbours.get(String(body.nodeId)) ?? { nodes: [], edges: [] };
      const loaded = new Set(body.loadedIds ?? []);
      // The real service returns only the delta; the fake honours that so the
      // test can prove loadedIds actually reached it.
      return HttpResponse.json({
        nodes: hop.nodes
          .filter((node) => !loaded.has(node.id))
          .map((node) => ({
            id: node.id,
            labels: ["Entity"],
            properties: { name: node.name, type: node.type },
          })),
        edges: hop.edges,
      });
    }),

    http.post(url("/reasoning/steps"), async ({ request }) => {
      const body = (await record(request, "/reasoning/steps")) as {
        conversationId: string;
        reasoning: string;
        actionTaken: string;
        result?: string;
      };
      const step: StoredStep = {
        id: state.nextId("step"),
        conversation_id: body.conversationId,
        reasoning: body.reasoning,
        action_taken: body.actionTaken,
        result: body.result,
        created_at: new Date().toISOString(),
      };
      state.steps.push(step);
      return HttpResponse.json(step);
    }),

    http.get(url("/reasoning/trace/:id"), async ({ request, params }) => {
      await record(request, "/reasoning/trace/:id");
      const id = String(params.id);
      return HttpResponse.json({
        conversation_id: id,
        steps: state.steps.filter((step) => step.conversation_id === id),
        tool_calls: [],
      });
    }),

    // Anything unimplemented is a loud failure, not a silent pass.
    http.all(`${ENDPOINT}/*`, ({ request }) => {
      state.calls.push({ method: request.method, path: new URL(request.url).pathname });
      return HttpResponse.json(
        { error: `mock NAMS: ${request.method} ${new URL(request.url).pathname} not implemented` },
        { status: 501 },
      );
    }),
  );
}
