/** Strict HTTP fixture for the captured hosted routes, through the real RestTransport. */
import { createServer } from "node:http";
import { CLEANUP_QUERIES } from "../../shared/tutorial-cleanup.js";

type Row = Record<string, unknown>;
export async function startHostedStub() {
  const conversations = new Map<string, Row & { id: string; messages: Row[] }>();
  const entities = new Map<string, Row>();
  const sources = new Map<string, string[]>();
  const neighbors = new Map<string, Array<{ id: string | null; relationship: string | null }>>();
  const calls: string[] = [];
  const requests: Array<{ method: string; path: string; body: Row; authorization?: string; workspace?: string }> = [];
  const steps: Row[] = [];
  const behavior = { extraction: "done", derive: true, failMessageRole: "", failDelete: "", residualIds: [] as string[], failStatus: 0 };
  let sequence = 0;
  const server = createServer(async (req, res) => {
    try {
      const requestUrl = new URL(req.url!, "http://localhost");
      const path = requestUrl.pathname.replace(/^\/v1/, "");
      calls.push(`${req.method} ${path}`);
      let raw = ""; for await (const chunk of req) raw += String(chunk);
      const body: Row = raw ? JSON.parse(raw) : {};
      requests.push({ method: req.method!, path, body, authorization: req.headers.authorization, workspace: req.headers["x-workspace-id"] as string | undefined });
      const send = (value: unknown, status = 200) => { res.writeHead(status, { "Content-Type": "application/json" }).end(JSON.stringify(value)); };
      const id = path.split("/")[2];
      const conversation = conversations.get(id!);
      const append = (message: Row) => {
        const stored = { ...message, id: `message-${++sequence}`, createdAt: new Date().toISOString() };
        conversation!.messages.push(stored);
        return stored;
      };
      if (path === "/conversations" && req.method === "POST") {
        const created = { ...body, id: `conversation-${++sequence}`, messages: [], createdAt: new Date().toISOString() };
        conversations.set(created.id, created); send(created);
      } else if (path === "/conversations" && req.method === "GET") {
        // list_conversations: hosted REST query params are snake_case (user_id, limit).
        const userId = requestUrl.searchParams.get("user_id");
        const limit = Number(requestUrl.searchParams.get("limit") ?? "");
        const matches = [...conversations.values()]
          .filter(c => !userId || c["userId"] === userId)
          .sort((a, b) => String(b["createdAt"]).localeCompare(String(a["createdAt"])));
        send({ conversations: Number.isFinite(limit) && limit > 0 ? matches.slice(0, limit) : matches });
      } else if (conversation && path.endsWith("/messages/bulk") && req.method === "POST") {
        send({ messages: (body.messages as Row[]).map(append) });
      } else if (conversation && path.endsWith("/messages")) {
        if (req.method === "POST" && body.role === behavior.failMessageRole) send({ error: "Injected persistence failure" }, 400);
        else send(req.method === "POST" ? append(body) : { messages: conversation.messages });
      } else if (conversation && path.endsWith("/context")) {
        send({ recentMessages: conversation.messages, observations: [], reflections: [] });
      } else if (conversation && path.endsWith("/search") && req.method === "POST") {
        send({ messages: conversation.messages.filter(message => String(message.content).includes(String(body.query))) });
      } else if (conversation && path.endsWith("/extraction-status")) {
        if (behavior.failStatus) { send({ error: "Injected status failure" }, behavior.failStatus); return; }
        if (conversation.messages.length > 0 && behavior.derive && ["done", "completed"].includes(behavior.extraction) && ![...sources.values()].some(ids => ids.includes(String(conversation.messages[0]?.id)))) {
          const entityId = `derived-${++sequence}`;
          const createdAt = new Date().toISOString();
          const name = String(conversation.messages[0]?.content).match(/Lantern Orchard [a-f0-9-]{36}/)?.[0] ?? "Fictional extracted entity";
          entities.set(entityId, { id: entityId, name, type: "organization", createdAt });
          sources.set(entityId, conversation.messages.map(m => String(m.id)));
        }
        send({ messages: conversation.messages.map(message => ({ id: message.id, status: behavior.extraction, attempts: 1 })) });
      } else if (conversation && path === `/conversations/${id}`) {
        if (req.method === "DELETE") {
          if (behavior.failDelete === id) { send({ error: "Injected delete failure" }, 400); return; }
          conversations.delete(id!); res.writeHead(204).end();
        } else send(conversation);
      } else if (path.startsWith("/conversations/")) send({ error: "Not found" }, 404);
      else if (path === "/entities" && req.method === "POST") {
        const entity = { ...body, id: `entity-${++sequence}`, createdAt: new Date().toISOString(), metadata: {} };
        entities.set(entity.id, entity); sources.set(entity.id, []); send(entity);
      } else if (path === "/entities/search" && req.method === "POST") {
        // search_entities: case-insensitive name/description substring match, closest a nearest-neighbour search gets offline.
        const q = String(body.query ?? "").toLowerCase();
        const limit = Number(body.limit ?? 10);
        const matches = [...entities.values()]
          .filter(e => q === "" || String(e["name"] ?? "").toLowerCase().includes(q) || String(e["description"] ?? "").toLowerCase().includes(q))
          .slice(0, limit);
        send({ entities: matches });
      } else if (path.endsWith("/feedback") && req.method === "PUT" && entities.has(id!)) {
        send({ id, updated: true }); // set_entity_feedback
      } else if (path.startsWith("/entities/") && entities.has(id!)) {
        if (req.method === "DELETE") {
          if (behavior.failDelete === id) { send({ error: "Injected delete failure" }, 400); return; }
          entities.delete(id!); sources.delete(id!); res.writeHead(204).end();
        } else send(entities.get(id!));
      } else if (path.startsWith("/entities/")) send({ error: "Not found" }, 404);
      else if (path === "/reasoning/steps" && req.method === "GET") {
        send({ steps: [] }); // list_steps: no fixture program records reasoning steps
      } else if (path === "/query" && req.method === "POST") {
        const params = body.params as { ids?: string[]; id?: string; conversation?: string };
        let rows: Row[];
        if (body.cypher === CLEANUP_QUERIES.derived || body.cypher === CLEANUP_QUERIES.graph) {
          rows = [...sources.entries()].filter(([entityId, ids]) => entities.has(entityId) && ids.some(source => params.ids!.includes(source))).flatMap<Row>(([entityId, ids]) =>
            body.cypher === CLEANUP_QUERIES.derived ? [{ id: entityId, created: entities.get(entityId)!.createdAt }] :
              ids.filter(source => params.ids!.includes(source)).map(source => ({ message_id: source, relationship: "EXTRACTED_FROM", entity_id: entityId })));
        } else if (body.cypher === CLEANUP_QUERIES.provenance) rows = entities.has(params.id!) ? [{ id: params.id, name: entities.get(params.id!)!.name, created: entities.get(params.id!)!.createdAt, sources: sources.get(params.id!) ?? [], source_count: new Set(sources.get(params.id!) ?? []).size }] : [];
        else if (body.cypher === CLEANUP_QUERIES.neighbors) rows = neighbors.get(params.id!) ?? ((sources.get(params.id!) ?? []).length ? sources.get(params.id!)!.map(id => ({ id, labels: ["Message"], relationship: "EXTRACTED_FROM" })) : [{ id: null, labels: null, relationship: null }]);
        else if (body.cypher === CLEANUP_QUERIES.residual) rows = behavior.residualIds.map(id => ({ id, labels: ["Message"] }));
        else { send({ error: "Unrecognized or unscoped query" }, 400); return; }
        send({ columns: rows.length ? Object.keys(rows[0]!) : [], rows, stats: { nodesCreated: 0, relationshipsCreated: 0 } });
      } else send({ error: `Unhandled lesson route: ${req.method} ${path}` }, 500);
    } catch (error) { res.writeHead(500).end(JSON.stringify({ error: String(error) })); }
  });
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address(); if (!address || typeof address === "string") throw new Error("No stub address");
  return { endpoint: `http://127.0.0.1:${address.port}/v1`, conversations, entities, sources, neighbors, steps, calls, requests, behavior,
    close: () => new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())) };
}
