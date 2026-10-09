import { createServer, type IncomingMessage } from "node:http";
import type { AddressInfo } from "node:net";

interface Conversation { id: string; userId: string; metadata: Record<string, unknown>; createdAt: string; updatedAt: string }
interface Message { id: string; role: string; content: string; createdAt: string }

async function readJson(req: IncomingMessage): Promise<any> {
  let raw = "";
  for await (const chunk of req) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

/** Body fields each endpoint accepts, from the NAMS API spec. */
const ACCEPTS: Record<string, string[]> = {
  conversations: ["userId", "metadata"],
  messages: ["role", "content", "metadata"],
  bulk: ["messages"],
  search: ["query", "limit"],
};

/**
 * A small stand-in for the NAMS server, kept in memory.
 * Like the real one, it filters chats by `userId` and rejects unknown fields.
 */
export async function startFakeNams() {
  const conversations: Conversation[] = [];
  const messages = new Map<string, Message[]>();
  const requests: string[] = [];
  /** Requests turned away for an unknown field. Should stay empty. */
  const rejected: string[] = [];
  let ids = 0;
  let clock = Date.UTC(2026, 9, 1);
  const now = () => new Date((clock += 1000)).toISOString();

  const add = (id: string, role: string, content: string): Message => {
    const message = { id: `msg-${++ids}`, role, content, createdAt: now() };
    messages.set(id, [...(messages.get(id) ?? []), message]);
    const conversation = conversations.find((c) => c.id === id);
    if (conversation) conversation.updatedAt = message.createdAt;
    return message;
  };

  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const body = await readJson(req);
    requests.push(`${req.method} ${url.pathname}${url.search}`);
    const send = (status: number, payload: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    };
    const [v1, collection, id, sub, sub2] = url.pathname.split("/").filter(Boolean);
    if (v1 !== "v1" || collection !== "conversations") return send(404, { error: "not found" });

    // Like the real server: an unknown field is an error, not ignored.
    const check = (fields: string[], allowed: string[] = []) => fields.find((f) => !allowed.includes(f));
    const unknown = req.method !== "POST"
      ? undefined
      : sub2 === "bulk"
        ? check(Object.keys(body), ACCEPTS.bulk) ??
          check(body.messages.flatMap((m: object) => Object.keys(m)), ACCEPTS.messages)
        : check(Object.keys(body), ACCEPTS[sub ?? "conversations"]);
    if (unknown) {
      rejected.push(`${req.method} ${url.pathname}: unknown field: ${unknown}`);
      return send(400, { error: `unknown field: ${unknown}` });
    }

    if (!id && req.method === "GET") {
      const userId = url.searchParams.get("userId");
      const listed = [...conversations].reverse();
      return send(200, { conversations: userId ? listed.filter((c) => c.userId === userId) : listed });
    }
    if (!id && req.method === "POST") {
      const at = now();
      const conversation = { id: `conv-${++ids}`, userId: body.userId ?? "", metadata: body.metadata ?? {}, createdAt: at, updatedAt: at };
      conversations.push(conversation);
      return send(201, { ...conversation, workspaceId: "ws-e2e" });
    }
    if (!conversations.some((c) => c.id === id)) return send(404, { error: "no such conversation" });
    if (sub === "messages" && !sub2 && req.method === "GET") {
      const limit = Number(url.searchParams.get("limit") ?? 50);
      return send(200, { messages: (messages.get(id) ?? []).slice(-limit).reverse() });
    }
    if (sub === "messages" && !sub2 && req.method === "POST") return send(201, add(id, body.role, body.content));
    if (sub === "messages" && sub2 === "bulk") {
      return send(201, { messages: body.messages.map((m: Message) => add(id, m.role, m.content)) });
    }
    if (sub === "search") {
      const found = (messages.get(id) ?? []).filter((m) => m.content.includes(body.query)).slice(0, body.limit ?? 10);
      return send(200, { messages: found, searchType: "text" });
    }
    if (sub === "context") return send(200, { reflections: [], observations: [], recentMessages: [] });
    return send(404, { error: "not found" });
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    endpoint: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`,
    conversations,
    messages,
    requests,
    rejected,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}
