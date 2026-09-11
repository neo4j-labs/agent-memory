# Mastra example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

A real [Mastra](https://mastra.ai) `Agent` (`@mastra/core` 1.66) whose
conversation history lives in a [Neo4j Agent Memory Service](https://memory.neo4jlabs.com)
graph instead of a Mastra store.

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

## What it shows

- **Two agent turns with no in-process buffer.** Each `agent.generate()` call is
  built from `memory.getMessages(threadId)` plus the new user message, so turn
  two knows about turn one only because the graph handed it back.
- **The thread is a real NAMS conversation.** `getThreadById` reads the title
  back out of conversation metadata, `shortTerm.getContext` returns three-tier
  context (reflections, observations, recent messages) over the very messages
  the agent just wrote, and `shortTerm.searchMessages` runs a semantic search
  scoped to the thread.
- **Cross-thread recall — the reason to choose NAMS here.** A preference written
  while planning the trip is resource-scoped, so a *second* thread for the same
  `resourceId` recalls it even though that thread's own history is empty. A
  thread-scoped store cannot do this.
- **Distinct thread ids per resource.** Both threads belong to one
  `resourceId`; `shortTerm.listConversations({ userId })` lists both.
- **Idempotent re-runs.** `CLEANUP=1` deletes both threads through
  `deleteThread` on the way out.

## What the adapter covers — and what it does not

`Neo4jMastraMemory` is a **thin thread-and-message-history adapter** in Mastra's
vocabulary (`resourceId`, thread, message). It is *not* a Mastra `Memory` and
*not* a Mastra storage adapter, so this example never passes it to
`new Agent({ memory })` or to `new Memory({ storage })`.

| Adapter method | SDK call it wraps |
|---|---|
| `createThread({ resourceId, title, metadata })` | `shortTerm.createConversation` (title folded into metadata) |
| `getThreadById(threadId)` | `shortTerm.getConversationMetadata` |
| `getMessages(threadId, { limit })` | `shortTerm.getConversation` |
| `saveMessage({ threadId, role, content })` | `shortTerm.addMessage` |
| `deleteThread(threadId)` | `shortTerm.deleteConversation` (falls back to `clearSession`) |

A custom backend Mastra itself drives is a **storage adapter**: a
`MastraCompositeStore` whose `memory` domain extends `MemoryStorage` from
`@mastra/core/storage` — nine abstract methods over `MastraDBMessage`. Two of
them (`listMessagesById`, `updateMessages`) have no NAMS equivalent today, so a
half-implemented domain would fail inside Mastra's own recall paths rather than
at wiring time. **The storage-adapter port is a follow-up, not shipped.**
`test/mastra.test.ts` pins that gap against the installed `@mastra/core` and
`@mastra/memory` typings, so a Mastra release that changes the surface fails the
test instead of quietly making this table wrong.

Everything written through the adapter is a real NAMS conversation, so entity
extraction, three-tier context and graph queries apply to it — that is what acts
2 and 3 of the script demonstrate.

## Prerequisites

- Node.js 22+ (Node 20 is EOL)
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com)
- An `OPENAI_API_KEY`, or `MASTRA_MODEL` pointing at another provider

## Run it

```bash
cp .env.example .env       # set MEMORY_API_KEY and OPENAI_API_KEY
npm install
npm start                  # add CLEANUP=1 to delete both threads afterwards
```

`npm start` loads `.env` itself (`tsx --env-file-if-exists=.env`). Running
without `MEMORY_API_KEY` fails immediately with a named error rather than a
transport 401.

## Expected output

```
thread 9f3c… for resource mastra-demo-user

user> I'm planning a 7-day trip to Lisbon.
memory: replayed 0 stored message(s) into the turn
agent> Lisbon is a great pick — start with flights and an Alfama food tour …

user> Given what I told you, what should I book first?
memory: replayed 2 stored message(s) into the turn
agent> Book the Alfama food tour and a Belém history walk first …

NAMS holds 4 messages for this thread:
  [user] I'm planning a 7-day trip to Lisbon.
  [assistant] Lisbon is a great pick — start with flights and an Alfama food tour
  [user] Given what I told you, what should I book first?
  [assistant] Book the Alfama food tour and a Belém history walk first …
getThreadById -> title "Lisbon trip planning"
Three-tier context: 0 reflections, 1 observations, 4 recent messages
Semantic search inside the thread matched 3 message(s)

second thread 4a71… (same resource, distinct id)
Preferences recalled in the new thread: 1
  [travel] Prefers food and history trips

user> Remind me what kind of trip I said I like.
agent> You said you like food and history trips …
NAMS lists 2 thread(s) for mastra-demo-user

Re-run with CLEANUP=1 to delete both threads on the way out.
```

Exact wording varies with the model; the structure does not. Reflection and
observation counts grow as NAMS processes the conversation in the background.

## Tests

```bash
npm test
```

Runs the whole script against a fake NAMS transport (`test/fake-nams.ts`) and
Mastra's own stub model (`createMockModel`) — no API key, no network, no Neo4j.
The stub records every prompt Mastra sends it, so the test can assert that turn
two's prompt contained turn one's text: if the history stopped coming out of the
graph, that assertion fails.

## Using this in your own app

Copy `src/nams-threads.ts` — one type and two small functions, the whole bridge
between NAMS history and Mastra message input — then:

```ts
import { Agent } from "@mastra/core/agent";
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { Neo4jMastraMemory } from "@neo4j-labs/agent-memory/integrations/mastra";
import { toMastraMessages } from "./nams-threads.js";

const client = new MemoryClient();            // reads MEMORY_API_KEY
const memory = new Neo4jMastraMemory(client); // thread + message history

const agent = new Agent({
  id: "scout",
  name: "scout",
  instructions: "You help users plan trips.",
  model: "openai/gpt-5-mini",
  // NOT `memory` — see "What the adapter covers" above.
});

const thread = await memory.createThread({ resourceId: userId, title: "Trip" });

// Per request: replay history out of the graph, then write the turn back.
const history = await memory.getMessages(thread.id);
const result = await agent.generate([
  ...toMastraMessages(history),
  { role: "user", content: userInput },
]);
await memory.saveMessage({ threadId: thread.id, role: "user", content: userInput });
await memory.saveMessage({ threadId: thread.id, role: "assistant", content: result.text });
```

Reuse the same thread id across requests and processes: the graph holds the
history, so a restarted server picks the thread up where it left off.

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [How-to: Mastra](https://neo4j.com/labs/agent-memory/how-to/typescript/mastra)
- [Mastra docs](https://mastra.ai)

---

_Verified against @neo4j-labs/agent-memory 0.4.1 (in-tree), @mastra/core 1.66.0,
@mastra/memory 1.29.0, Node 22+ — 2026-09-10._
