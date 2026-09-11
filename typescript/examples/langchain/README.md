# LangChain JS example

![Neo4j Labs](https://img.shields.io/badge/Neo4j-Labs-6366F1?logo=neo4j)
![Status: Experimental](https://img.shields.io/badge/Status-Experimental-F59E0B)
![Community Supported](https://img.shields.io/badge/Support-Community-6B7280)

A real [LangChain JS v1](https://js.langchain.com) agent (`createAgent`) whose
entire memory lives in a [Neo4j Agent Memory Service](https://memory.neo4jlabs.com)
graph — no checkpointer, no in-process message buffer.

> ⚠️ **Neo4j Labs Project**
>
> This project is part of Neo4j Labs and is actively maintained, but not
> officially supported. There are no SLAs or guarantees around backwards
> compatibility and deprecation. For questions and support, please use
> the [Neo4j Community Forum](https://community.neo4j.com).

## What it shows

- **`namsMemoryMiddleware`** (`src/nams-memory.ts`) — a LangChain v1 middleware.
  `wrapModelCall` prepends the conversation's three-tier context (reflections,
  observations, recent messages) plus recalled preferences to the system
  message; `afterAgent` writes the turn back through
  `Neo4jChatMessageHistory`. The second turn is invoked with **only** the new
  user message, so everything it knows about turn one came back out of the graph.
- **`createEntityLookupTool`** — `Neo4jEntityRetriever` exposed as an agent
  tool, so the model can search entities NAMS extracted from past conversations.
- **Extraction is asynchronous** — the script awaits
  `longTerm.waitForExtraction()` before querying entities, instead of racing a
  fixed delay.
- **Graph, not buffer** — the run ends by printing three-tier context counts, a
  preference recalled across conversations, and one `longTerm.expandGraph()` hop
  out of the entity the retriever found.

### The adapter bridge

`Neo4jChatMessageHistory` and `Neo4jEntityRetriever` are framework-free: they
speak plain `{ type, content }` and `{ pageContent, metadata }` objects rather
than `@langchain/core` classes. `toLangChainMessage`, `fromLangChainMessage` and
`toLangChainDocument` in `src/nams-memory.ts` are the whole bridge — three small
functions, no `as unknown as` casts anywhere. If the SDK later has these classes
extend `BaseChatMessageHistory` / `BaseRetriever`, the bridge disappears and the
rest of the example is unchanged.

## Prerequisites

- Node.js 22+ (Node 20 is EOL)
- A `MEMORY_API_KEY` from [memory.neo4jlabs.com](https://memory.neo4jlabs.com)
- An `OPENAI_API_KEY` (or swap in any LangChain chat model — see below)

## Run it

```bash
cp .env.example .env       # set MEMORY_API_KEY and OPENAI_API_KEY
npm install
npm start
```

Running without `MEMORY_API_KEY` fails immediately with a named error rather
than a transport 401.

## Expected output

```
conversation: 0f4c…

user> I'm evaluating graph databases for a recommendation engine. Neo4j is top of my list.
memory: injected 4 lines of graph context
tool search_memory_entities("graph database") -> 0 entities
memory: persisted human message (84 chars)
memory: persisted ai message (287 chars)
agent> Neo4j is a strong fit for relationship-heavy recommendations …

user> Which of the things I mentioned should I benchmark first?
memory: injected 8 lines of graph context
memory: persisted human message (57 chars)
memory: persisted ai message (301 chars)
agent> Start with Neo4j and your recommendation query patterns …

NAMS holds 4 messages for this conversation:
  [human] I'm evaluating graph databases for a recommendation engine. Neo4j is top
  [ai] Neo4j is a strong fit for relationship-heavy recommendations …
  [human] Which of the things I mentioned should I benchmark first?
  [ai] Start with Neo4j and your recommendation query patterns …

Waiting for background entity extraction ...
Retriever returned 3 entities from the graph:
  Neo4j (concept) id=4a1e…
  recommendation engine (concept) id=9c22…
  graph database (concept) id=1d07…

Three-tier context: 1 reflections, 2 observations, 4 recent messages
Preferences recalled across conversations: 1
  [database] Prefers graph databases for relationship-heavy workloads
One hop from Neo4j: nodes=4 edges=3
```

Exact wording varies with the model; the structure does not. Entity names come
out of the messages the agent just persisted — nothing is hand-seeded.

## Tests

```bash
npm test
```

Runs the whole script against a fake NAMS transport (`test/fake-nams.ts`) and
LangChain's `fakeModel()` — no API key, no network, no Neo4j. The fake only
returns entities from the second poll onward, so the test fails if
`waitForExtraction()` stops being awaited.

## Using a different model provider

```ts
import { ChatAnthropic } from "@langchain/anthropic";

await main({ model: new ChatAnthropic({ model: process.env.ANTHROPIC_MODEL }) });
```

`main()` accepts any `BaseChatModel`, so the memory wiring is provider-agnostic.

## Using this in your own app

Copy `src/nams-memory.ts`, then:

```ts
import { MemoryClient } from "@neo4j-labs/agent-memory";
import { Neo4jChatMessageHistory, Neo4jEntityRetriever }
  from "@neo4j-labs/agent-memory/integrations/langchain";
import { ChatOpenAI } from "@langchain/openai";
import { createAgent, HumanMessage } from "langchain";
import { createEntityLookupTool, namsMemoryMiddleware } from "./nams-memory.js";

const memory = new MemoryClient();                 // reads MEMORY_API_KEY
const conversationId = (await memory.shortTerm.createConversation({ userId })).id;
const history = new Neo4jChatMessageHistory(memory, conversationId);

const agent = createAgent({
  model: new ChatOpenAI({ model: process.env.OPENAI_MODEL ?? "gpt-5-mini" }),
  systemPrompt: "You are a concise assistant with a long memory.",
  tools: [createEntityLookupTool(new Neo4jEntityRetriever(memory, { topK: 5 }))],
  middleware: [namsMemoryMiddleware({ client: memory, conversationId, history })],
});

const result = await agent.invoke({ messages: [new HumanMessage(userInput)] });
```

Reuse the same `conversationId` across requests and processes: the graph holds
the history, so a restarted server picks the conversation up where it left off.

## Support

This is a Neo4j Labs project — community supported, no SLA. Ask questions on the
[Neo4j Community Forum](https://community.neo4j.com) or open an issue on
[GitHub](https://github.com/neo4j-labs/agent-memory/issues).

## See also

- [How-to: LangChain JS](https://neo4j.com/labs/agent-memory/how-to/typescript/langchain)
- [LangChain JS docs](https://js.langchain.com)

---

_Verified against @neo4j-labs/agent-memory 0.4.1 (in-tree), langchain 1.5.11,
@langchain/core 1.2.10, @langchain/openai 1.5.12, Node 22+ — 2026-09-10._
