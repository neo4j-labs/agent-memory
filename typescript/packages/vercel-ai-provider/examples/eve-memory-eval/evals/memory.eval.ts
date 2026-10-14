import { defineEval } from "eve/evals";
import { equals, includes } from "eve/evals/expect";
import type config from "./evals.config.js";

const until = async (check: () => boolean, ms = 10_000) => {
  for (const end = Date.now() + ms; Date.now() < end; await new Promise((r) => setTimeout(r, 100))) {
    if (check()) return true;
  }
  return check();
};

export default defineEval<typeof config>({
  description: "NAMS eve memory provider: remember, recall in a new session, search, capture.",
  timeoutMs: 180_000,
  async test(t) {
    const nams = t.context.nams;

    // Chat 1: the model saves a note.
    const first = await t.send("remember that I prefer aisle seats");
    t.succeeded();
    t.calledTool("nams__remember");
    t.check(first.message, includes("remembered"));

    // The chat was saved, under this user's id.
    const captured = await until(() =>
      nams.conversations.some((c) =>
        c.metadata.kind === "session" &&
        (nams.messages.get(c.id) ?? []).some((m) => m.content === "remember that I prefer aisle seats")));
    t.check(captured, equals(true));
    const scoped = nams.conversations.every((c) => c.userId.startsWith("memscope1_"));
    t.check(scoped, equals(true));
    t.log(`conversations: ${JSON.stringify(nams.conversations.map((c) => ({ userId: c.userId.slice(0, 18), metadata: c.metadata })))}`);

    // NAMS files what it extracts from a chat under the chat's userId. Stand in
    // for that: one entity for this user, one for someone else.
    const scopeKey = nams.conversations[0]!.userId;
    const at = new Date().toISOString();
    nams.entities.push(
      { id: "ent-1", name: "Aisle seat", type: "PREFERENCE", description: "Seat this user books", userId: scopeKey, createdAt: at },
      { id: "ent-2", name: "Window seat", type: "PREFERENCE", description: "Another user's seat", userId: "memscope1_someone-else", createdAt: at },
    );

    // Chat 2: the model sees the note and the user's own entities before it answers.
    const second = await t.send("what do you know about me?");
    t.check(second.message, includes("recalled:"));
    t.check(second.message, includes("Prefers aisle seats"));
    t.check(second.message, includes("From this user's knowledge graph"));
    t.check(second.message, includes("Aisle seat — Seat this user books"));
    const leaked = (second.message ?? "").includes("Window seat");
    t.check(leaked, equals(false));
    t.log(`recall reply: ${second.message}`);

    // Every entity search was scoped to this user.
    t.check(nams.entitySearches.length > 0, equals(true));
    t.check(nams.entitySearches.every((b) => b.userId === scopeKey), equals(true));

    // Search finds the note and the earlier chat.
    const third = await second.session.send("search my memory");
    t.calledTool("nams__search");
    t.check(third.message, includes('"from":"saved note"'));
    t.check(third.message, includes('"from":"earlier session"'));
    t.check(third.message, includes('"entities":["Aisle seat — Seat this user books"]'));
    t.log(`search reply: ${third.message}`);

    // NAMS turned nothing away.
    t.check(nams.rejected, equals([]));
  },
});
